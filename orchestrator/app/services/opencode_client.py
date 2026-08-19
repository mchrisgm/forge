"""Thin async client for the OpenCode server API (PLAN §6.3, §14).

This is the single integration point with OpenCode's HTTP surface; the API
shapes assumed here are pinned by tests/test_opencode_client_shapes.py and by
the OpenCode version pinned in session-runner/Dockerfile. Everything else in
the orchestrator goes through these helpers or the generic reverse proxy.

Assumed surface (verified against sst/opencode source — PromptInput schema):
  GET  /session                      -> [ {id, title, ...} ]
  POST /session {title?}             -> {id, ...}
  GET  /session/{id}/message         -> [ {info: {...}, parts: [...]}, ... ]
  POST /session/{id}/message
       {model: {providerID, modelID}, parts: [{type:"text", text}]}
                                     -> assistant message (blocks until turn done)
  POST /session/{id}/prompt_async    -> same body, fire-and-forget
  POST /session/{id}/abort           -> boolean
  POST /session/{id}/permissions/{permissionID}
       {response: "once"|"always"|"reject"}
  GET  /event                        -> SSE stream of {type, properties} events
"""

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

log = logging.getLogger(__name__)

PROMPT_TIMEOUT_S = 3600


async def create_session(base_url: str, title: str = "") -> str:
    async with httpx.AsyncClient(timeout=30) as http:
        resp = await http.post(f"{base_url}/session", json={"title": title} if title else {})
        resp.raise_for_status()
        return resp.json()["id"]


async def list_messages(base_url: str, oc_session_id: str) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=30) as http:
        resp = await http.get(f"{base_url}/session/{oc_session_id}/message")
        resp.raise_for_status()
        return resp.json()


async def send_prompt(
    base_url: str,
    oc_session_id: str,
    text: str,
    provider_id: str,
    model_id: str,
    system: str | None = None,
) -> dict[str, Any]:
    """Send a prompt and wait for the agent turn to finish (long poll).
    `system` (PromptInput.system) carries e.g. thinking-level directives."""
    body: dict[str, Any] = {
        "model": {"providerID": provider_id, "modelID": model_id},
        "parts": [{"type": "text", "text": text}],
    }
    if system:
        body["system"] = system
    async with httpx.AsyncClient(timeout=PROMPT_TIMEOUT_S) as http:
        resp = await http.post(f"{base_url}/session/{oc_session_id}/message", json=body)
        resp.raise_for_status()
        return resp.json()


async def abort(base_url: str, oc_session_id: str) -> None:
    async with httpx.AsyncClient(timeout=15) as http:
        await http.post(f"{base_url}/session/{oc_session_id}/abort")


async def respond_permission(
    base_url: str, oc_session_id: str, permission_id: str, response: str
) -> None:
    async with httpx.AsyncClient(timeout=15) as http:
        resp = await http.post(
            f"{base_url}/session/{oc_session_id}/permissions/{permission_id}",
            json={"response": response},
        )
        resp.raise_for_status()


async def event_stream(base_url: str) -> AsyncIterator[dict[str, Any]]:
    """Yield parsed events from OpenCode's global SSE stream."""
    timeout = httpx.Timeout(connect=10, read=None, write=10, pool=10)
    async with httpx.AsyncClient(timeout=timeout) as http:
        async with http.stream("GET", f"{base_url}/event") as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload:
                    continue
                try:
                    yield json.loads(payload)
                except json.JSONDecodeError:
                    log.debug("unparseable opencode event: %r", payload[:200])


def extract_text(message: dict[str, Any]) -> str:
    """Pull the assistant text out of an OpenCode message response."""
    parts = message.get("parts") or []
    texts = [p.get("text", "") for p in parts if p.get("type") == "text"]
    return "\n".join(t for t in texts if t)
