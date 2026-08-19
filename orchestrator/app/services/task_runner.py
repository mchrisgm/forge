"""Task queue — fire a prompt at a session container and track it (PLAN M6).

Each Task gets its own OpenCode session inside the container so parallel runs
in different session containers stay independent. State transitions are
published on the event bus for the parallel-runs view.
"""

import asyncio
import logging
from datetime import UTC, datetime

from ..db import read_session, write_session
from ..models import ModelEntry, Session, SessionState, Task, TaskState, ThinkingLevel
from ..opencode_config import OPENCODE_PROVIDER, opencode_model_id
from . import opencode_client
from .events import bus
from .session_manager import opencode_base_url, session_manager
from .thinking import apply_to_prompt, directives_for

log = logging.getLogger(__name__)

_running: dict[int, asyncio.Task] = {}
_task_sessions: dict[int, str] = {}  # task_id -> session_id, for the reaper


def inflight_session_ids() -> set[str]:
    """Sessions with a task currently queued/running — the reaper skips them."""
    return {
        _task_sessions[task_id]
        for task_id, task in _running.items()
        if not task.done() and task_id in _task_sessions
    }


def _publish(task: Task) -> None:
    bus.publish(
        "task.state",
        {
            "task_id": task.id,
            "session_id": task.session_id,
            "user_id": task.user_id,
            "state": task.state.value,
            "result": task.result[:500],
        },
    )


def _set_state(task_id: int, state: TaskState, **fields) -> Task | None:
    with write_session() as db:
        task = db.get(Task, task_id)
        if task is None:
            return None
        task.state = state
        for key, value in fields.items():
            setattr(task, key, value)
        db.add(task)
    with read_session() as db:
        task = db.get(Task, task_id)
    if task:
        _publish(task)
    return task


async def create_task(
    session_id: str,
    prompt: str,
    thinking: ThinkingLevel = ThinkingLevel.auto,
    user_id: int | None = None,
) -> Task:
    with read_session() as db:
        session = db.get(Session, session_id)
    if session is None:
        raise ValueError("session not found")
    task = Task(
        session_id=session_id,
        prompt=prompt,
        thinking=thinking,
        user_id=user_id if user_id is not None else session.user_id,
    )
    with write_session() as db:
        db.add(task)
        db.flush()
        db.refresh(task)
        task_id = task.id
    with read_session() as db:
        task = db.get(Task, task_id)
    _publish(task)
    _task_sessions[task_id] = session_id
    _running[task_id] = asyncio.create_task(_run(task_id))
    return task


async def _run(task_id: int) -> None:
    try:
        with read_session() as db:
            task = db.get(Task, task_id)
            session = db.get(Session, task.session_id) if task else None
            model = db.get(ModelEntry, session.model_id) if session and session.model_id else None
        if task is None or session is None or model is None:
            _set_state(task_id, TaskState.failed, result="session or model missing")
            return

        if session.state in (SessionState.idle, SessionState.stopped):
            await session_manager.start(session.id)
        elif session.state == SessionState.creating:
            for _ in range(60):
                await asyncio.sleep(2)
                with read_session() as db:
                    session = db.get(Session, session.id)
                if session.state == SessionState.running:
                    break
            else:
                _set_state(task_id, TaskState.failed, result="session never became ready")
                return

        base_url = opencode_base_url(session.id)
        _set_state(task_id, TaskState.running)
        session_manager.touch(session.id)

        oc_session_id = task.opencode_session_id
        if not oc_session_id:
            oc_session_id = await _retry_create(base_url, f"task-{task_id}")
            _set_state(task_id, TaskState.running, opencode_session_id=oc_session_id)

        directives = directives_for(model, task.thinking)
        message = await opencode_client.send_prompt(
            base_url,
            oc_session_id,
            apply_to_prompt(task.prompt, directives),
            provider_id=OPENCODE_PROVIDER,
            model_id=opencode_model_id(model),
            system=directives.system or None,
        )
        text = opencode_client.extract_text(message) or "(no text response)"
        _set_state(
            task_id,
            TaskState.done,
            result=text[:4000],
            finished_at=datetime.now(UTC),
        )
        session_manager.touch(session.id)
    except Exception as exc:
        log.exception("task %s failed", task_id)
        _set_state(
            task_id,
            TaskState.failed,
            result=str(exc)[:2000],
            finished_at=datetime.now(UTC),
        )
    finally:
        _running.pop(task_id, None)
        _task_sessions.pop(task_id, None)


async def _retry_create(base_url: str, title: str, attempts: int = 10) -> str:
    last_exc: Exception | None = None
    for _ in range(attempts):
        try:
            return await opencode_client.create_session(base_url, title)
        except Exception as exc:  # container may still be booting OpenCode
            last_exc = exc
            await asyncio.sleep(3)
    raise RuntimeError(f"could not reach OpenCode in session container: {last_exc}")
