"""Minimal MCP client (streamable HTTP transport) for orchestrator-side calls.

Session containers speak MCP through OpenCode; this client exists for the few
places the ORCHESTRATOR itself needs a connector — today, chat image
generation through a remote server like Higgsfield. It implements just the
slice of the 2025-03-26 streamable-HTTP transport that requires:

- POST JSON-RPC to the server URL with ``Accept: application/json,
  text/event-stream``; the response is either a plain JSON body or an SSE
  stream carrying the response message.
- ``initialize`` → capture the ``Mcp-Session-Id`` response header and echo it
  on every later request; then fire the ``notifications/initialized`` note.
- ``tools/list`` / ``tools/call``.

No sampling, no resources, no server-initiated requests — tool errors and
protocol violations surface as MCPError.
"""

import json
import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)

PROTOCOL_VERSION = "2025-03-26"


class MCPError(RuntimeError):
    pass


def _parse_sse_response(text: str, request_id: int) -> dict[str, Any]:
    """Extract the JSON-RPC response with `request_id` from an SSE body."""
    # The SSE spec allows CRLF (and bare CR) line endings — normalize first.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    for chunk in text.split("\n\n"):
        data_lines = [
            line[5:].lstrip()
            for line in chunk.splitlines()
            if line.startswith("data:")
        ]
        if not data_lines:
            continue
        try:
            message = json.loads("\n".join(data_lines))
        except json.JSONDecodeError:
            continue
        if isinstance(message, dict) and message.get("id") == request_id:
            return message
    raise MCPError("no JSON-RPC response found in the server's SSE stream")


class MCPClient:
    """One connection to a streamable-HTTP MCP server.

    Usage::

        async with MCPClient(url, headers={...}) as mcp:
            tools = await mcp.list_tools()
            result = await mcp.call_tool("generate_image", {"prompt": "..."})
    """

    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.url = url
        self._headers = dict(headers or {})
        self._session_id: str | None = None
        self._request_id = 0
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=15.0))

    async def __aenter__(self) -> "MCPClient":
        await self.initialize()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    def _base_headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
            **self._headers,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    async def _post(self, payload: dict[str, Any], timeout: float | None = None) -> httpx.Response:
        try:
            return await self._client.post(
                self.url,
                content=json.dumps(payload),
                headers=self._base_headers(),
                timeout=timeout,
            )
        except httpx.HTTPError as exc:
            raise MCPError(f"MCP server unreachable: {exc}") from exc

    async def _rpc(
        self, method: str, params: dict[str, Any] | None = None, timeout: float | None = None
    ) -> dict[str, Any]:
        self._request_id += 1
        request_id = self._request_id
        response = await self._post(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params or {},
            },
            timeout=timeout,
        )
        if response.status_code == 401:
            raise MCPError(
                "MCP server rejected the credentials (401) — check the "
                "connector's token"
            )
        if response.status_code >= 400:
            raise MCPError(
                f"MCP server returned HTTP {response.status_code}: "
                f"{response.text[:300]}"
            )
        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            message = _parse_sse_response(response.text, request_id)
        else:
            try:
                message = response.json()
            except json.JSONDecodeError as exc:
                raise MCPError(f"MCP server sent a non-JSON response: {exc}") from exc
        if not isinstance(message, dict):
            raise MCPError("MCP server sent a malformed JSON-RPC response")
        if message.get("error"):
            error = message["error"]
            raise MCPError(
                f"MCP error {error.get('code')}: {error.get('message', 'unknown')}"
            )
        result = message.get("result")
        if not isinstance(result, dict):
            raise MCPError("MCP response is missing its result object")
        return result

    async def _notify(self, method: str) -> None:
        response = await self._post({"jsonrpc": "2.0", "method": method})
        # 202 Accepted is the spec response; anything <400 is close enough.
        if response.status_code >= 400:
            log.warning("MCP notification %s got HTTP %s", method, response.status_code)

    async def initialize(self) -> dict[str, Any]:
        self._request_id += 1
        request_id = self._request_id
        response = await self._post(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "forge-orchestrator", "version": "1.0"},
                },
            }
        )
        if response.status_code == 401:
            raise MCPError(
                "MCP server rejected the credentials (401) — check the "
                "connector's token"
            )
        if response.status_code >= 400:
            raise MCPError(
                f"MCP initialize failed with HTTP {response.status_code}: "
                f"{response.text[:300]}"
            )
        self._session_id = response.headers.get("mcp-session-id") or None
        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            message = _parse_sse_response(response.text, request_id)
        else:
            try:
                message = response.json()
            except json.JSONDecodeError as exc:
                raise MCPError(
                    f"MCP server sent a non-JSON initialize response: {exc}"
                ) from exc
        if not isinstance(message, dict) or "result" not in message:
            raise MCPError("MCP initialize returned no result")
        await self._notify("notifications/initialized")
        return message["result"]

    async def list_tools(self) -> list[dict[str, Any]]:
        result = await self._rpc("tools/list")
        tools = result.get("tools")
        if not isinstance(tools, list):
            raise MCPError("tools/list returned no tools array")
        return [tool for tool in tools if isinstance(tool, dict)]

    async def call_tool(
        self, name: str, arguments: dict[str, Any], timeout: float | None = None
    ) -> dict[str, Any]:
        result = await self._rpc(
            "tools/call", {"name": name, "arguments": arguments}, timeout=timeout
        )
        if result.get("isError"):
            texts = [
                block.get("text", "")
                for block in result.get("content", [])
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            raise MCPError(
                f"tool {name} failed: " + (" ".join(texts).strip() or "(no detail)")
            )
        return result
