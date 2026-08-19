"""Chat "read this page" — fetch a URL as markdown through the Scrapling MCP
service (compose service ``mcp-scrapling``, streamable HTTP, no auth).

Two lanes behind one call:

- ``fast``: the ``get`` tool — an impersonated plain HTTP request with
  markdown extraction. Cheap, but only works on low/mid-protection pages.
- ``stealth``: the ``stealthy_fetch`` tool — a hardened headless browser that
  renders JS and solves Cloudflare Turnstile/Interstitial challenges.

``mode="auto"`` (the default) tries ``fast`` first and escalates to
``stealth`` when the fetch fails, comes back with an error status, or returns
suspiciously little markdown from a 200 (the signature of a JS-only page).
Both tools return Scrapling's ResponseModel — ``{status, content: [...],
url}`` — as MCP structured output; ``_parse_result`` also accepts the JSON
text-block serialization older transports produce.

Failures surface as HTTPException (400 for caller mistakes, 502 when the
scrapling service is unreachable or errors), mirroring image_service.
"""

import json
import logging
import re
from typing import Any
from urllib.parse import urlsplit

from fastapi import HTTPException

from ..config import get_settings
from .mcp_client import MCPClient, MCPError

log = logging.getLogger(__name__)

MODES = ("auto", "fast", "stealth")
MAX_URL_CHARS = 2048
# Returned-content cap (~150KB) — pages beyond it are cut with a marker.
MAX_CONTENT_CHARS = 150_000
TRUNCATION_MARKER = "\n\n… [truncated: page content exceeded the 150 KB limit]"
# A 200 whose extracted markdown is this short is almost certainly a JS shell
# ("enable JavaScript", empty <body>) — auto mode escalates to the browser.
MIN_USEFUL_CHARS = 200
FAST_TIMEOUT = 60.0
STEALTH_TIMEOUT = 240.0  # browser start + render + possible Cloudflare solve

_TITLE_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)


def _validate_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        raise HTTPException(400, "url required")
    if len(url) > MAX_URL_CHARS:
        raise HTTPException(400, f"url exceeds {MAX_URL_CHARS} characters")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise HTTPException(400, "url must be http(s)")
    return url


def _parse_result(result: dict[str, Any]) -> tuple[int | None, str]:
    """(http_status, markdown) from a scrapling fetch-tool result.

    The fetch tools are registered with structured output, so the
    ResponseModel arrives in ``structuredContent``; fall back to text content
    blocks (which may carry the same model serialized as JSON, or plain
    text)."""

    def from_model(model: Any) -> tuple[int | None, str] | None:
        if not isinstance(model, dict):
            return None
        content = model.get("content")
        if not isinstance(content, list):
            return None
        status = model.get("status")
        text = "\n".join(str(chunk) for chunk in content)
        return (status if isinstance(status, int) else None), text

    parsed = from_model(result.get("structuredContent"))
    if parsed is not None:
        return parsed
    texts = [
        block.get("text", "")
        for block in result.get("content", [])
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    joined = "\n".join(text for text in texts if text)
    try:
        parsed = from_model(json.loads(joined))
        if parsed is not None:
            return parsed
    except json.JSONDecodeError:
        pass
    return None, joined


def _looks_empty(status: int | None, markdown: str) -> bool:
    """Whether a fast-lane result warrants escalating to the browser."""
    if status is not None and status >= 400:
        return True
    return len(markdown.strip()) < MIN_USEFUL_CHARS


def _title_of(markdown: str) -> str:
    match = _TITLE_RE.search(markdown)
    return match.group(1).strip() if match else ""


def _fetch_arguments(url: str) -> dict[str, Any]:
    return {"url": url, "extraction_type": "markdown", "main_content_only": True}


async def read_page(url: str, mode: str = "auto") -> dict:
    """Read one web page as markdown via the Scrapling MCP service.

    Returns ``{url, mode_used, title, markdown, truncated}`` where mode_used
    is the lane that produced the content ("fast" or "stealth")."""
    url = _validate_url(url)
    if mode not in MODES:
        raise HTTPException(400, f"mode must be one of {', '.join(MODES)}")
    settings = get_settings()

    try:
        async with MCPClient(settings.scrapling_mcp_url, timeout=STEALTH_TIMEOUT) as mcp:
            markdown: str | None = None
            mode_used = "fast"
            if mode in ("auto", "fast"):
                try:
                    result = await mcp.call_tool(
                        "get", _fetch_arguments(url), timeout=FAST_TIMEOUT
                    )
                    status, markdown = _parse_result(result)
                    if mode == "auto" and _looks_empty(status, markdown):
                        log.info(
                            "fast fetch of %s looked empty/blocked (status=%s, "
                            "%d chars) — escalating to stealth",
                            url, status, len(markdown),
                        )
                        markdown = None
                except MCPError as exc:
                    if mode == "fast":
                        raise
                    log.info("fast fetch of %s failed (%s) — escalating to stealth", url, exc)
            if markdown is None:  # stealth forced, or auto escalation
                result = await mcp.call_tool(
                    "stealthy_fetch",
                    {**_fetch_arguments(url), "solve_cloudflare": True},
                    timeout=STEALTH_TIMEOUT,
                )
                _status, markdown = _parse_result(result)
                mode_used = "stealth"
    except MCPError as exc:
        raise HTTPException(
            502,
            f"could not read the page — the scrapling web-reader service "
            f"failed: {exc}",
        ) from exc

    truncated = len(markdown) > MAX_CONTENT_CHARS
    if truncated:
        markdown = markdown[:MAX_CONTENT_CHARS] + TRUNCATION_MARKER
    return {
        "url": url,
        "mode_used": mode_used,
        "title": _title_of(markdown),
        "markdown": markdown,
        "truncated": truncated,
    }
