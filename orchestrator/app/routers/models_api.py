import asyncio
import json
import re
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import select

from ..auth import current_user
from ..config import get_settings
from ..db import read_session, write_session
from ..models import (
    EngineKind,
    ModelEntry,
    ModelStatus,
    Quant,
    Suggestion,
    ToolCallFormat,
    User,
)
from ..services import downloader, oauth_flows
from ..services.engine_manager import engine_manager
from ..services.events import bus, sse_stream
from ..services.registry import (
    is_diffusers_repo,
    resolve_text_candidate,
    scan,
    search_hub,
    snapshot_size_gb,
)

router = APIRouter(prefix="/models")


def _hf_token(user: User) -> str | None:
    """The caller's Hugging Face sign-in (or pasted token), so searches and
    downloads of gated/private repos run with THEIR access; global HF_TOKEN
    stays the fallback inside registry/downloader."""
    return oauth_flows.stored_token(user.id, "hugging-face") or None


class ManualAddBody(BaseModel):
    hf_repo: str
    display_name: str = ""
    engine: EngineKind = EngineKind.llamacpp
    quant: Quant | None = None
    gguf_filename: str = ""  # required for llamacpp
    params_b: float = 0.0
    ctx_max: int = 16384
    n_layers: int = 0
    is_moe: bool = False
    tool_call_format: ToolCallFormat = ToolCallFormat.none
    auto_download: bool = True


DEFAULT_QUANT = {
    EngineKind.llamacpp: Quant.gguf_q4_k_m,
    EngineKind.vllm: Quant.awq,
    EngineKind.airllm: Quant.fp16_airllm,
}


@router.get("")
def list_models() -> list[dict]:
    with read_session() as db:
        rows = db.exec(select(ModelEntry)).all()
    rows = sorted(rows, key=lambda r: r.added_at, reverse=True)
    return [r.model_dump(mode="json") for r in rows]


@router.post("")
async def add_model(
    body: ManualAddBody, user: User = Depends(current_user)
) -> dict:
    if body.engine == EngineKind.llamacpp and not body.gguf_filename.endswith(".gguf"):
        raise HTTPException(400, "llamacpp models need gguf_filename (*.gguf)")
    entry = ModelEntry(
        hf_repo=body.hf_repo,
        display_name=body.display_name or body.hf_repo.split("/")[-1],
        engine=body.engine,
        quant=body.quant or DEFAULT_QUANT[body.engine],
        file_path=body.gguf_filename if body.engine == EngineKind.llamacpp else "",
        params_b=body.params_b,
        ctx_max=body.ctx_max,
        n_layers=body.n_layers,
        is_moe=body.is_moe,
        tool_call_format=body.tool_call_format,
        status=ModelStatus.approved,
    )
    with write_session() as db:
        db.add(entry)
        db.flush()
        db.refresh(entry)
        entry_id = entry.id
    with read_session() as db:
        entry = db.get(ModelEntry, entry_id)
    if body.auto_download:
        await downloader.start_download(entry, _hf_token(user))
    return entry.model_dump(mode="json")


@router.get("/suggestions")
def list_suggestions() -> list[dict]:
    with read_session() as db:
        rows = db.exec(
            select(Suggestion).where(Suggestion.dismissed == False)  # noqa: E712
        ).all()
    result = []
    for row in rows:
        item = row.model_dump(mode="json")
        try:
            item["reason"] = json.loads(row.reason)
        except json.JSONDecodeError:
            item["reason"] = {}
        result.append(item)
    result.sort(key=lambda r: r["reason"].get("score", 0), reverse=True)
    return result


@router.post("/suggestions/{suggestion_id}/approve")
async def approve_suggestion(
    suggestion_id: int, user: User = Depends(current_user)
) -> dict:
    with read_session() as db:
        suggestion = db.get(Suggestion, suggestion_id)
    if suggestion is None:
        raise HTTPException(404, "suggestion not found")
    reason = json.loads(suggestion.reason or "{}")
    lane = reason.get("lane") or "llamacpp-offload"

    if lane == "vllm":
        engine, quant = EngineKind.vllm, Quant.awq
        hf_repo, file_path = suggestion.hf_repo, ""
    elif lane == "airllm":
        engine, quant = EngineKind.airllm, Quant.fp16_airllm
        hf_repo, file_path = suggestion.hf_repo, ""
    else:
        engine, quant = EngineKind.llamacpp, Quant.gguf_q4_k_m
        hf_repo = reason.get("gguf_repo") or suggestion.hf_repo
        file_path = reason.get("gguf_file") or ""
        if not file_path:
            raise HTTPException(409, "no GGUF artifact recorded for this suggestion")

    entry = ModelEntry(
        hf_repo=hf_repo,
        display_name=suggestion.hf_repo.split("/")[-1],
        engine=engine,
        quant=quant,
        file_path=file_path,
        params_b=float(reason.get("params_b") or 0),
        is_moe=bool(reason.get("is_moe")),
        score=float(reason.get("score") or 0),
        status=ModelStatus.approved,
        tool_call_format=ToolCallFormat.hermes,  # sensible default; edit per model
    )
    with write_session() as db:
        db.add(entry)
        db.flush()
        db.refresh(entry)
        entry_id = entry.id
        sug = db.get(Suggestion, suggestion_id)
        if sug:
            sug.dismissed = True
            db.add(sug)
    with read_session() as db:
        entry = db.get(ModelEntry, entry_id)
    await downloader.start_download(entry, _hf_token(user))
    bus.publish("suggestion.approved", {"suggestion_id": suggestion_id, "model_id": entry_id})
    return entry.model_dump(mode="json")


@router.post("/suggestions/{suggestion_id}/dismiss")
def dismiss_suggestion(suggestion_id: int) -> dict:
    with write_session() as db:
        suggestion = db.get(Suggestion, suggestion_id)
        if suggestion is None:
            raise HTTPException(404, "suggestion not found")
        suggestion.dismissed = True
        db.add(suggestion)
    return {"ok": True}


@router.post("/registry/scan")
async def trigger_scan() -> dict:
    return await asyncio.to_thread(scan)


# ── Hub search: find a SPECIFIC model by name and add it in one click ───────


@router.get("/search")
async def search_models(
    q: str,
    kind: str = "text",
    limit: int = 20,
    user: User = Depends(current_user),
) -> list[dict]:
    if kind not in ("text", "image"):
        raise HTTPException(400, "kind must be 'text' or 'image'")
    if not q.strip():
        raise HTTPException(400, "q required")
    try:
        return await asyncio.to_thread(
            search_hub, q.strip(), kind, limit, _hf_token(user)
        )
    except Exception as exc:
        raise HTTPException(502, f"Hugging Face search failed: {exc}") from exc


class SearchAddBody(BaseModel):
    hf_repo: str
    kind: str = "text"  # text | image
    auto_download: bool = True


LANE_NOTE = {
    "vllm": "Added from Hub search — AWQ build assigned to the vLLM fast lane.",
    "llamacpp-full-gpu": "Added from Hub search — GGUF fits fully in VRAM.",
    "llamacpp-offload": "Added from Hub search — GGUF runs with CPU offload.",
    "airllm": "Added from Hub search — AirLLM slow lane (chat-only).",
}


@router.post("/search/add")
async def add_from_search(
    body: SearchAddBody, user: User = Depends(current_user)
) -> dict:
    """Resolve a searched repo into a runnable catalog entry: text models get
    artifact discovery + lane assignment (GGUF/AWQ hunt, same as suggestions);
    image models become imagegen-lane snapshot entries."""
    hf_repo = body.hf_repo.strip()
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", hf_repo):
        raise HTTPException(400, "hf_repo must look like owner/name")
    with read_session() as db:
        exists = db.exec(
            select(ModelEntry).where(ModelEntry.hf_repo == hf_repo)
        ).first()
    if exists:
        raise HTTPException(409, f"{hf_repo} is already in the catalog")

    if body.kind == "image":
        # Only diffusers-format repos are loadable by the imagegen server;
        # text-to-image covers plenty of raw-checkpoint/LoRA repos that would
        # download tens of GB and then fail at load time.
        if not await asyncio.to_thread(is_diffusers_repo, hf_repo, _hf_token(user)):
            raise HTTPException(
                409,
                f"{hf_repo} is not a diffusers-format repo (no model_index.json) "
                "— the imagegen lane cannot load raw checkpoints or LoRAs",
            )
        size_gb = await asyncio.to_thread(snapshot_size_gb, hf_repo, _hf_token(user))
        entry = ModelEntry(
            hf_repo=hf_repo,
            display_name=hf_repo.split("/")[-1],
            engine=EngineKind.imagegen,
            quant=Quant.fp16_diffusers,
            file_path="",
            size_gb=size_gb,
            tool_call_format=ToolCallFormat.none,
            status=ModelStatus.approved,
            note="Added from Hub search — text-to-image (diffusers snapshot).",
        )
    elif body.kind == "text":
        try:
            resolved = await asyncio.to_thread(
                resolve_text_candidate, hf_repo, _hf_token(user)
            )
        except Exception as exc:
            raise HTTPException(502, f"could not resolve {hf_repo}: {exc}") from exc
        lane = resolved["lane"]
        if lane is None:
            raise HTTPException(
                409,
                f"{hf_repo} does not fit this hardware in any lane (no usable "
                "GGUF/AWQ artifact within the VRAM+RAM budgets)",
            )
        if lane == "vllm":
            engine, quant = EngineKind.vllm, Quant.awq
            repo, file_path = hf_repo, ""
        elif lane == "airllm":
            engine, quant = EngineKind.airllm, Quant.fp16_airllm
            repo, file_path = hf_repo, ""
        else:
            engine, quant = EngineKind.llamacpp, Quant.gguf_q4_k_m
            repo = resolved["gguf_repo"] or hf_repo
            file_path = resolved["gguf_file"] or ""
            if not file_path:
                raise HTTPException(409, f"no single-file GGUF found for {hf_repo}")
        # The llamacpp lane stores the RESOLVED quantizer repo in hf_repo, so
        # the searched-repo dedupe above misses re-adds — check the resolved
        # artifact too (file_path basenames survive the downloader's
        # gguf/<slug>/ rewrite).
        if repo != hf_repo or file_path:
            with read_session() as db:
                rows = db.exec(
                    select(ModelEntry).where(ModelEntry.hf_repo == repo)
                ).all()
            for row in rows:
                if row.engine != engine:
                    continue
                if engine != EngineKind.llamacpp or (
                    Path(row.file_path).name == Path(file_path).name
                ):
                    raise HTTPException(
                        409,
                        f"{hf_repo} resolves to {repo} "
                        f"{Path(file_path).name or ''}".strip()
                        + ", which is already in the catalog",
                    )
        entry = ModelEntry(
            hf_repo=repo,
            display_name=hf_repo.split("/")[-1],
            engine=engine,
            quant=quant,
            file_path=file_path,
            params_b=float(resolved["params_b"] or 0),
            size_gb=float(resolved["gguf_size_gb"] or 0),
            is_moe=bool(resolved["is_moe"]),
            tool_call_format=ToolCallFormat.hermes,
            status=ModelStatus.approved,
            note=LANE_NOTE[lane],
        )
    else:
        raise HTTPException(400, "kind must be 'text' or 'image'")

    with write_session() as db:
        db.add(entry)
        db.flush()
        entry_id = entry.id
    with read_session() as db:
        entry = db.get(ModelEntry, entry_id)
    if body.auto_download:
        await downloader.start_download(entry, _hf_token(user))
    bus.publish("model.added", {"model_id": entry_id, "hf_repo": entry.hf_repo})
    return entry.model_dump(mode="json")


@router.get("/downloads/stream")
async def downloads_stream() -> StreamingResponse:
    async def generate():
        async with bus.subscribe() as queue:
            async for frame in sse_stream(queue):
                yield frame

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.get("/{model_id}/thinking/{level}")
def thinking_directives(model_id: int, level: str) -> dict:
    """Per-family reasoning directives for a thinking level — the PWA applies
    these to session-chat prompts (which go through the OpenCode proxy)."""
    from ..models import ThinkingLevel
    from ..services.thinking import directives_for, model_thinking_family

    try:
        thinking_level = ThinkingLevel(level)
    except ValueError as exc:
        valid = ", ".join(entry.value for entry in ThinkingLevel)
        raise HTTPException(400, f"level must be one of: {valid}") from exc
    with read_session() as db:
        entry = db.get(ModelEntry, model_id)
    if entry is None:
        raise HTTPException(404, "model not found")
    return {
        "family": model_thinking_family(entry),
        "level": thinking_level.value,
        **directives_for(entry, thinking_level).as_dict(),
    }


@router.post("/{model_id}/download")
async def download_model(
    model_id: int, user: User = Depends(current_user)
) -> dict:
    with read_session() as db:
        entry = db.get(ModelEntry, model_id)
    if entry is None:
        raise HTTPException(404, "model not found")
    if downloader.is_downloading(model_id):
        raise HTTPException(409, "already downloading")
    await downloader.start_download(entry, _hf_token(user))
    return {"ok": True}


@router.delete("/{model_id}")
async def delete_model(model_id: int) -> dict:
    if downloader.is_downloading(model_id):
        raise HTTPException(409, "download in progress")
    if any(
        lease.model_id == model_id for lease in engine_manager.active_leases()
    ):
        raise HTTPException(409, "model is loaded — unload the engine first")
    with read_session() as db:
        entry = db.get(ModelEntry, model_id)
    if entry is None:
        raise HTTPException(404, "model not found")

    settings = get_settings()
    if entry.file_path:
        # gguf files live under gguf/<repo>/, snapshots under hf/<repo>/ — remove the dir
        target = (Path(settings.models_dir) / entry.file_path).resolve()
        models_root = Path(settings.models_dir).resolve()
        if models_root in target.parents:
            victim = target if target.is_dir() else target.parent
            if victim != models_root:
                await asyncio.to_thread(shutil.rmtree, victim, True)

    with write_session() as db:
        entry = db.get(ModelEntry, model_id)
        if entry:
            db.delete(entry)
    bus.publish("model.deleted", {"model_id": model_id})
    return {"ok": True}
