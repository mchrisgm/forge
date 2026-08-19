import asyncio

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from sqlmodel import select

from ..auth import current_user
from ..db import read_session
from ..models import Session, Task, ThinkingLevel, User
from ..services import exec_service, opencode_client, task_runner
from ..services.exec_service import ExecError
from ..services.session_manager import (
    SessionError,
    opencode_base_url,
    session_manager,
)

router = APIRouter()

# Hop-by-hop / connection-specific headers that must not be forwarded either way
_SKIP_HEADERS = {
    "host", "connection", "keep-alive", "transfer-encoding", "upgrade",
    "proxy-authenticate", "proxy-authorization", "te", "trailers",
    "content-length", "authorization",
}


def _get_session(session_id: str, user: User) -> Session:
    with read_session() as db:
        session = db.get(Session, session_id)
    owned = session is not None and (
        session.user_id == user.id
        # Legacy pre-multi-user sessions (no owner) belong to the admin view.
        or (session.user_id is None and user.is_admin)
    )
    if not owned:
        raise HTTPException(404, "session not found")
    return session


class CreateSessionBody(BaseModel):
    name: str
    model_id: int
    repo_url: str | None = None


class WriteFileBody(BaseModel):
    path: str
    content: str


class CommitBody(BaseModel):
    message: str
    add_all: bool = True


class TaskBody(BaseModel):
    prompt: str
    thinking: ThinkingLevel = ThinkingLevel.auto


@router.get("/sessions")
def list_sessions(user: User = Depends(current_user)) -> list[dict]:
    with read_session() as db:
        rows = db.exec(select(Session)).all()
    rows = [
        r for r in rows
        if r.user_id == user.id or (r.user_id is None and user.is_admin)
    ]
    rows = sorted(rows, key=lambda r: r.created_at, reverse=True)
    return [r.model_dump(mode="json") for r in rows]


@router.post("/sessions")
async def create_session(
    body: CreateSessionBody, user: User = Depends(current_user)
) -> dict:
    try:
        session = await session_manager.create(
            body.name, body.model_id, body.repo_url, user_id=user.id
        )
    except SessionError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    return session.model_dump(mode="json")


@router.get("/sessions/{session_id}")
def get_session(session_id: str, user: User = Depends(current_user)) -> dict:
    return _get_session(session_id, user).model_dump(mode="json")


@router.post("/sessions/{session_id}/stop")
async def stop_session(session_id: str, user: User = Depends(current_user)) -> dict:
    _get_session(session_id, user)
    try:
        session = await session_manager.stop(session_id)
    except SessionError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    return session.model_dump(mode="json")


@router.post("/sessions/{session_id}/start")
async def start_session(session_id: str, user: User = Depends(current_user)) -> dict:
    _get_session(session_id, user)
    try:
        session = await session_manager.start(session_id)
    except SessionError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    return session.model_dump(mode="json")


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, user: User = Depends(current_user)) -> dict:
    _get_session(session_id, user)
    try:
        await session_manager.delete(session_id)
    except SessionError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    return {"ok": True}


# ── OpenCode reverse proxy (PLAN §6.1 "the key trick") ──────────────────────


@router.api_route(
    "/sessions/{session_id}/opencode/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
async def opencode_proxy(
    session_id: str, path: str, request: Request, user: User = Depends(current_user)
):
    _get_session(session_id, user)
    session_manager.touch(session_id)
    base = opencode_base_url(session_id)
    url = f"{base}/{path}"
    headers = {
        k: v for k, v in request.headers.items() if k.lower() not in _SKIP_HEADERS
    }
    body = await request.body()

    client = httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=None, write=30, pool=10))
    try:
        upstream_request = client.build_request(
            request.method, url, headers=headers, content=body,
            params=dict(request.query_params),
        )
        upstream = await client.send(upstream_request, stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        raise HTTPException(502, f"session container unreachable: {exc}") from exc

    response_headers = {
        k: v for k, v in upstream.headers.items() if k.lower() not in _SKIP_HEADERS
    }

    if "text/event-stream" in upstream.headers.get("content-type", ""):
        async def stream():
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()
                session_manager.touch(session_id)

        return StreamingResponse(
            stream(), status_code=upstream.status_code, headers=response_headers,
            media_type="text/event-stream",
        )

    try:
        content = await upstream.aread()
    finally:
        await upstream.aclose()
        await client.aclose()
    # Touch again on completion: a long blocking agent turn must count as
    # activity right up to its end, or the idle reaper undercounts.
    session_manager.touch(session_id)
    return Response(
        content=content, status_code=upstream.status_code, headers=response_headers,
    )


@router.get("/sessions/{session_id}/events")
async def session_events(
    session_id: str, user: User = Depends(current_user)
) -> StreamingResponse:
    """Proxied OpenCode SSE event stream for this session's container."""
    _get_session(session_id, user)
    base = opencode_base_url(session_id)

    async def generate():
        import json as _json

        yield ": connected\n\n"
        try:
            async for event in opencode_client.event_stream(base):
                yield f"data: {_json.dumps(event)}\n\n"
        except (httpx.HTTPError, asyncio.CancelledError):
            yield 'data: {"type": "forge.disconnected"}\n\n'

    return StreamingResponse(generate(), media_type="text/event-stream")


# ── Files & git via docker exec ─────────────────────────────────────────────


@router.get("/sessions/{session_id}/files")
async def list_files(
    session_id: str, path: str = "", user: User = Depends(current_user)
) -> dict:
    session = _get_session(session_id, user)
    try:
        entries = await asyncio.to_thread(exec_service.list_dir, session, path)
    except ExecError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    return {"path": path, "entries": [e.__dict__ for e in entries]}


@router.get("/sessions/{session_id}/file")
async def read_file(
    session_id: str, path: str, user: User = Depends(current_user)
) -> dict:
    session = _get_session(session_id, user)
    try:
        content = await asyncio.to_thread(exec_service.read_file, session, path)
    except ExecError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    return {"path": path, "content": content}


@router.put("/sessions/{session_id}/file")
async def write_file(
    session_id: str, body: WriteFileBody, user: User = Depends(current_user)
) -> dict:
    session = _get_session(session_id, user)
    try:
        await asyncio.to_thread(exec_service.write_file, session, body.path, body.content)
    except ExecError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    session_manager.touch(session_id)
    return {"ok": True}


@router.get("/sessions/{session_id}/git/status")
async def git_status(session_id: str, user: User = Depends(current_user)) -> dict:
    session = _get_session(session_id, user)
    try:
        return await asyncio.to_thread(exec_service.git_status, session)
    except ExecError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc


@router.get("/sessions/{session_id}/git/log")
async def git_log(
    session_id: str, limit: int = 30, user: User = Depends(current_user)
) -> list[dict]:
    session = _get_session(session_id, user)
    try:
        return await asyncio.to_thread(exec_service.git_log, session, limit)
    except ExecError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc


@router.get("/sessions/{session_id}/git/diff")
async def git_diff(
    session_id: str, staged: bool = False, user: User = Depends(current_user)
) -> dict:
    session = _get_session(session_id, user)
    try:
        diff = await asyncio.to_thread(exec_service.git_diff, session, staged)
    except ExecError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    return {"diff": diff}


@router.post("/sessions/{session_id}/git/commit")
async def git_commit(
    session_id: str, body: CommitBody, user: User = Depends(current_user)
) -> dict:
    session = _get_session(session_id, user)
    try:
        out = await asyncio.to_thread(
            exec_service.git_commit, session, body.message, body.add_all
        )
    except ExecError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    session_manager.touch(session_id)
    return {"output": out}


@router.post("/sessions/{session_id}/git/push")
async def git_push(session_id: str, user: User = Depends(current_user)) -> dict:
    session = _get_session(session_id, user)
    try:
        out = await asyncio.to_thread(exec_service.git_push, session)
    except ExecError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    return {"output": out}


# ── Tasks (PLAN M6 — parallel runs) ─────────────────────────────────────────


@router.get("/tasks")
def list_all_tasks(user: User = Depends(current_user)) -> list[dict]:
    with read_session() as db:
        rows = db.exec(select(Task)).all()
    rows = [
        r for r in rows
        if r.user_id == user.id or (r.user_id is None and user.is_admin)
    ]
    rows = sorted(rows, key=lambda r: r.created_at, reverse=True)
    return [r.model_dump(mode="json") for r in rows]


@router.get("/sessions/{session_id}/tasks")
def list_session_tasks(
    session_id: str, user: User = Depends(current_user)
) -> list[dict]:
    _get_session(session_id, user)
    with read_session() as db:
        rows = db.exec(select(Task).where(Task.session_id == session_id)).all()
    rows = sorted(rows, key=lambda r: r.created_at, reverse=True)
    return [r.model_dump(mode="json") for r in rows]


@router.post("/sessions/{session_id}/tasks")
async def create_task(
    session_id: str, body: TaskBody, user: User = Depends(current_user)
) -> dict:
    _get_session(session_id, user)
    if not body.prompt.strip():
        raise HTTPException(400, "prompt required")
    task = await task_runner.create_task(
        session_id, body.prompt, body.thinking, user_id=user.id
    )
    return task.model_dump(mode="json")
