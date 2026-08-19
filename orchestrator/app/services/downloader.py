"""Model weight downloads via huggingface_hub with SSE progress (PLAN §6.4).

The blocking hf download runs in a worker thread; a poller task watches bytes
on disk vs the expected total and publishes `download.progress` events the UI
streams from /api/models/downloads/stream.
"""

import asyncio
import logging
import re
from pathlib import Path

from huggingface_hub import HfApi, hf_hub_download, snapshot_download

from ..config import get_settings
from ..db import write_session
from ..models import EngineKind, ModelEntry, ModelStatus
from .events import bus

log = logging.getLogger(__name__)

_active: dict[int, asyncio.Task] = {}


def repo_slug(hf_repo: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "__", hf_repo)


def _dir_size_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _expected_bytes(
    hf_repo: str,
    filename: str | None,
    token: str | None,
    ignore: list[str] | None = None,
) -> int:
    from fnmatch import fnmatch

    try:
        info = HfApi(token=token).model_info(hf_repo, files_metadata=True)
        siblings = info.siblings or []
        if filename:
            for s in siblings:
                if s.rfilename == filename:
                    return s.size or 0
            return 0
        return sum(
            s.size or 0
            for s in siblings
            if s.size and not any(fnmatch(s.rfilename, pat) for pat in ignore or [])
        )
    except Exception as exc:
        log.warning("could not fetch expected size for %s: %s", hf_repo, exc)
        return 0


def _diffusers_ignore_patterns(hf_repo: str, token: str | None) -> list[str]:
    """Snapshot pruning for the imagegen lane. Diffusers repos routinely ship
    the same weights three ways — per-component fp32 + fp16 variants PLUS
    root-level single-file checkpoints (sdxl-turbo: ~34 GB of duplicates next
    to the ~7 GB fp16 set the server actually loads). Skip formats we never
    read, and when the repo is diffusers-format (model_index.json) skip the
    root checkpoints and fp32 twins of shipped fp16 variants."""
    patterns = ["*.ckpt", "*.onnx", "*.onnx_data", "*.msgpack", "*.h5", "*.tflite"]
    try:
        files = set(HfApi(token=token).list_repo_files(hf_repo))
    except Exception as exc:
        log.warning("could not list %s for snapshot pruning: %s", hf_repo, exc)
        return patterns
    if "model_index.json" not in files:
        return patterns
    patterns += [
        f for f in files if "/" not in f and f.endswith((".safetensors", ".bin"))
    ]
    for f in files:
        if f.endswith(".fp16.safetensors"):
            fp32_twin = f.replace(".fp16.safetensors", ".safetensors")
            if fp32_twin in files:
                patterns.append(fp32_twin)
    if any("/" in f and f.endswith(".safetensors") for f in files):
        patterns.append("*.bin")  # legacy twins of shipped safetensors
    return patterns


def _set_status(model_id: int, status: ModelStatus, **fields) -> None:
    with write_session() as db:
        entry = db.get(ModelEntry, model_id)
        if entry is None:
            return
        entry.status = status
        for key, value in fields.items():
            setattr(entry, key, value)
        db.add(entry)


def is_downloading(model_id: int) -> bool:
    task = _active.get(model_id)
    return bool(task and not task.done())


async def start_download(model: ModelEntry) -> None:
    if model.id is None or is_downloading(model.id):
        return
    _active[model.id] = asyncio.create_task(_download(model))


async def _download(model: ModelEntry) -> None:
    settings = get_settings()
    token = settings.hf_token or None
    models_dir = Path(settings.models_dir)
    model_id = model.id or 0

    if model.engine == EngineKind.llamacpp:
        # file_path is the repo-relative GGUF path (subfolders allowed). After
        # a prior download it may already carry our gguf/<slug>/ prefix —
        # strip it so re-downloads stay idempotent.
        prefix = f"gguf/{repo_slug(model.hf_repo)}/"
        filename = model.file_path or ""
        if filename.startswith(prefix):
            filename = filename[len(prefix):]
        if not filename.endswith(".gguf"):
            _set_status(model_id, ModelStatus.failed, note="no GGUF filename set")
            bus.publish("download.failed", {"model_id": model_id, "error": "no GGUF filename"})
            return
        rel_dir = Path("gguf") / repo_slug(model.hf_repo)
        rel_path = rel_dir / filename  # hf_hub_download preserves subdirs under local_dir
        expected_filename: str | None = filename
    else:
        rel_dir = Path("hf") / repo_slug(model.hf_repo)
        rel_path = rel_dir
        expected_filename = None

    local_dir = models_dir / rel_dir
    local_dir.mkdir(parents=True, exist_ok=True)
    _set_status(model_id, ModelStatus.downloading)
    bus.publish("download.started", {"model_id": model_id, "hf_repo": model.hf_repo})

    ignore: list[str] = []
    if model.engine == EngineKind.imagegen:
        ignore = await asyncio.to_thread(
            _diffusers_ignore_patterns, model.hf_repo, token
        )
    expected = await asyncio.to_thread(
        _expected_bytes, model.hf_repo, expected_filename, token, ignore
    )

    def blocking_download() -> None:
        if expected_filename:
            hf_hub_download(
                repo_id=model.hf_repo,
                filename=expected_filename,
                local_dir=str(local_dir),
                token=token,
            )
        else:
            snapshot_download(
                repo_id=model.hf_repo,
                local_dir=str(local_dir),
                token=token,
                ignore_patterns=ignore or None,
            )

    download_thread = asyncio.create_task(asyncio.to_thread(blocking_download))
    try:
        while not download_thread.done():
            done_bytes = await asyncio.to_thread(_dir_size_bytes, local_dir)
            bus.publish(
                "download.progress",
                {
                    "model_id": model_id,
                    "downloaded_gb": round(done_bytes / 1024**3, 2),
                    "total_gb": round(expected / 1024**3, 2) if expected else None,
                    "pct": round(100 * done_bytes / expected, 1) if expected else None,
                },
            )
            await asyncio.sleep(1.5)
        await download_thread  # re-raise download errors
    except Exception as exc:
        log.exception("download failed for %s", model.hf_repo)
        _set_status(model_id, ModelStatus.failed, note=f"download failed: {exc}")
        bus.publish("download.failed", {"model_id": model_id, "error": str(exc)})
        return
    finally:
        _active.pop(model_id, None)

    size_gb = round(await asyncio.to_thread(_dir_size_bytes, local_dir) / 1024**3, 2)
    _set_status(model_id, ModelStatus.ready, file_path=str(rel_path), size_gb=size_gb)
    bus.publish("download.done", {"model_id": model_id, "size_gb": size_gb})
