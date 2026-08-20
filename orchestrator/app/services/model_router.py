"""Auto model routing: a tiny resident LLM picks the right model per prompt.

When a conversation's model is set to "auto", a small always-on router model
(chosen on the Settings page — TinyLlama/Qwen-0.6B class, must be a READY
llama.cpp model) reads the user's prompt and decides which downloaded model
should answer it; the orchestrator then makes sure that model is serving
(loading it onto a GPU if needed) before generation starts. The whole dance
streams as forge:"status" frames, so the chat narrates "choosing → routed →
loading → generating" instead of sitting silent.

Placement (the user's spec): with multiple GPUs the router lives on the
"worst" one (smallest VRAM); with a single GPU it runs alongside the big
model with NO GPU layers (--n-gpu-layers 0) — a 1B Q4 model classifies a
prompt on CPU in well under a second, and stealing VRAM from the main lane
would be a worse trade. The router container sits OUTSIDE the per-GPU lease
map (label forge.router, fixed name/port), so it never blocks a lane.

Every failure degrades softly: router not configured, container won't start,
or the reply unparseable → fall back to the largest ready model (or whatever
is already serving) with an honest reason in the status frame.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx
from sqlmodel import select

from ..config import get_settings
from ..db import get_setting, read_session
from ..models import EngineKind, ModelEntry, ModelStatus
from . import docker_util
from .engine_manager import (
    Lease,
    LeaseHeldError,
    engine_manager,
    opencode_model_id_for,
)

log = logging.getLogger(__name__)

ROUTER_LABEL = "forge.router"
ROUTER_CONTAINER = "forge-engine-router"
AUTO_SLUG = "auto"  # the conversation.model_slug sentinel

_HEALTH_TIMEOUT_S = 180.0  # tiny models load in seconds; leave slack for pulls
_CHOOSE_TIMEOUT_S = 20.0

# Module state: the router's lease-shaped handle (never in the lease map).
_router: dict[str, Any] = {"lease": None, "model_id": None}
_router_lock = asyncio.Lock()


def router_model_slug() -> str:
    """The configured router model slug ('' = auto routing disabled)."""
    return (get_setting("router_model_slug") or "").strip()


def router_model_entry() -> ModelEntry | None:
    """The configured router model — must be READY and on the llama.cpp lane
    (tiny GGUF is the only sane always-resident shape)."""
    slug = router_model_slug()
    if not slug:
        return None
    with read_session() as db:
        rows = db.exec(
            select(ModelEntry).where(
                ModelEntry.status == ModelStatus.ready,
                ModelEntry.engine == EngineKind.llamacpp,
            )
        ).all()
    for row in rows:
        if opencode_model_id_for(row) == slug:
            return row
    return None


def worst_gpu(gpus: list[dict]) -> int:
    """The GPU the router should live on when several exist: smallest VRAM;
    ties break to the HIGHEST index (keep GPU 0, the usual primary, clean)."""
    if not gpus:
        return 0
    return min(gpus, key=lambda g: (g.get("vram_total_gb", 0), -g.get("index", 0)))[
        "index"
    ]


def _gpu_stats() -> list[dict]:
    try:
        import pynvml

        pynvml.nvmlInit()
        try:
            out = []
            for index in range(pynvml.nvmlDeviceGetCount()):
                handle = pynvml.nvmlDeviceGetHandleByIndex(index)
                mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
                out.append(
                    {"index": index, "vram_total_gb": round(mem.total / 1024**3, 2)}
                )
            return out
        finally:
            pynvml.nvmlShutdown()
    except Exception:
        return []


def _router_command(model: ModelEntry, settings, gpu_layers: int) -> list[str]:
    ctx = 4096  # classification prompts are short; keep the footprint tiny
    return [
        "-m", f"/data/models/{model.file_path}",
        "--host", "0.0.0.0",
        "--port", str(settings.router_port),
        "-c", str(ctx),
        "--n-gpu-layers", str(gpu_layers),
        "--parallel", "1",
        "--jinja",
        "--alias", opencode_model_id_for(model),
    ]


def _router_base_url(settings) -> str:
    return f"http://{ROUTER_CONTAINER}:{settings.router_port}/v1"


def _spawn_router_blocking(model: ModelEntry) -> Any:
    import docker

    settings = get_settings()
    client = docker_util.client()
    # Replace any previous router container (old model, old image).
    try:
        old = client.containers.get(ROUTER_CONTAINER)
        docker_util.remove_container(old)
    except docker.errors.NotFound:
        pass

    gpu_count = engine_manager.gpu_count
    device_requests = None
    if gpu_count > 1:
        stats = _gpu_stats()
        gpu = worst_gpu(stats) if stats else gpu_count - 1
        gpu_layers = 999  # tiny model — fully offload onto the worst GPU
        device_requests = [
            docker.types.DeviceRequest(device_ids=[str(gpu)], capabilities=[["gpu"]])
        ]
        placement = f"gpu {gpu} (smallest VRAM)"
    else:
        # Single GPU: run beside the big model WITHOUT touching its VRAM.
        gpu_layers = 0
        placement = "cpu (sharing the box with the main lane)"
    log.info("starting router model %s on %s", model.display_name, placement)

    return client.containers.run(
        settings.llamacpp_image,
        command=_router_command(model, settings, gpu_layers),
        name=ROUTER_CONTAINER,
        labels={ROUTER_LABEL: "1", "forge.model_id": str(model.id or 0)},
        network=settings.docker_network,
        mounts=[
            docker.types.Mount(
                target="/data/models", source=settings.models_volume, type="volume"
            )
        ],
        detach=True,
        device_requests=device_requests,
        restart_policy={"Name": "no"},
    )


async def ensure_router() -> Lease | None:
    """The router model's lease-shaped handle, starting its container on
    first use (and restarting it when the configured model changed or the
    container died). None when unconfigured or the start failed — callers
    fall back to deterministic selection."""
    model = router_model_entry()
    if model is None:
        return None
    settings = get_settings()
    async with _router_lock:
        lease: Lease | None = _router["lease"]
        if lease is not None and _router["model_id"] == model.id:
            try:
                async with httpx.AsyncClient(timeout=3) as http:
                    resp = await http.get(f"{lease.base_url}/models")
                    if resp.status_code == 200:
                        return lease
            except httpx.HTTPError:
                pass  # died — respawn below
        try:
            await asyncio.to_thread(_spawn_router_blocking, model)
        except Exception as exc:
            log.warning("router container failed to start: %s", exc)
            _router["lease"] = None
            return None
        lease = Lease(
            model_id=model.id or 0,
            model_name=model.display_name,
            model_slug=opencode_model_id_for(model),
            engine=EngineKind.llamacpp,
            gpu_ids=[],
            state="ready",
            base_url=_router_base_url(settings),
        )
        deadline = time.monotonic() + _HEALTH_TIMEOUT_S
        async with httpx.AsyncClient(timeout=3) as http:
            while time.monotonic() < deadline:
                try:
                    resp = await http.get(f"{lease.base_url}/models")
                    if resp.status_code == 200:
                        _router["lease"] = lease
                        _router["model_id"] = model.id
                        return lease
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(2)
    log.warning("router model never became healthy")
    _router["lease"] = None
    return None


def ready_candidates() -> list[ModelEntry]:
    """Every downloaded text model auto mode can route to."""
    with read_session() as db:
        rows = db.exec(
            select(ModelEntry).where(ModelEntry.status == ModelStatus.ready)
        ).all()
    return [r for r in rows if r.engine != EngineKind.imagegen]


def _fallback(candidates: list[ModelEntry]) -> ModelEntry:
    # Prefer whatever is ALREADY serving (no load wait), else the largest.
    serving = {le.model_id for le in engine_manager.ready_text_leases()}
    live = [c for c in candidates if (c.id or 0) in serving]
    pool = live or candidates
    return max(pool, key=lambda c: c.params_b)


async def choose_model(prompt: str) -> tuple[ModelEntry, str]:
    """(model, reason) for this prompt. The tiny router model decides when it
    is configured and healthy; every failure path falls back deterministically
    with an honest reason."""
    candidates = ready_candidates()
    if not candidates:
        raise RuntimeError(
            "no downloaded model is ready — download one from the Models page"
        )
    if len(candidates) == 1:
        return candidates[0], "the only ready model"

    lease = await ensure_router()
    if lease is None:
        pick = _fallback(candidates)
        return pick, "router model unavailable — defaulted"

    menu = "\n".join(
        f"- {opencode_model_id_for(c)}: {c.display_name}, {c.params_b:g}B params"
        + (f" — {c.note[:80]}" if c.note else "")
        for c in candidates
    )
    body = {
        "model": lease.model_slug,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You route user requests to the best local model. Pick the "
                    "smallest model that can handle the request well: simple "
                    "chat/short questions -> small models; coding, long or "
                    "complex reasoning -> larger ones. Reply with ONLY the "
                    "chosen model id from the list, nothing else.\n" + menu
                ),
            },
            {"role": "user", "content": prompt[:2000]},
        ],
        "max_tokens": 24,
        "temperature": 0,
    }
    try:
        async with httpx.AsyncClient(timeout=_CHOOSE_TIMEOUT_S) as http:
            resp = await http.post(f"{lease.base_url}/chat/completions", json=body)
            resp.raise_for_status()
            reply = str(resp.json()["choices"][0]["message"]["content"])
    except Exception as exc:
        log.warning("router choose failed: %s", exc)
        pick = _fallback(candidates)
        return pick, "router call failed — defaulted"

    reply_lower = reply.lower()
    matches = [
        c for c in candidates if opencode_model_id_for(c).lower() in reply_lower
    ]
    if matches:
        # Longest slug wins ("qwen3-14b" must not lose to a "qwen3-1b" substring).
        pick = max(matches, key=lambda c: len(opencode_model_id_for(c)))
        return pick, f"picked by {lease.model_name}"
    pick = _fallback(candidates)
    return pick, "router reply unparseable — defaulted"


async def ensure_serving(model: ModelEntry, push_status) -> Lease:
    """A READY lease for `model`, loading it onto a GPU when necessary. When
    every GPU is held by OTHER models, fall back to whatever is serving
    rather than evicting someone's active model."""
    slug = opencode_model_id_for(model)
    for lease in engine_manager.ready_text_leases():
        if lease.model_slug == slug:
            return lease

    settings = get_settings()
    try:
        lease = await engine_manager.load(model)
    except LeaseHeldError:
        ready = engine_manager.ready_text_leases()
        if ready:
            push_status(
                f"every GPU is busy — answering with {ready[0].model_name} instead"
            )
            return ready[0]
        raise RuntimeError(
            "no GPU is free and nothing is serving — unload an engine first"
        ) from None

    push_status(
        f"loading {model.display_name} onto the GPU — this can take a while"
    )
    deadline = time.monotonic() + settings.engine_load_timeout_s
    while time.monotonic() < deadline:
        if lease.state == "ready":
            return lease
        if lease.state == "failed":
            raise RuntimeError(f"loading {model.display_name} failed: {lease.error}")
        await asyncio.sleep(2)
    raise RuntimeError(f"loading {model.display_name} timed out")
