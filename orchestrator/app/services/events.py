"""In-process pub/sub feeding the UI's SSE streams (PLAN §6.1 /events/stream).

publish() is safe from async code and from worker threads (downloads run in
threads); thread publishes hop onto the main loop via call_soon_threadsafe.
Slow subscribers drop events instead of blocking publishers.
"""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

QUEUE_SIZE = 256


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def publish(self, kind: str, data: dict[str, Any] | None = None) -> None:
        event = {"kind": kind, "ts": time.time(), **(data or {})}
        loop = self._loop
        if loop is None:
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._fanout(event)
        else:
            loop.call_soon_threadsafe(self._fanout, event)

    def _fanout(self, event: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass  # drop for slow consumers; UI state is re-fetchable

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[dict[str, Any]]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)


bus = EventBus()


def sse_format(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event)}\n\n"


async def sse_stream(
    queue: asyncio.Queue[dict[str, Any]], heartbeat_s: float = 20.0
) -> AsyncIterator[str]:
    """Yield SSE frames from a queue, with comment heartbeats to keep proxies open."""
    while True:
        try:
            event = await asyncio.wait_for(queue.get(), timeout=heartbeat_s)
            yield sse_format(event)
        except TimeoutError:
            yield ": keepalive\n\n"
