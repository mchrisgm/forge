"""Background chat generation jobs.

A chat generation runs as an orchestrator-owned asyncio task, NOT inside the
HTTP request that started it — so leaving the chat (closing the SSE stream)
never stops the generation. Clients *subscribe* to a job's output; a replay
buffer lets a returning client re-attach and receive everything produced so
far followed by live tokens.

Concurrency is load-balanced across the ready engine leases: each new job picks
the least-loaded lease serving its model, and a per-lease slot budget queues
work so an engine (above all the single-slot AirLLM lane) is never
oversubscribed. Multiple conversations therefore generate at once, spread over
the available GPUs/slots, and every job always streams.

Layering: this service knows the engine leases and chat_service.stream_completion;
the per-exchange follow-ups (auto-title, memory) are injected by the router as a
callback so the memory/title policy stays there.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable

from ..config import get_settings
from ..db import write_session
from ..models import ChatMessage, EngineKind
from . import chat_service, memory
from .engine_manager import Lease, engine_manager

log = logging.getLogger(__name__)

# Keep a finished job in the registry this long so a client that re-attaches
# right after completion still replays the final frames without a DB round-trip.
FINISHED_TTL_S = 120.0


def lease_capacity(lease: Lease) -> int:
    """Concurrent generations an engine lane can take without thrashing."""
    settings = get_settings()
    return {
        EngineKind.llamacpp: max(1, settings.llamacpp_slots),
        EngineKind.vllm: max(1, settings.vllm_max_concurrency),
        EngineKind.sglang: max(1, settings.sglang_max_concurrency),
        EngineKind.tabby: max(1, settings.tabby_max_concurrency),
        EngineKind.airllm: 1,  # streams layers from disk — strictly one at a time
    }.get(lease.engine, 1)


def _frame(payload: dict) -> str:
    return "data: " + json.dumps(payload) + "\n\n"


def _frame_error(frame: str) -> str | None:
    """The engine reports connect/HTTP failures as a normal ``{"error": …}``
    data frame rather than by raising, so a job that only saw such a frame must
    still end in the 'error' state. Return the message when this frame is one."""
    payload = frame.strip()
    if payload.startswith("data:"):
        payload = payload[len("data:") :].strip()
    if not payload or payload == "[DONE]":
        return None
    try:
        obj = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return obj.get("error") if isinstance(obj, dict) else None


class ChatJob:
    """One server-side generation for a conversation, with a replay buffer and
    fan-out to any number of live subscribers."""

    def __init__(self, conversation_id: str, user_id: int, lease: Lease) -> None:
        self.conversation_id = conversation_id
        self.user_id = user_id
        self.model_slug = lease.model_slug
        self.lease_key = lease.base_url  # unique per (engine, gpu)
        # Signature of the user turn this job is answering (content+attachments).
        # A second POST re-attaches only when its turn matches; a genuinely
        # different message must not be swallowed by the reattach shortcut.
        self.turn_key = ""
        self.state = "queued"  # queued | running | done | error
        self.error = ""
        self.assistant_message_id: int | None = None
        self.created_at = time.monotonic()
        self.finished_at: float | None = None
        self.frames: list[str] = []  # replay buffer of SSE frames, verbatim
        self.collected: list[str] = []  # assistant text pieces
        self.task: asyncio.Task | None = None
        self._subscribers: set[asyncio.Queue] = set()
        self._done = asyncio.Event()

    # ── producer ────────────────────────────────────────────────────────────
    def push(self, frame: str) -> None:
        self.frames.append(frame)
        for q in list(self._subscribers):
            q.put_nowait(frame)

    def _finish(self) -> None:
        self.finished_at = time.monotonic()
        self._done.set()
        for q in list(self._subscribers):
            q.put_nowait(None)  # stream-over sentinel

    # ── consumer ────────────────────────────────────────────────────────────
    async def subscribe(self) -> AsyncIterator[str]:
        """Replay everything buffered, then stream live frames until the job
        ends. Detaching (client disconnect) only drops this subscriber — the
        job keeps running."""
        q: asyncio.Queue = asyncio.Queue()
        # Snapshot + register with NO await between them: on the single-threaded
        # loop no push() can interleave, so a frame is never both replayed and
        # queued (no dup) nor pushed to neither (no gap).
        replay = list(self.frames)
        done = self._done.is_set()
        if not done:
            self._subscribers.add(q)
        try:
            for frame in replay:
                yield frame
            if done:
                return
            while True:
                frame = await q.get()
                if frame is None:
                    break
                yield frame
        finally:
            self._subscribers.discard(q)

    def status(self) -> dict:
        return {
            "conversation_id": self.conversation_id,
            "state": self.state,
            "assistant_message_id": self.assistant_message_id,
            "chars": len("".join(self.collected)),
        }


# Per-lease concurrency budgets, keyed by lease base_url. Created lazily and
# sized to the lane's capacity; survives across job lifetimes.
_lease_slots: dict[str, asyncio.Semaphore] = {}


def _slots_for(lease: Lease) -> asyncio.Semaphore:
    sem = _lease_slots.get(lease.base_url)
    if sem is None:
        sem = asyncio.Semaphore(lease_capacity(lease))
        _lease_slots[lease.base_url] = sem
    return sem


async def _pump_frames(
    job: ChatJob,
    base_url: str,
    model_slug: str,
    messages: list[dict],
    idle_timeout_s: float,
) -> str | None:
    """Stream the engine's frames into `job`, aborting if the engine goes
    silent for `idle_timeout_s` (a wedged engine must not hang the job, its
    lease slot, and every subscriber forever). Returns the engine's error
    message if it reported one as a data frame, else None."""
    error_text: str | None = None
    source = chat_service.stream_completion(base_url, model_slug, messages, job.collected)
    ait = source.__aiter__()
    try:
        while True:
            try:
                async with asyncio.timeout(idle_timeout_s):
                    frame = await ait.__anext__()
            except StopAsyncIteration:
                break
            except TimeoutError:
                raise TimeoutError(
                    f"engine produced no output for {idle_timeout_s:.0f}s"
                ) from None
            job.push(frame)
            error_text = _frame_error(frame) or error_text
    finally:
        await source.aclose()
    return error_text


class ChatJobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, ChatJob] = {}  # keyed by conversation_id

    def _evict_stale(self) -> None:
        now = time.monotonic()
        for cid in list(self._jobs):
            job = self._jobs[cid]
            if job.finished_at is not None and now - job.finished_at > FINISHED_TTL_S:
                del self._jobs[cid]

    def slot_for(self, lease: Lease) -> asyncio.Semaphore:
        """The per-lease concurrency slot, for engine consumers OUTSIDE the job
        manager (temporary chats, the memory helper's short completions) so they
        honor the same per-lane budget and never oversubscribe a lane — above
        all the single-slot AirLLM lane. Use as ``async with mgr.slot_for(le):``."""
        return _slots_for(lease)

    def get(self, conversation_id: str) -> ChatJob | None:
        self._evict_stale()
        return self._jobs.get(conversation_id)

    def active_for(self, conversation_ids: set[str]) -> list[dict]:
        """Status of in-flight jobs whose conversation is in `conversation_ids`."""
        self._evict_stale()
        return [
            job.status()
            for cid, job in self._jobs.items()
            if cid in conversation_ids and job.state in ("queued", "running")
        ]

    def _load_on(self, lease_key: str) -> int:
        return sum(
            1
            for j in self._jobs.values()
            if j.lease_key == lease_key and j.state in ("queued", "running")
        )

    def select_lease(self, model_slug: str) -> Lease | None:
        """Least-loaded ready TEXT lease serving `model_slug` (any lease when
        the conversation pins no model). Load is per-slot so work spreads
        across both GPUs and an engine's parallel slots."""
        self._evict_stale()
        leases = engine_manager.ready_text_leases()
        if model_slug:
            leases = [le for le in leases if le.model_slug == model_slug]
        if not leases:
            return None
        return min(
            leases,
            key=lambda le: self._load_on(le.base_url) / max(1, lease_capacity(le)),
        )

    def start(
        self,
        *,
        conversation_id: str,
        user_id: int,
        lease: Lease,
        model_slug: str,
        messages: list[dict],
        turn_key: str = "",
        post_exchange: Callable[[str], Awaitable[None]] | None = None,
    ) -> ChatJob:
        """Create and launch a background generation for this conversation,
        replacing any finished job still parked under the same id."""
        self._evict_stale()
        job = ChatJob(conversation_id, user_id, lease)
        job.turn_key = turn_key
        self._jobs[conversation_id] = job
        job.task = asyncio.create_task(
            self._run(job, lease, model_slug, messages, post_exchange)
        )
        return job

    async def _run(
        self,
        job: ChatJob,
        lease: Lease,
        model_slug: str,
        messages: list[dict],
        post_exchange: Callable[[str], Awaitable[None]] | None,
    ) -> None:
        # Whatever happens below, the job MUST reach a terminal state and wake
        # its subscribers — a stuck 'running' job would hang every reader on it.
        try:
            sem = _slots_for(lease)
            # Announce queueing only when a slot is genuinely unavailable — the
            # common uncontended path streams tokens with no extra status frame.
            was_queued = sem.locked()
            if was_queued:
                job.push(
                    _frame(
                        {
                            "forge": "queued",
                            "conversation_id": job.conversation_id,
                            "detail": (
                                f"queued — the {lease.engine.value} lane is busy "
                                "with another generation"
                            ),
                        }
                    )
                )
            await sem.acquire()
            try:
                job.state = "running"
                if was_queued:
                    job.push(
                        _frame(
                            {"forge": "running", "conversation_id": job.conversation_id}
                        )
                    )
                # Always tell the client what is actually happening before the
                # first token — prompt processing on a big model can take a
                # while and a silent wait reads as "stuck".
                job.push(
                    _frame(
                        {
                            "forge": "status",
                            "conversation_id": job.conversation_id,
                            "detail": (
                                f"prompt sent to {model_slug} "
                                f"({lease.engine.value}) — processing"
                            ),
                        }
                    )
                )
                error_text = await _pump_frames(
                    job,
                    lease.base_url,
                    model_slug,
                    messages,
                    get_settings().chat_stream_idle_timeout_s,
                )
                # A connect/HTTP failure arrives as a data frame (already
                # forwarded to subscribers), not an exception — record it so the
                # job's terminal state is 'error', not a false 'done'.
                if error_text:
                    job.error = error_text
            finally:
                sem.release()
        except asyncio.CancelledError:
            job.error = "cancelled"
            raise
        except Exception as exc:  # engine/network failure, or a bug below
            log.exception("chat generation failed")
            job.error = str(exc)
            job.push(_frame({"error": f"generation failed: {exc}"}))
        finally:
            assistant_id = None
            try:
                assistant_id = self._persist(job)
            except Exception:
                log.exception("failed to persist chat generation")
            job.assistant_message_id = assistant_id
            job.state = "error" if job.error else "done"
            job.push(
                _frame(
                    {
                        "forge": "done",
                        "conversation_id": job.conversation_id,
                        "assistant_message_id": assistant_id,
                    }
                )
            )
            job._finish()
            if assistant_id and post_exchange is not None and not job.error:
                memory.schedule_background(post_exchange("".join(job.collected)))

    def _persist(self, job: ChatJob) -> int | None:
        """Save whatever streamed — server-side, so a detached client's partial
        (or full) reply is never lost."""
        text = "".join(job.collected)
        if not text:
            return None
        with write_session() as db:
            message = ChatMessage(
                conversation_id=job.conversation_id,
                role="assistant",
                content=text,
                token_estimate=memory.estimate_tokens(text),
            )
            db.add(message)
            db.flush()
            return message.id


chat_job_manager = ChatJobManager()
