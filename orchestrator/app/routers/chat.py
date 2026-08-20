"""The Chat section: per-user conversations with history, continuation,
temporary (unsaved) chats, attachments, and memory integration."""

import json
import logging
import re
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
from ..services import (
    chat_service,
    image_service,
    memory,
    model_router,
    uploads,
    web_reader,
)
from ..services.chat_jobs import chat_job_manager
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


def _select_lease(model_slug: str):
    """Pick the least-loaded ready TEXT lease serving this chat's model,
    spreading concurrent generations across GPUs/slots. 409 with a helpful
    message when nothing serves it."""
    lease = chat_job_manager.select_lease(model_slug or "")
    if lease is None:
        served = (
            ", ".join(le.model_slug for le in engine_manager.ready_text_leases())
            or "(none)"
        )
        detail = (
            f"model {model_slug!r} is not being served. Currently serving: "
            f"{served}. Load it from the Models page first."
            if model_slug
            else "No model is loaded. Load one from the Models page first."
        )
        raise HTTPException(
            409, {"message": "no model is serving this chat", "detail": detail}
        )
    return lease


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
            "generated": u.generated,
            "prompt": u.prompt,
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
    new_attachments_json = json.dumps([a.id for a in attachments])
    turn_key = new_attachments_json + "\n" + body.content

    # Re-attach, don't restart: if a generation is already running for this
    # conversation, stream the existing job rather than launching a second one.
    # Only the SAME still-unanswered turn re-attaches (the user navigated away
    # and came back, or a duplicate/retried submit); a genuinely different
    # message can't run until this reply finishes — it needs that reply in the
    # history — so reject it instead of silently dropping it.
    running = chat_job_manager.get(conversation_id)
    if running is not None and running.state in ("queued", "running"):
        if running.turn_key == turn_key:
            return StreamingResponse(
                running.subscribe(), media_type="text/event-stream"
            )
        raise HTTPException(
            409, "a reply is still being generated for this chat — wait for it "
            "to finish before sending another message"
        )

    # Model selection — "auto" OR a specific downloaded model — is resolved and
    # LOADED inside the background job's prepare hook (model_router), so a chat
    # can use ANY downloaded model, not just one that happens to be serving.
    # Nothing needs a lease up front; even a minutes-long load streams progress
    # as forge:"status" frames and survives the client detaching. An empty slug
    # (legacy chats) means auto.
    slug = conversation.model_slug
    is_auto = slug in ("", model_router.AUTO_SLUG)
    explicit_model = None
    if is_auto:
        if not model_router.ready_candidates():
            raise HTTPException(
                409,
                {
                    "message": "no model is ready to route to",
                    "detail": "Auto mode needs at least one downloaded text "
                    "model. Download one from the Models page first.",
                },
            )
    else:
        explicit_model = model_router.model_for_slug(slug)
        if explicit_model is None:
            raise HTTPException(
                409,
                {
                    "message": "that model isn't downloaded",
                    "detail": f"{slug!r} is no longer available — pick another "
                    "model from the menu, or download it from the Models page.",
                },
            )
    lease = None
    thinking = body.thinking or conversation.thinking

    use_memory = user.memory_enabled and conversation.memory_enabled
    entries = memory.retrieve(user.id, body.content) if use_memory else []
    if entries:
        memory.record_use(entries)

    # Retry idempotence: re-sending the SAME content AND attachments while it
    # is still the (unanswered) last message reuses that turn instead of
    # duplicating it. Attachments must match — an attachment-only follow-up
    # with different files is a new turn, not a retry.
    with read_session() as db:
        last = db.exec(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation_id)
            .order_by(ChatMessage.id.desc())  # type: ignore[union-attr]
        ).first()
    reused_last = (
        last is not None
        and last.role == "user"
        and last.content == body.content
        and (last.attachments_json or "[]") == new_attachments_json
    )

    history = _history_for(conversation)
    if reused_last and history:
        history = history[:-1]  # the retried turn re-enters via body.content

    def _assemble(answering_model: ModelEntry | None) -> list[dict]:
        return chat_service.assemble(
            user,
            history,
            body.content,
            attachments,
            answering_model,
            thinking,
            entries,
            conversation.summary,
        )

    # Assembly happens inside prepare against the model that actually answers
    # (auto's routed pick, or the explicit choice once loaded), so the system
    # prompt and tool formatting match it.
    messages: list[dict] = []

    # Persist the user turn before generating so the history stays consistent
    # even if the client is long gone (skipped when reusing a retried turn).
    with write_session() as db:
        if not reused_last:
            db.add(
                ChatMessage(
                    conversation_id=conversation_id,
                    role="user",
                    content=body.content,
                    attachments_json=new_attachments_json,
                    token_estimate=memory.estimate_tokens(body.content),
                )
            )
        row = db.get(Conversation, conversation_id)
        if row:
            row.updated_at = datetime.now(UTC)
            if not row.model_slug:
                # An empty (legacy) selection is auto routing — pin it so the
                # picker and the next turn agree on the mode.
                row.model_slug = model_router.AUTO_SLUG
            db.add(row)

    async def post_exchange(assistant_text: str) -> None:
        await _post_exchange(user, conversation, body.content, assistant_text)

    # Runs INSIDE the background job (chat_jobs._run) before the slot is taken:
    # routing (auto) and the model load both stream progress as forge:"status"
    # frames and survive the client detaching.
    async def prepare(push_status):
        if is_auto:
            push_status("choosing the best model for this prompt…")
            chosen, reason = await model_router.choose_model(body.content)
            push_status(f"routed to {chosen.display_name} — {reason}")
        else:
            chosen = explicit_model
        routed = await model_router.ensure_serving(chosen, push_status)
        answering = _model_for_lease(routed) or chosen
        return routed, routed.model_slug, _assemble(answering)

    # The job runs in the orchestrator's event loop, independent of this
    # request — closing the SSE below never stops it.
    job = chat_job_manager.start(
        conversation_id=conversation_id,
        user_id=user.id,
        lease=lease,
        model_slug=model_router.AUTO_SLUG if is_auto else slug,
        messages=messages,
        turn_key=turn_key,
        post_exchange=post_exchange,
        prepare=prepare,
    )
    return StreamingResponse(job.subscribe(), media_type="text/event-stream")


@router.post("/conversations/{conversation_id}/cancel")
async def cancel_generation(
    conversation_id: str, user: User = Depends(current_user)
):
    """Stop the in-flight generation for this conversation server-side. The
    partial reply is kept (persisted as the assistant turn); leaving and
    re-entering the chat will NOT resume it."""
    _own_conversation(conversation_id, user)
    status = chat_job_manager.cancel(conversation_id)
    if status is None:
        raise HTTPException(409, "nothing is generating for this conversation")
    return {"ok": True, **status}


@router.get("/conversations/{conversation_id}/stream")
async def stream_conversation(
    conversation_id: str, user: User = Depends(current_user)
):
    """Re-attach to an in-flight generation: replays everything produced so far
    then streams live tokens. Used when returning to a chat whose reply is
    still being generated. Emits a single 'idle' frame when nothing is running
    (the client then just renders the stored history)."""
    _own_conversation(conversation_id, user)
    job = chat_job_manager.get(conversation_id)
    if job is not None:
        return StreamingResponse(job.subscribe(), media_type="text/event-stream")

    async def idle():
        yield "data: " + json.dumps(
            {"forge": "idle", "conversation_id": conversation_id}
        ) + "\n\n"

    return StreamingResponse(idle(), media_type="text/event-stream")


@router.get("/active")
def active_generations(user: User = Depends(current_user)) -> list[dict]:
    """Which of the caller's conversations are generating right now, so the
    conversation list can badge them live."""
    with read_session() as db:
        owned = {
            c.id
            for c in db.exec(
                select(Conversation).where(Conversation.user_id == user.id)
            ).all()
        }
    return chat_job_manager.active_for(owned)


class ImageBody(BaseModel):
    prompt: str
    conversation_id: str | None = None
    provider: str = "local"  # "local" or an enabled remote connector kind
    size: str = "1024x1024"
    # Incognito: the image is returned inline (data URI) and NOTHING is
    # stored — no Upload row, no file on disk, honoring temporary chat's
    # "stores nothing" promise.
    temporary: bool = False


def _validate_size(size: str) -> None:
    """Mirror the imagegen server's real bounds (256-1536, multiples of 8)
    instead of silently serving a different resolution than requested."""
    match = re.fullmatch(r"(\d{2,4})x(\d{2,4})", size)
    if match is None:
        raise HTTPException(400, "size must look like 1024x1024")
    for dim in map(int, match.groups()):
        if not (256 <= dim <= 1536) or dim % 8:
            raise HTTPException(
                400, "size dimensions must be 256-1536 and multiples of 8"
            )


@router.post("/image")
async def generate_image(body: ImageBody, user: User = Depends(current_user)) -> dict:
    """Generate an image (local imagegen lane or a connector like Higgsfield)
    and, when a conversation is given, record the exchange with the image
    attached to the assistant turn."""
    prompt = body.prompt.strip()
    if not prompt:
        raise HTTPException(400, "prompt required")
    _validate_size(body.size)
    if body.temporary and body.conversation_id:
        raise HTTPException(400, "temporary generation cannot target a conversation")
    conversation = (
        _own_conversation(body.conversation_id, user) if body.conversation_id else None
    )

    data, mime = await image_service.generate(user.id, prompt, body.provider, body.size)

    if body.temporary:
        import base64

        return {
            "upload": None,
            "image_data_uri": f"data:{mime};base64,{base64.b64encode(data).decode()}",
            "conversation_id": None,
            "user_message_id": None,
            "assistant_message_id": None,
        }

    upload = uploads.save_generated(user.id, data, prompt, mime)

    user_message_id: int | None = None
    assistant_message_id: int | None = None
    if conversation is not None:
        with write_session() as db:
            user_message = ChatMessage(
                conversation_id=conversation.id,
                role="user",
                content=prompt,
                token_estimate=memory.estimate_tokens(prompt),
            )
            assistant_message = ChatMessage(
                conversation_id=conversation.id,
                role="assistant",
                content=f"[Generated image: {prompt}]",
                attachments_json=json.dumps([upload.id]),
            )
            db.add(user_message)
            db.add(assistant_message)
            row = db.get(Conversation, conversation.id)
            if row:
                row.updated_at = datetime.now(UTC)
                if row.title == "New chat":
                    # Image-first chats would otherwise never get an
                    # auto-title (the text-exchange gate counts these rows).
                    row.title = " ".join(prompt.split()[:6])[:80]
                db.add(row)
            db.flush()
            user_message_id = user_message.id
            assistant_message_id = assistant_message.id
    return {
        "upload": {
            "id": upload.id,
            "filename": upload.filename,
            "kind": upload.kind,
            "mime": upload.mime,
            "size_bytes": upload.size_bytes,
            "generated": True,
            "prompt": upload.prompt,
        },
        "conversation_id": conversation.id if conversation else None,
        "user_message_id": user_message_id,
        "assistant_message_id": assistant_message_id,
    }


class ReadPageBody(BaseModel):
    url: str
    mode: str = "auto"  # auto | fast | stealth


@router.post("/read_page")
async def read_page(body: ReadPageBody, user: User = Depends(current_user)) -> dict:
    """Read a web page as markdown (Scrapling MCP) and save it as a text
    attachment the composer can attach to messages.

    Request: ``{"url": "https://…", "mode": "auto"}`` — mode "auto" (default)
    tries the fast HTTP fetch and escalates to the stealth browser on failure
    or JS-empty content; "fast" and "stealth" force one lane.

    Response::

        {
          "upload": {id, filename, kind: "text", mime: "text/markdown",
                     size_bytes, generated: true, prompt: <source url>},
          "url": <requested url>,
          "mode_used": "fast" | "stealth",
          "truncated": bool           # content was cut at ~150 KB
        }

    The ``upload`` object matches the attachment-meta shape used across chat
    (pass its ``id`` in a message's ``attachment_ids``). Errors: 400 for a
    bad url/mode, 502 when the scrapling service is unreachable or fails.
    """
    result = await web_reader.read_page(body.url, body.mode)
    upload = uploads.save_generated_text(user.id, result["markdown"], result["url"])
    return {
        "upload": {
            "id": upload.id,
            "filename": upload.filename,
            "kind": upload.kind,
            "mime": upload.mime,
            "size_bytes": upload.size_bytes,
            "generated": True,
            "prompt": upload.prompt,
        },
        "url": result["url"],
        "mode_used": result["mode_used"],
        "truncated": result["truncated"],
    }


@router.post("/temporary")
async def temporary_chat(body: TemporaryBody, user: User = Depends(current_user)):
    """Incognito: streams a reply, stores nothing, reads no memory."""
    if not body.messages:
        raise HTTPException(400, "messages required")
    # Temporary chats store nothing and shouldn't trigger model loads either:
    # "auto" degrades to whatever is already serving (least-loaded lease).
    slug = "" if body.model_slug == model_router.AUTO_SLUG else body.model_slug
    lease = _select_lease(slug)
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
        # Honor the same per-lane slot budget as background jobs so a temporary
        # chat never oversubscribes a lane (above all single-slot AirLLM) that
        # is already busy generating.
        async with chat_job_manager.slot_for(lease):
            async for frame in chat_service.stream_completion(
                lease.base_url, lease.model_slug, messages, collected
            ):
                yield frame
        yield 'data: {"forge": "done", "temporary": true}\n\n'

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.get("/status")
def chat_status(user: User = Depends(current_user)) -> dict:
    """What the chat composer needs: which models are serving right now, and
    whether the "auto" routing option is usable."""
    leases = [lease.as_dict() for lease in engine_manager.ready_text_leases()]
    image_lease = engine_manager.ready_image_lease()
    return {
        "serving": leases,
        "image": image_lease.as_dict() if image_lease else None,
        "auto": {
            # Auto works whenever at least one text model is downloaded; the
            # tiny router model refines the pick when configured and ready.
            "available": bool(model_router.ready_candidates()),
            "router_model": model_router.router_model_slug(),
            "router_ready": model_router.router_model_entry() is not None,
        },
    }
