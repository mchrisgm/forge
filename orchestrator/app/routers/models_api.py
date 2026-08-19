import asyncio
import json
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import select

from ..config import get_settings
from ..db import read_session, write_session
from ..models import (
    EngineKind,
    ModelEntry,
    ModelStatus,
    Quant,
    Suggestion,
    ToolCallFormat,
)
from ..services import downloader
from ..services.engine_manager import engine_manager
from ..services.events import bus, sse_stream
from ..services.registry import scan

router = APIRouter(prefix="/models")


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
async def add_model(body: ManualAddBody) -> dict:
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
        await downloader.start_download(entry)
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
async def approve_suggestion(suggestion_id: int) -> dict:
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
    await downloader.start_download(entry)
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


@router.get("/downloads/stream")
async def downloads_stream() -> StreamingResponse:
    async def generate():
        async with bus.subscribe() as queue:
            async for frame in sse_stream(queue):
                yield frame

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/{model_id}/download")
async def download_model(model_id: int) -> dict:
    with read_session() as db:
        entry = db.get(ModelEntry, model_id)
    if entry is None:
        raise HTTPException(404, "model not found")
    if downloader.is_downloading(model_id):
        raise HTTPException(409, "already downloading")
    await downloader.start_download(entry)
    return {"ok": True}


@router.delete("/{model_id}")
async def delete_model(model_id: int) -> dict:
    if downloader.is_downloading(model_id):
        raise HTTPException(409, "download in progress")
    lease = engine_manager.lease
    if lease and lease.model_id == model_id and lease.state != "failed":
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
