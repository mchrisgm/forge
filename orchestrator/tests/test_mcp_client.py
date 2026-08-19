"""The orchestrator-side MCP client (streamable HTTP transport): SSE response
parsing, the initialize/session-id handshake, header propagation, and the
error surface (HTTP failures, JSON-RPC error objects, isError tool results) —
all against an in-process fake MCP server behind httpx.MockTransport."""

import json

import httpx
import pytest

from app.services.mcp_client import (
    PROTOCOL_VERSION,
    MCPClient,
    MCPError,
    _parse_sse_response,
)

MCP_URL = "https://mcp.example.com/mcp"

GENERATE_TOOL = {
    "name": "generate_image",
    "description": "Render an image from a text prompt.",
    "inputSchema": {"type": "object", "properties": {"prompt": {"type": "string"}}},
}


def sse_body(message: dict) -> str:
    return f"event: message\ndata: {json.dumps(message)}\n\n"


class FakeMCPServer:
    """Speaks just enough streamable-HTTP MCP for MCPClient: JSON-RPC dispatch
    by method, request recording, and per-method overrides for failure modes.
    Reused by the image-service connector tests."""

    def __init__(self) -> None:
        self.session_id: str | None = "sess-123"
        self.initialize_sse = False
        self.tools: list = [GENERATE_TOOL]
        self.call_result: dict = {
            "content": [{"type": "text", "text": "https://cdn.example.com/out.png"}]
        }
        self.status_for: dict[str, int] = {}  # method -> HTTP status override
        self.responders: dict = {}  # method -> fn(request_id, payload) -> Response
        self.requests: list[httpx.Request] = []
        self.messages: list[dict] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        payload = json.loads(request.content)
        self.messages.append(payload)
        method = payload.get("method", "")
        if method in self.status_for:
            return httpx.Response(self.status_for[method], text="denied")
        if "id" not in payload:  # a notification: 202 Accepted, no body
            return httpx.Response(202)
        request_id = payload["id"]
        if method in self.responders:
            return self.responders[method](request_id, payload)
        if method == "initialize":
            message = {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"serverInfo": {"name": "fake-mcp"}, "capabilities": {}},
            }
            headers = {"Mcp-Session-Id": self.session_id} if self.session_id else {}
            if self.initialize_sse:
                return httpx.Response(
                    200,
                    text=sse_body(message),
                    headers={**headers, "Content-Type": "text/event-stream"},
                )
            return httpx.Response(200, json=message, headers=headers)
        if method == "tools/list":
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": request_id, "result": {"tools": self.tools}},
            )
        if method == "tools/call":
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": request_id, "result": self.call_result},
            )
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"unknown method {method}"},
            },
        )

@pytest.fixture
def mcp_server(httpx_mock) -> FakeMCPServer:
    server = FakeMCPServer()
    httpx_mock.set_handler(server.handle)
    return server


# ── SSE parsing ─────────────────────────────────────────────────────────────


class TestParseSseResponse:
    def test_finds_the_response_by_id_among_multiple_events(self):
        body = (
            ": keep-alive comment\n\n"
            + sse_body({"jsonrpc": "2.0", "id": 7, "result": {"other": True}})
            + "data: not json at all\n\n"
            + sse_body({"jsonrpc": "2.0", "id": 8, "result": {"mine": True}})
        )
        assert _parse_sse_response(body, 8)["result"] == {"mine": True}

    def test_multi_line_data_chunks_are_joined(self):
        body = 'data: {"id": 3,\ndata: "result": {"ok": true}}\n\n'
        assert _parse_sse_response(body, 3)["result"] == {"ok": True}

    def test_crlf_line_endings_parse(self):
        # SSE permits CRLF; a ping event ahead of the response must not hide it.
        message = {"jsonrpc": "2.0", "id": 3, "result": {"ok": True}}
        body = f"event: ping\r\ndata: {{}}\r\n\r\ndata: {json.dumps(message)}\r\n\r\n"
        assert _parse_sse_response(body, 3)["result"] == {"ok": True}

    def test_missing_id_raises(self):
        body = sse_body({"jsonrpc": "2.0", "id": 1, "result": {}})
        with pytest.raises(MCPError, match="no JSON-RPC response"):
            _parse_sse_response(body, 2)

    def test_empty_body_raises(self):
        with pytest.raises(MCPError):
            _parse_sse_response("", 1)


# ── initialize handshake ────────────────────────────────────────────────────


class TestInitialize:
    async def test_json_initialize_captures_and_echoes_the_session_id(self, mcp_server):
        async with MCPClient(MCP_URL) as mcp:
            tools = await mcp.list_tools()
        assert [m.get("method") for m in mcp_server.messages] == [
            "initialize",
            "notifications/initialized",
            "tools/list",
        ]
        assert tools == [GENERATE_TOOL]

        initialize, notify, tools_list = mcp_server.requests
        assert initialize.headers["accept"] == "application/json, text/event-stream"
        assert initialize.headers["mcp-protocol-version"] == PROTOCOL_VERSION
        assert "mcp-session-id" not in initialize.headers
        # Every request after initialize echoes the server's session id.
        assert notify.headers["mcp-session-id"] == "sess-123"
        assert tools_list.headers["mcp-session-id"] == "sess-123"

    async def test_custom_headers_ride_every_request(self, mcp_server):
        async with MCPClient(MCP_URL, headers={"Authorization": "Bearer tok-1"}) as mcp:
            await mcp.list_tools()
        assert all(
            request.headers["authorization"] == "Bearer tok-1"
            for request in mcp_server.requests
        )

    async def test_sse_initialize_response_is_parsed(self, mcp_server):
        mcp_server.initialize_sse = True
        async with MCPClient(MCP_URL) as mcp:
            tools = await mcp.list_tools()
        assert tools == [GENERATE_TOOL]
        assert mcp_server.requests[-1].headers["mcp-session-id"] == "sess-123"

    async def test_initialize_returns_the_server_result(self, mcp_server):
        client = MCPClient(MCP_URL)
        try:
            result = await client.initialize()
        finally:
            await client.close()
        assert result["serverInfo"] == {"name": "fake-mcp"}

    async def test_session_id_is_optional(self, mcp_server):
        mcp_server.session_id = None
        async with MCPClient(MCP_URL) as mcp:
            await mcp.list_tools()
        assert all(
            "mcp-session-id" not in request.headers for request in mcp_server.requests
        )

    async def test_401_mentions_credentials(self, mcp_server):
        mcp_server.status_for["initialize"] = 401
        client = MCPClient(MCP_URL)
        try:
            with pytest.raises(MCPError, match="credentials"):
                await client.initialize()
        finally:
            await client.close()

    async def test_http_error_surfaces_the_status(self, mcp_server):
        mcp_server.status_for["initialize"] = 503
        client = MCPClient(MCP_URL)
        try:
            with pytest.raises(MCPError, match="HTTP 503"):
                await client.initialize()
        finally:
            await client.close()

    async def test_non_json_initialize_body_is_wrapped(self, mcp_server):
        # A custom connector URL pointing at a non-MCP endpoint that answers
        # 200 HTML must surface as MCPError (→ friendly 502), never a raw
        # JSONDecodeError (→ 500).
        mcp_server.responders["initialize"] = lambda rid, payload: httpx.Response(
            200, text="<html>login</html>", headers={"Content-Type": "text/html"}
        )
        with pytest.raises(MCPError, match="non-JSON initialize"):
            async with MCPClient(MCP_URL):
                pass

    async def test_missing_result_raises(self, mcp_server):
        mcp_server.responders["initialize"] = lambda request_id, payload: httpx.Response(
            200, json={"jsonrpc": "2.0", "id": request_id}
        )
        client = MCPClient(MCP_URL)
        try:
            with pytest.raises(MCPError, match="no result"):
                await client.initialize()
        finally:
            await client.close()


# ── tools/list and tools/call ───────────────────────────────────────────────


class TestListTools:
    async def test_non_dict_entries_are_dropped(self, mcp_server):
        mcp_server.tools = [GENERATE_TOOL, "junk", 42]
        async with MCPClient(MCP_URL) as mcp:
            assert await mcp.list_tools() == [GENERATE_TOOL]

    async def test_missing_tools_array_raises(self, mcp_server):
        mcp_server.responders["tools/list"] = (
            lambda request_id, payload: httpx.Response(
                200, json={"jsonrpc": "2.0", "id": request_id, "result": {}}
            )
        )
        async with MCPClient(MCP_URL) as mcp:
            with pytest.raises(MCPError, match="no tools array"):
                await mcp.list_tools()

    async def test_sse_rpc_response_is_parsed(self, mcp_server):
        mcp_server.responders["tools/list"] = (
            lambda request_id, payload: httpx.Response(
                200,
                text=sse_body(
                    {"jsonrpc": "2.0", "id": request_id, "result": {"tools": [GENERATE_TOOL]}}
                ),
                headers={"Content-Type": "text/event-stream"},
            )
        )
        async with MCPClient(MCP_URL) as mcp:
            assert await mcp.list_tools() == [GENERATE_TOOL]

    async def test_401_after_initialize_mentions_credentials(self, mcp_server):
        mcp_server.status_for["tools/list"] = 401
        async with MCPClient(MCP_URL) as mcp:
            with pytest.raises(MCPError, match="credentials"):
                await mcp.list_tools()


class TestCallTool:
    async def test_happy_path_forwards_name_and_arguments(self, mcp_server):
        mcp_server.call_result = {"content": [{"type": "text", "text": "done"}]}
        async with MCPClient(MCP_URL) as mcp:
            result = await mcp.call_tool("generate_image", {"prompt": "a fox"})
        assert result == mcp_server.call_result
        call = mcp_server.messages[-1]
        assert call["method"] == "tools/call"
        assert call["params"] == {
            "name": "generate_image",
            "arguments": {"prompt": "a fox"},
        }

    async def test_is_error_result_surfaces_the_text(self, mcp_server):
        mcp_server.call_result = {
            "isError": True,
            "content": [
                {"type": "text", "text": "quota exhausted"},
                {"type": "image", "data": "ignored"},
            ],
        }
        async with MCPClient(MCP_URL) as mcp:
            with pytest.raises(MCPError, match="quota exhausted"):
                await mcp.call_tool("generate_image", {"prompt": "a fox"})

    async def test_is_error_without_text_still_raises(self, mcp_server):
        mcp_server.call_result = {"isError": True, "content": []}
        async with MCPClient(MCP_URL) as mcp:
            with pytest.raises(MCPError, match=r"\(no detail\)"):
                await mcp.call_tool("generate_image", {"prompt": "a fox"})

    async def test_jsonrpc_error_object_raises(self, mcp_server):
        mcp_server.responders["tools/call"] = (
            lambda request_id, payload: httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32000, "message": "boom"},
                },
            )
        )
        async with MCPClient(MCP_URL) as mcp:
            with pytest.raises(MCPError, match="MCP error -32000: boom"):
                await mcp.call_tool("generate_image", {"prompt": "a fox"})

    async def test_non_json_response_raises(self, mcp_server):
        mcp_server.responders["tools/call"] = lambda request_id, payload: httpx.Response(
            200, text="<html>gateway error</html>"
        )
        async with MCPClient(MCP_URL) as mcp:
            with pytest.raises(MCPError, match="non-JSON"):
                await mcp.call_tool("generate_image", {"prompt": "a fox"})

    async def test_non_dict_result_raises(self, mcp_server):
        mcp_server.responders["tools/call"] = lambda request_id, payload: httpx.Response(
            200, json={"jsonrpc": "2.0", "id": request_id, "result": "nope"}
        )
        async with MCPClient(MCP_URL) as mcp:
            with pytest.raises(MCPError, match="missing its result"):
                await mcp.call_tool("generate_image", {"prompt": "a fox"})

    async def test_transport_failure_is_wrapped(self, httpx_mock, mcp_server):
        async with MCPClient(MCP_URL) as mcp:

            def unreachable(request: httpx.Request) -> httpx.Response:
                raise httpx.ConnectError("connection refused")

            httpx_mock.set_handler(unreachable)
            with pytest.raises(MCPError, match="unreachable"):
                await mcp.call_tool("generate_image", {"prompt": "a fox"})
