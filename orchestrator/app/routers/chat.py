"""The Chat section: per-user conversations with history, continuation,
temporary (unsaved) chats, attachments, and memory integration."""

import json
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import select

from ..auth import current_user
from ..db import read_session, write_session
from ..models import (
    ChatMessage,
    Conversation,
    ModelEntry,
    ThinkingLevel,
    Upload,
    User,
)
from ..services import chat_service, memory
from ..services.engine_manager import engine_manager

log = logging.getLogger(__name__)
router = APIRouter(prefix="/chat")


class ConversationCreate(BaseModel):
    title: str = ""
    model_slug: str = ""
    thinking: ThinkingLevel = ThinkingLevel.auto
    memory_enabled: bool = True


class ConversationPatch(BaseModel):
    title: str | None = None
    model_slug: str | None = None
    thinking: ThinkingLevel | None = None
    memory_enabled: bool | None = None
    archived: bool | None = None


class MessageBody(BaseModel):
    content: str
    attachment_ids: list[str] = []
    thinking: ThinkingLevel | None = None  # overrides the conversation default


class TemporaryBody(BaseModel):
    messages: list[dict]  # [{role, content}] — client keeps the history
    model_slug: str = ""
    thinking: ThinkingLevel = ThinkingLevel.auto
    attachment_ids: list[str] = []


def _own_conversation(conversation_id: str, user: User) -> Conversation:
    with read_session() as db:
        conversation = db.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != user.id:
        raise HTTPException(404, "conversation not found")
    return conversation


def _own_uploads(ids: list[str], user: User) -> list[Upload]:
    result = []
    with read_session() as db:
        for upload_id in ids[:8]:
            upload = db.get(Upload, upload_id)
            if upload is None or upload.user_id != user.id:
                raise HTTPException(404, f"attachment {upload_id} not found")
            result.append(upload)
    return result


def _resolve_lease(model_slug: str):
    from .openai_router import resolve_lease

    try:
        return resolve_lease(model_slug or None)
    except HTTPException as exc:
        raise HTTPException(
            409,
            {"message": "no model is serving this chat", "detail": exc.detail},
        ) from exc


def _model_for_lease(lease) -> ModelEntry | None:
    with read_session() as db:
        return db.get(ModelEntry, lease.model_id)


def _attachment_meta(ids: list[str]) -> list[dict]:
    if not ids:
        return []
    with read_session() as db:
        rows = [db.get(Upload, upload_id) for upload_id in ids]
    return [
        {
            "id": u.id,
            "filename": u.filename,
            "kind": u.kind,
            "mime": u.mime,
            "size_bytes": u.size_bytes,
        }
        for u in rows
        if u is not None
    ]


# ── conversations CRUD ──────────────────────────────────────────────────────


@router.get("/conversations")
def list_conversations(
    archived: bool = False, user: User = Depends(current_user)
) -> list[dict]:
    with read_session() as db:
        rows = db.exec(
            select(Conversation).where(
                Conversation.user_id == user.id,
                Conversation.archived == archived,  # noqa: E712
            )
        ).all()
    rows = sorted(rows, key=lambda r: r.updated_at, reverse=True)
    return [r.model_dump(mode="json", exclude={"summary"}) for r in rows]


@router.post("/conversations")
def create_conversation(
    body: ConversationCreate, user: User = Depends(current_user)
) -> dict:
    conversation = Conversation(
        user_id=user.id,
        title=body.title.strip() or "New chat",
        model_slug=body.model_slug,
        thinking=body.thinking,
        memory_enabled=body.memory_enabled,
    )
    with write_session() as db:
        db.add(conversation)
        db.flush()
        conversation_id = conversation.id
    with read_session() as db:
        conversation = db.get(Conversation, conversation_id)
    return conversation.model_dump(mode="json")


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: str, user: User = Depends(current_user)) -> dict:
    conversation = _own_conversation(conversation_id, user)
    with read_session() as db:
        messages = sorted(
            db.exec(
                select(ChatMessage).where(
                    ChatMessage.conversation_id == conversation_id
                )
            ).all(),
            key=lambda m: m.id or 0,
        )
    return {
        **conversation.model_dump(mode="json"),
        "messages": [
            {
                **m.model_dump(mode="json", exclude={"attachments_json"}),
                "attachments": _attachment_meta(json.loads(m.attachments_json or "[]")),
            }
            for m in messages
        ],
    }


@router.patch("/conversations/{conversation_id}")
def patch_conversation(
    conversation_id: str, body: ConversationPatch, user: User = Depends(current_user)
) -> dict:
    _own_conversation(conversation_id, user)
    with write_session() as db:
        row = db.get(Conversation, conversation_id)
        for field in ("title", "model_slug", "thinking", "memory_enabled", "archived"):
            value = getattr(body, field)
            if value is not None:
                setattr(row, field, value)
        row.updated_at = datetime.now(UTC)
        db.add(row)
        db.flush()
        db.refresh(row)
        result = row.model_dump(mode="json")
    return result


@router.delete("/conversations/{conversation_id}")
def delete_conversation(
    conversation_id: str, user: User = Depends(current_user)
) -> dict:
    _own_conversation(conversation_id, user)
    with write_session() as db:
        from sqlmodel import col
        from sqlmodel import delete as sql_delete

        db.exec(
            sql_delete(ChatMessage).where(
                col(ChatMessage.conversation_id) == conversation_id
            )
        )
        row = db.get(Conversation, conversation_id)
        if row:
            db.delete(row)
    return {"ok": True}


# ── messaging ───────────────────────────────────────────────────────────────


def _history_for(conversation: Conversation) -> list[dict]:
    with read_session() as db:
        messages = sorted(
            db.exec(
                select(ChatMessage).where(
                    ChatMessage.conversation_id == conversation.id,
                    ChatMessage.id > conversation.summarized_until,  # type: ignore[arg-type]
                )
            ).all(),
            key=lambda m: m.id or 0,
        )
    return [{"role": m.role, "content": m.content} for m in messages]


async def _post_exchange(
    user: User, conversation: Conversation, user_text: str, assistant_text: str
) -> None:
    """Background follow-ups after a saved exchange: auto-title, compression,
    memory extraction. Never raises into the request path."""
    with read_session() as db:
        count = len(
            db.exec(
                select(ChatMessage.id).where(
                    ChatMessage.conversation_id == conversation.id
                )
            ).all()
        )
    if count <= 2 and conversation.title == "New chat":
        title = await memory._model_text(
            f"User: {user_text[:400]}\nAssistant: {assistant_text[:400]}",
            "Give this conversation a title of 3-6 plain words. Reply with only "
            "the title — no quotes, no punctuation at the end.",
            max_tokens=20,
        )
        if title:
            with write_session() as db:
                row = db.get(Conversation, conversation.id)
                if row and row.title == "New chat":
                    row.title = title.strip().strip('"')[:80]
                    db.add(row)
    await memory.compress_conversation(conversation.id)
    if user.memory_enabled and conversation.memory_enabled:
        await memory.extract_from_exchange(
            user.id, conversation.id, user_text, assistant_text
        )


@router.post("/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: str, body: MessageBody, user: User = Depends(current_user)
):
    if not body.content.strip() and not body.attachment_ids:
        raise HTTPException(400, "message content required")
    conversation = _own_conversation(conversation_id, user)
    attachments = _own_uploads(body.attachment_ids, user)
    lease = _resolve_lease(conversation.model_slug)
    model = _model_for_lease(lease)
    thinking = body.thinking or conversation.thinking

    use_memory = user.memory_enabled and conversation.memory_enabled
    entries = memory.retrieve(user.id, body.content) if use_memory else []
    if entries:
        memory.record_use(entries)

    history = _history_for(conversation)
    messages = chat_service.assemble(
        user,
        history,
        body.content,
        attachments,
        model,
        thinking,
        entries,
        conversation.summary,
    )

    # Persist the user turn before streaming so a dropped connection still
    # leaves a consistent history.
    user_message = ChatMessage(
        conversation_id=conversation_id,
        role="user",
        content=body.content,
        attachments_json=json.dumps([a.id for a in attachments]),
        token_estimate=memory.estimate_tokens(body.content),
    )
    with write_session() as db:
        db.add(user_message)
        row = db.get(Conversation, conversation_id)
        if row:
            row.updated_at = datetime.now(UTC)
            if not row.model_slug:
                row.model_slug = lease.model_slug
            db.add(row)

    collected: list[str] = []

    async def generate():
        async for frame in chat_service.stream_completion(
            lease.base_url, lease.model_slug, messages, collected
        ):
            yield frame
        assistant_text = "".join(collected)
        assistant_id = None
        if assistant_text:
            with write_session() as db:
                assistant_message = ChatMessage(
                    conversation_id=conversation_id,
                    role="assistant",
                    content=assistant_text,
                    token_estimate=memory.estimate_tokens(assistant_text),
                )
                db.add(assistant_message)
                db.flush()
                assistant_id = assistant_message.id
            memory.schedule_background(
                _post_exchange(user, conversation, body.content, assistant_text)
            )
        yield (
            "data: "
            + json.dumps(
                {
                    "forge": "done",
                    "conversation_id": conversation_id,
                    "assistant_message_id": assistant_id,
                }
            )
            + "\n\n"
        )

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/temporary")
async def temporary_chat(body: TemporaryBody, user: User = Depends(current_user)):
    """Incognito: streams a reply, stores nothing, reads no memory."""
    if not body.messages:
        raise HTTPException(400, "messages required")
    lease = _resolve_lease(body.model_slug)
    model = _model_for_lease(lease)
    attachments = _own_uploads(body.attachment_ids, user)

    history = [
        {"role": m.get("role", "user"), "content": str(m.get("content", ""))}
        for m in body.messages[:-1]
    ]
    last = body.messages[-1]
    messages = chat_service.assemble(
        user,
        history,
        str(last.get("content", "")),
        attachments,
        model,
        body.thinking,
        memory_entries=[],  # temporary chats never read memory
        summary="",
    )

    collected: list[str] = []

    async def generate():
        async for frame in chat_service.stream_completion(
            lease.base_url, lease.model_slug, messages, collected
        ):
            yield frame
        yield 'data: {"forge": "done", "temporary": true}\n\n'

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.get("/status")
def chat_status(user: User = Depends(current_user)) -> dict:
    """What the chat composer needs: which models are serving right now."""
    leases = [lease.as_dict() for lease in engine_manager.ready_leases()]
    return {"serving": leases}
