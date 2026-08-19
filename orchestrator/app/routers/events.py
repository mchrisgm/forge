from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from ..auth import current_user
from ..models import User
from ..services.events import bus, sse_format

router = APIRouter(prefix="/events")


@router.get("/stream")
async def global_stream(user: User = Depends(current_user)) -> StreamingResponse:
    """Global SSE: engine state, downloads, session states, task states.

    Shared-hardware events (engine, downloads, registry, skills) go to every
    subscriber; user-scoped events (session.*, task.*) carry a user_id and are
    delivered only to their owner — profile A must never see profile B's task
    results or session names.
    """

    async def filtered():
        yield ": connected\n\n"
        async with bus.subscribe() as queue:
            import asyncio

            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=20.0)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                owner = event.get("user_id")
                if owner is not None and owner != user.id and not user.is_admin:
                    continue
                yield sse_format(event)

    return StreamingResponse(filtered(), media_type="text/event-stream")
