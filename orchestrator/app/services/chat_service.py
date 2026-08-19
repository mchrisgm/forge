"""Chat context assembly + streamed completions (the LAN chat section).

Context layout per request, inside FORGE_CHAT_CONTEXT_TOKENS:

  system:  Forge persona
           + the user's personal instructions
           + memory block (retrieved, budgeted — persistent chats only)
  system:  rolling conversation summary (when compression has run)
  ...      recent message tail (newest-first greedy fit)
  user:    the new message (+ inlined text attachments, image parts if the
           serving model has vision)

Streaming: chunks are forwarded to the client as-is while assistant text is
accumulated for persistence; the final SSE frame is a forge.done event.
"""

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from ..config import get_settings
from ..models import ModelEntry, ThinkingLevel, Upload, User
from . import memory, uploads
from .thinking import apply_to_openai_messages, directives_for

log = logging.getLogger(__name__)

PERSONA = (
    "You are Forge, a helpful assistant running privately on the user's own "
    "hardware. Be direct, warm, and concrete. Use markdown when it helps."
)
REPLY_HEADROOM_TOKENS = 1200
ATTACHMENT_BUDGET_TOKENS = 4000


def build_system_prompt(
    user: User, memory_entries: list, summary: str = ""
) -> list[dict[str, Any]]:
    parts = [PERSONA]
    if user.personal_instructions.strip():
        parts.append(
            "The user's standing instructions for you:\n"
            + user.personal_instructions.strip()
        )
    block = memory.render_block(memory_entries)
    if block:
        parts.append(block)
    messages: list[dict[str, Any]] = [{"role": "system", "content": "\n\n".join(parts)}]
    if summary.strip():
        messages.append(
            {
                "role": "system",
                "content": "Summary of the earlier part of this conversation:\n"
                + summary.strip(),
            }
        )
    return messages


def _attachment_parts(
    attachments: list[Upload], model: ModelEntry | None
) -> tuple[str, list[dict[str, Any]]]:
    """(inline text to append, image content parts). Non-vision models get an
    honest placeholder for images instead of silently dropping them."""
    inline_chunks: list[str] = []
    image_parts: list[dict[str, Any]] = []
    budget = ATTACHMENT_BUDGET_TOKENS
    for upload in attachments:
        if upload.kind == "image":
            if model is not None and model.vision:
                uri = uploads.image_data_uri(upload)
                if uri:
                    image_parts.append(
                        {"type": "image_url", "image_url": {"url": uri}}
                    )
                    continue
            inline_chunks.append(
                f"[attached image: {upload.filename} — this model cannot view "
                "images, so describe what you need from it]"
            )
            continue
        text = uploads.text_content(upload)
        if text:
            cost = memory.estimate_tokens(text)
            if cost > budget:
                text = text[: budget * 4]
                cost = budget
            budget -= cost
            inline_chunks.append(
                f"--- attached file: {upload.filename} ---\n{text}\n"
                f"--- end of {upload.filename} ---"
            )
        else:
            inline_chunks.append(
                f"[attached file: {upload.filename} ({upload.mime}, "
                f"{upload.size_bytes} bytes) — contents could not be inlined]"
            )
    return "\n\n".join(inline_chunks), image_parts


def build_user_message(
    content: str, attachments: list[Upload], model: ModelEntry | None
) -> dict[str, Any]:
    inline, image_parts = _attachment_parts(attachments, model)
    text = content if not inline else f"{content}\n\n{inline}"
    if image_parts:
        return {
            "role": "user",
            "content": [{"type": "text", "text": text}, *image_parts],
        }
    return {"role": "user", "content": text}


def fit_history(
    history: list[dict[str, Any]], budget_tokens: int
) -> list[dict[str, Any]]:
    """Greedy newest-first fit; returns oldest-first for the request."""
    chosen: list[dict[str, Any]] = []
    used = 0
    for message in reversed(history):
        content = message.get("content", "")
        text = content if isinstance(content, str) else json.dumps(content)
        cost = memory.estimate_tokens(text) + 4
        if used + cost > budget_tokens and chosen:
            break
        chosen.append(message)
        used += cost
    return list(reversed(chosen))


def assemble(
    user: User,
    history: list[dict[str, Any]],
    new_content: str,
    attachments: list[Upload],
    model: ModelEntry | None,
    thinking: ThinkingLevel,
    memory_entries: list,
    summary: str = "",
) -> list[dict[str, Any]]:
    settings = get_settings()
    system_messages = build_system_prompt(user, memory_entries, summary)
    user_message = build_user_message(new_content, attachments, model)

    fixed_cost = sum(
        memory.estimate_tokens(str(m.get("content", ""))) for m in system_messages
    ) + memory.estimate_tokens(str(user_message.get("content", "")))
    history_budget = max(
        400, settings.chat_context_tokens - fixed_cost - REPLY_HEADROOM_TOKENS
    )
    messages = [*system_messages, *fit_history(history, history_budget), user_message]

    if model is not None and thinking != ThinkingLevel.auto:
        messages = apply_to_openai_messages(messages, directives_for(model, thinking))
    return messages


async def stream_completion(
    base_url: str,
    model_slug: str,
    messages: list[dict[str, Any]],
    collected: list[str],
) -> AsyncIterator[str]:
    """Yield upstream SSE frames verbatim while accumulating assistant text
    into `collected` (joined by the caller after the stream ends)."""
    body = {"model": model_slug, "messages": messages, "stream": True}
    timeout = httpx.Timeout(connect=10, read=None, write=30, pool=10)
    async with httpx.AsyncClient(timeout=timeout) as http:
        async with http.stream(
            "POST", f"{base_url}/chat/completions", json=body
        ) as resp:
            if resp.status_code != 200:
                detail = (await resp.aread()).decode(errors="replace")[:400]
                yield (
                    "data: "
                    + json.dumps({"error": f"engine error {resp.status_code}: {detail}"})
                    + "\n\n"
                )
                return
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                yield f"data: {payload}\n\n"
                if payload == "[DONE]":
                    break
                try:
                    delta = json.loads(payload)["choices"][0].get("delta", {})
                    piece = delta.get("content")
                    if piece:
                        collected.append(piece)
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
