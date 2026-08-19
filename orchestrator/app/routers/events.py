from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from ..services.events import bus, sse_stream

router = APIRouter(prefix="/events")


@router.get("/stream")
async def global_stream() -> StreamingResponse:
    """Global SSE: engine state, downloads, session states, task states."""

    async def generate():
        yield ": connected\n\n"
        async with bus.subscribe() as queue:
            async for frame in sse_stream(queue):
                yield frame

    return StreamingResponse(generate(), media_type="text/event-stream")
