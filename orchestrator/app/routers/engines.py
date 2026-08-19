import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from ..db import read_session
from ..models import ModelEntry, ModelStatus
from ..services.engine_manager import LeaseHeldError, engine_manager

router = APIRouter(prefix="/engines")

# AirLLM can take minutes-to-hours per reply (PLAN §6.2) — no read timeout.
_CHAT_TIMEOUT = httpx.Timeout(connect=10, read=None, write=30, pool=10)


class LoadBody(BaseModel):
    model_id: int
    force: bool = False


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
        lease = await engine_manager.load(model, force=body.force)
    except LeaseHeldError as exc:
        raise HTTPException(
            status_code=409,
            detail={"message": "GPU lease is held", "holder": exc.holder},
        ) from exc
    return {"lease": lease.as_dict()}


@router.post("/unload")
async def unload() -> dict:
    await engine_manager.unload()
    return {"lease": None}


@router.post("/chat")
async def engine_chat(request: Request):
    """Direct chat with whatever model holds the GPU lease — the only surface
    for the chat-only AirLLM lane (PLAN §6.2), works for all three lanes.
    Body is an OpenAI chat-completions request, passed through verbatim."""
    lease = engine_manager.lease
    if lease is None or lease.state != "ready":
        state = lease.state if lease else "none"
        raise HTTPException(409, f"no engine is serving (lease state: {state})")

    body = await request.body()
    url = f"{lease.base_url}/chat/completions"
    client = httpx.AsyncClient(timeout=_CHAT_TIMEOUT)
    try:
        upstream_request = client.build_request(
            "POST", url, content=body, headers={"content-type": "application/json"}
        )
        upstream = await client.send(upstream_request, stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        raise HTTPException(502, f"engine unreachable: {exc}") from exc

    content_type = upstream.headers.get("content-type", "application/json")
    if "text/event-stream" in content_type:
        async def stream():
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()

        return StreamingResponse(
            stream(), status_code=upstream.status_code, media_type="text/event-stream"
        )

    try:
        content = await upstream.aread()
    finally:
        await upstream.aclose()
        await client.aclose()
    return Response(content, status_code=upstream.status_code, media_type=content_type)
