import json

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..db import read_session
from ..models import ModelEntry, ModelStatus, ThinkingLevel
from ..services.engine_manager import LeaseHeldError, engine_manager
from ..services.openai_proxy import proxy_openai_request
from ..services.thinking import apply_to_openai_messages, directives_for

router = APIRouter(prefix="/engines")


class LoadBody(BaseModel):
    model_id: int
    force: bool = False
    gpu_index: int | None = None  # None = auto-pick a free GPU
    gpu_count: int = 1  # >1 = tensor-parallel (vLLM lane only)


@router.get("")
def engines_status() -> dict:
    return engine_manager.status()


@router.post("/load")
async def load(body: LoadBody) -> dict:
    with read_session() as db:
        model = db.get(ModelEntry, body.model_id)
    if model is None:
        raise HTTPException(404, "model not found")
    if model.status != ModelStatus.ready:
        raise HTTPException(409, f"model is not ready (status: {model.status.value})")
    try:
        lease = await engine_manager.load(
            model, force=body.force, gpu_index=body.gpu_index, gpu_count=body.gpu_count
        )
    except LeaseHeldError as exc:
        raise HTTPException(
            status_code=409,
            detail={"message": "all GPUs are leased", "holders": exc.holders},
        ) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"lease": lease.as_dict()}


@router.post("/unload")
async def unload(gpu_index: int | None = None) -> dict:
    await engine_manager.unload(gpu_index)
    return {"leases": [lease.as_dict() for lease in engine_manager.active_leases()]}


@router.post("/chat")
async def engine_chat(request: Request):
    """Direct chat with a served model — the only surface for the chat-only
    AirLLM lane (PLAN §6.2), works for all lanes. Body is an OpenAI
    chat-completions request; `model` (slug) picks the lease when several are
    serving; an optional `thinking` field maps to per-family reasoning
    directives before forwarding."""
    if not engine_manager.ready_leases():
        states = [lease.as_dict() for lease in engine_manager.active_leases()]
        raise HTTPException(
            409, {"message": "no engine is serving", "leases": states}
        )

    raw = await request.body()
    try:
        payload = json.loads(raw or b"{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "body must be JSON") from exc

    from .openai_router import resolve_lease

    lease = resolve_lease(payload.get("model"))

    thinking_raw = payload.pop("thinking", None)
    if thinking_raw:
        try:
            level = ThinkingLevel(thinking_raw)
        except ValueError as exc:
            valid = ", ".join(level.value for level in ThinkingLevel)
            raise HTTPException(400, f"thinking must be one of: {valid}") from exc
        with read_session() as db:
            model = db.get(ModelEntry, lease.model_id)
        if model is not None and level != ThinkingLevel.auto:
            payload["messages"] = apply_to_openai_messages(
                payload.get("messages", []), directives_for(model, level)
            )
    payload["model"] = lease.model_slug
    return await proxy_openai_request(
        lease.base_url, "chat/completions", json.dumps(payload).encode()
    )
