"""Chat image generation — local diffusers lane or a connector MCP server.

Two providers behind one call:

- ``local``: the imagegen engine lease (engines/imagegen, OpenAI Images API)
  — POST /v1/images/generations with response_format=b64_json.
- any enabled remote connector (e.g. ``higgsfield``): connect with the
  orchestrator-side MCP client, find an image-generation tool, call it, and
  pull the image out of the result (inline image blocks first, then image
  URLs mentioned in text/structured content).

Either way the bytes are returned to the chat router, which persists them as
a generated Upload.
"""

import base64
import json
import logging
import re
from typing import Any

import httpx
from fastapi import HTTPException
from sqlmodel import select

from ..connector_catalog import CATALOG, request_headers
from ..db import read_session
from ..models import Connector
from .engine_manager import engine_manager
from .mcp_client import MCPClient, MCPError

log = logging.getLogger(__name__)

GENERATION_TIMEOUT = 300.0  # diffusion is slow; hosted queues slower
MAX_RESULT_BYTES = 30 * 1024 * 1024

# Tool-name heuristics: exact intent first, then anything image-flavored that
# isn't an editing/analysis tool.
_TOOL_PREFERRED = re.compile(
    r"text.?to.?image|generate.?image|image.?generat|create.?image", re.IGNORECASE
)
_TOOL_FALLBACK = re.compile(r"image", re.IGNORECASE)
_TOOL_EXCLUDE = re.compile(
    r"edit|upscale|variation|analy|describe|caption|background|inpaint|outpaint"
    r"|search|list|show|batch",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"https://[^\s\"'<>()\[\]]+")
_IMAGE_URL_RE = re.compile(r"\.(png|jpe?g|webp|gif)(\?|$)", re.IGNORECASE)


async def generate_local(prompt: str, size: str) -> tuple[bytes, str]:
    """Generate through the local imagegen lease (OpenAI Images API)."""
    lease = engine_manager.ready_image_lease()
    if lease is None:
        raise HTTPException(
            409,
            {
                "message": "no image model is loaded",
                "detail": "Load an image model (imagegen lane) from the Models "
                "page, or pick a connector provider.",
            },
        )
    try:
        async with httpx.AsyncClient(timeout=GENERATION_TIMEOUT) as client:
            # lease.base_url already ends in /v1 (see engine_base_url).
            response = await client.post(
                f"{lease.base_url}/images/generations",
                json={
                    "prompt": prompt,
                    "n": 1,
                    "size": size,
                    "response_format": "b64_json",
                },
            )
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"image engine unreachable: {exc}") from exc
    if response.status_code >= 400:
        raise HTTPException(
            502, f"image engine error {response.status_code}: {response.text[:300]}"
        )
    try:
        b64 = response.json()["data"][0]["b64_json"]
        data = base64.b64decode(b64)
    except (KeyError, IndexError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(502, f"image engine sent a malformed response: {exc}") from exc
    return data, "image/png"


def _connector_endpoint(user_id: int, kind: str) -> tuple[str, dict[str, str]]:
    """(url, headers) for an enabled remote connector owned by this user."""
    with read_session() as db:
        row = db.exec(
            select(Connector).where(
                Connector.user_id == user_id, Connector.kind == kind
            )
        ).first()
    if row is None or not row.enabled:
        raise HTTPException(
            409,
            {
                "message": f"connector {kind!r} is not enabled",
                "detail": "Enable and configure it on the Connectors page first.",
            },
        )
    try:
        config = json.loads(row.config_json or "{}")
    except json.JSONDecodeError:
        config = {}
    entry = CATALOG.get(kind)
    if entry is not None:
        if entry.mcp_type != "remote":
            raise HTTPException(
                409, f"connector {kind!r} is a local MCP server — chat image "
                "generation needs a remote endpoint or the local imagegen lane"
            )
        url = entry.url or str(config.get("url") or "")
        headers = request_headers(entry, config)
    elif kind.startswith("custom-") and isinstance(config.get("mcp"), dict):
        block = config["mcp"]
        url = str(block.get("url") or "")
        headers = {
            str(k): str(v)
            for k, v in (block.get("headers") or {}).items()
            if isinstance(k, str)
        }
    else:
        raise HTTPException(404, f"unknown connector {kind!r}")
    if not url:
        raise HTTPException(409, f"connector {kind!r} has no MCP URL configured")
    return url, headers


def _pick_tool(tools: list[dict[str, Any]]) -> dict[str, Any] | None:
    def usable(tool: dict[str, Any]) -> bool:
        return not _TOOL_EXCLUDE.search(str(tool.get("name", "")))

    for pattern in (_TOOL_PREFERRED, _TOOL_FALLBACK):
        for tool in tools:
            name = str(tool.get("name", ""))
            if pattern.search(name) and usable(tool):
                return tool
    return None


def _tool_arguments(tool: dict[str, Any], prompt: str) -> dict[str, Any]:
    """{prompt: ...} under whatever the tool's schema calls its prompt."""
    schema = tool.get("inputSchema") or {}
    properties = schema.get("properties") if isinstance(schema, dict) else None
    if isinstance(properties, dict):
        for key in ("prompt", "text", "description", "query", "input"):
            if key in properties:
                return {key: prompt}
    return {"prompt": prompt}


def _iter_urls(value: Any):
    """Every https URL mentioned anywhere in a JSON-ish structure."""
    if isinstance(value, str):
        yield from _URL_RE.findall(value)
    elif isinstance(value, dict):
        for v in value.values():
            yield from _iter_urls(v)
    elif isinstance(value, list):
        for v in value:
            yield from _iter_urls(v)


async def _fetch_image_url(client: httpx.AsyncClient, url: str) -> tuple[bytes, str] | None:
    try:
        response = await client.get(url, follow_redirects=True)
    except httpx.HTTPError:
        return None
    content_type = response.headers.get("content-type", "").split(";")[0].strip()
    if response.status_code >= 400 or not content_type.startswith("image/"):
        return None
    if len(response.content) > MAX_RESULT_BYTES:
        return None
    return response.content, content_type


async def _extract_image(result: dict[str, Any]) -> tuple[bytes, str]:
    """Image bytes from an MCP tool result: inline blocks, then linked URLs."""
    content = result.get("content") or []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "image" and block.get("data"):
            try:
                return (
                    base64.b64decode(block["data"]),
                    str(block.get("mimeType") or "image/png"),
                )
            except ValueError:
                continue

    candidates: list[str] = []
    for block in content:
        if isinstance(block, dict):
            candidates.extend(_iter_urls(block.get("text", "")))
    candidates.extend(_iter_urls(result.get("structuredContent")))
    # Obvious image URLs first, then the rest (hosted services often serve
    # images from extension-less CDN URLs — the content-type check decides).
    candidates = sorted(
        dict.fromkeys(candidates),
        key=lambda u: 0 if _IMAGE_URL_RE.search(u) else 1,
    )
    async with httpx.AsyncClient(timeout=60.0) as client:
        for url in candidates[:6]:
            fetched = await _fetch_image_url(client, url)
            if fetched:
                return fetched
    raise HTTPException(
        502,
        "the connector's tool finished but returned no image — its reply had "
        "no inline image and no fetchable image URL",
    )


async def generate_via_connector(
    user_id: int, kind: str, prompt: str
) -> tuple[bytes, str]:
    url, headers = _connector_endpoint(user_id, kind)
    try:
        async with MCPClient(url, headers=headers, timeout=GENERATION_TIMEOUT) as mcp:
            tools = await mcp.list_tools()
            tool = _pick_tool(tools)
            if tool is None:
                names = ", ".join(str(t.get("name", "?")) for t in tools[:20]) or "(none)"
                raise HTTPException(
                    409,
                    f"connector {kind!r} exposes no image-generation tool "
                    f"(tools: {names})",
                )
            result = await mcp.call_tool(
                str(tool["name"]),
                _tool_arguments(tool, prompt),
                timeout=GENERATION_TIMEOUT,
            )
    except MCPError as exc:
        raise HTTPException(502, f"connector {kind!r}: {exc}") from exc
    return await _extract_image(result)


async def generate(
    user_id: int, prompt: str, provider: str, size: str
) -> tuple[bytes, str]:
    if provider in ("", "local"):
        return await generate_local(prompt, size)
    return await generate_via_connector(user_id, provider, prompt)
