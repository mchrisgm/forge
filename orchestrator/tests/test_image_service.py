"""Chat image generation service: tool selection heuristics, argument
mapping, image extraction from MCP tool results (inline blocks, then URL
harvesting with content-type checks), connector endpoint resolution against
per-user Connector rows, and provider routing for generate()."""

import base64
import json

import httpx
import pytest
from fastapi import HTTPException
from sqlmodel import select

from app import db as db_module
from app.connector_catalog import CATALOG
from app.models import Connector, EngineKind
from app.services import image_service
from app.services.engine_manager import Lease, engine_manager

from .test_mcp_client import FakeMCPServer

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
PNG_B64 = base64.b64encode(PNG).decode()


def user_id_of(api, headers) -> int:
    return api.get("/api/users/me", headers=headers).json()["id"]


def set_connector(user_id: int, kind: str, enabled: bool = True, config: dict | None = None):
    """Upsert this user's Connector row (registration seeds the catalog kinds)."""
    with db_module.write_session() as db:
        row = db.exec(
            select(Connector).where(
                Connector.user_id == user_id, Connector.kind == kind
            )
        ).first() or Connector(user_id=user_id, kind=kind)
        row.enabled = enabled
        row.config_json = json.dumps(config or {})
        db.add(row)


# ── _pick_tool ──────────────────────────────────────────────────────────────


class TestPickTool:
    def test_prefers_generation_intent_over_listing_order(self):
        tools = [{"name": "image_remix"}, {"name": "generate_image"}]
        assert image_service._pick_tool(tools) == {"name": "generate_image"}

    @pytest.mark.parametrize(
        "name",
        ["generate_image", "text_to_image", "text-to-image", "create_image", "image_generator"],
    )
    def test_generation_style_names_are_preferred(self, name):
        tools = [{"name": "image_remix"}, {"name": name}]
        assert image_service._pick_tool(tools) == {"name": name}

    def test_falls_back_to_any_image_named_tool(self):
        tools = [{"name": "generate_video"}, {"name": "image_remix"}]
        assert image_service._pick_tool(tools) == {"name": "image_remix"}

    @pytest.mark.parametrize(
        "name",
        [
            "edit_image",
            "upscale_image",
            "analyze_image",
            "describe_image",
            "remove_background",
            "generate_image_batch",
            "image_search",
            "show_images",
        ],
    )
    def test_editing_and_analysis_tools_are_never_picked(self, name):
        assert image_service._pick_tool([{"name": name}]) is None

    def test_excluded_preferred_name_falls_through_to_a_clean_fallback(self):
        tools = [{"name": "generate_image_batch"}, {"name": "image_remix"}]
        assert image_service._pick_tool(tools) == {"name": "image_remix"}

    def test_nothing_image_flavored_returns_none(self):
        tools = [{"name": "generate_video"}, {"name": "list_voices"}, {}]
        assert image_service._pick_tool(tools) is None

    def test_empty_tool_list_returns_none(self):
        assert image_service._pick_tool([]) is None


# ── _tool_arguments ─────────────────────────────────────────────────────────


class TestToolArguments:
    @pytest.mark.parametrize("key", ["prompt", "text", "description", "query", "input"])
    def test_maps_to_the_schemas_prompt_flavored_key(self, key):
        tool = {"inputSchema": {"properties": {key: {"type": "string"}}}}
        assert image_service._tool_arguments(tool, "a fox") == {key: "a fox"}

    def test_prompt_wins_when_several_keys_exist(self):
        tool = {
            "inputSchema": {
                "properties": {"text": {}, "prompt": {}, "description": {}}
            }
        }
        assert image_service._tool_arguments(tool, "a fox") == {"prompt": "a fox"}

    def test_defaults_to_prompt_without_a_schema(self):
        assert image_service._tool_arguments({}, "a fox") == {"prompt": "a fox"}

    def test_defaults_to_prompt_for_a_malformed_schema(self):
        assert image_service._tool_arguments(
            {"inputSchema": "not a dict"}, "a fox"
        ) == {"prompt": "a fox"}
        assert image_service._tool_arguments(
            {"inputSchema": {"properties": {"size": {}}}}, "a fox"
        ) == {"prompt": "a fox"}


# ── _iter_urls ──────────────────────────────────────────────────────────────


class TestIterUrls:
    def test_harvests_https_urls_from_nested_structures(self):
        value = {
            "text": "see https://cdn.example.com/a.png now",
            "nested": [{"url": "https://cdn.example.com/b.jpg"}],
            "count": 3,
        }
        assert sorted(image_service._iter_urls(value)) == [
            "https://cdn.example.com/a.png",
            "https://cdn.example.com/b.jpg",
        ]

    def test_ignores_plain_http_and_non_urls(self):
        assert list(image_service._iter_urls("http://insecure.example.com/x.png nope")) == []
        assert list(image_service._iter_urls(None)) == []

    def test_trailing_punctuation_is_not_swallowed(self):
        (url,) = image_service._iter_urls("(https://cdn.example.com/a.png)")
        assert url == "https://cdn.example.com/a.png"


# ── _extract_image ──────────────────────────────────────────────────────────


class TestExtractImage:
    async def test_inline_image_block_wins_without_any_fetch(self, httpx_mock):
        result = {
            "content": [
                {"type": "image", "data": PNG_B64, "mimeType": "image/png"},
                {"type": "text", "text": "also at https://cdn.example.com/x.png"},
            ]
        }
        assert await image_service._extract_image(result) == (PNG, "image/png")
        assert httpx_mock.requests == []

    async def test_inline_block_defaults_to_png_mime(self, httpx_mock):
        result = {"content": [{"type": "image", "data": PNG_B64}]}
        assert await image_service._extract_image(result) == (PNG, "image/png")

    async def test_image_extension_urls_are_tried_before_extensionless(self, httpx_mock):
        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url).endswith(".png"):
                return httpx.Response(200, content=PNG, headers={"Content-Type": "image/png"})
            return httpx.Response(200, text="<html>", headers={"Content-Type": "text/html"})

        httpx_mock.set_handler(handler)
        result = {
            "content": [
                {
                    "type": "text",
                    "text": "https://cdn.example.com/page then https://cdn.example.com/pic.png",
                }
            ]
        }
        assert await image_service._extract_image(result) == (PNG, "image/png")
        assert str(httpx_mock.requests[0].url) == "https://cdn.example.com/pic.png"

    async def test_https_to_http_redirects_are_rejected(self, httpx_mock):
        # The one way a connector reply could point the fetch at plain-http
        # internal services is an https→http redirect — the final URL must
        # still be https or the candidate is dropped.
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.scheme == "https":
                return httpx.Response(
                    302, headers={"Location": "http://internal.local/img"}
                )
            return httpx.Response(
                200, content=PNG, headers={"Content-Type": "image/png"}
            )

        httpx_mock.set_handler(handler)
        result = {
            "content": [{"type": "text", "text": "https://cdn.example.com/gen.png"}]
        }
        with pytest.raises(HTTPException) as excinfo:
            await image_service._extract_image(result)
        assert excinfo.value.status_code == 502

    async def test_structured_content_urls_are_harvested(self, httpx_mock):
        httpx_mock.set_handler(
            lambda request: httpx.Response(
                200, content=PNG, headers={"Content-Type": "image/jpeg"}
            )
        )
        result = {
            "content": [],
            "structuredContent": {"images": [{"url": "https://cdn.example.com/gen"}]},
        }
        # Extension-less CDN URL: the content-type check decides.
        assert await image_service._extract_image(result) == (PNG, "image/jpeg")

    async def test_invalid_inline_base64_falls_back_to_urls(self, httpx_mock):
        httpx_mock.set_handler(
            lambda request: httpx.Response(
                200, content=PNG, headers={"Content-Type": "image/png"}
            )
        )
        result = {
            "content": [
                {"type": "image", "data": "!!!not-base64!!!"},
                {"type": "text", "text": "https://cdn.example.com/x.png"},
            ]
        }
        assert await image_service._extract_image(result) == (PNG, "image/png")

    async def test_non_image_content_types_are_skipped(self, httpx_mock):
        httpx_mock.set_handler(
            lambda request: httpx.Response(
                200, text="<html>", headers={"Content-Type": "text/html"}
            )
        )
        result = {"content": [{"type": "text", "text": "https://cdn.example.com/x.png"}]}
        with pytest.raises(HTTPException) as excinfo:
            await image_service._extract_image(result)
        assert excinfo.value.status_code == 502

    async def test_oversize_downloads_are_rejected(self, httpx_mock, monkeypatch):
        monkeypatch.setattr(image_service, "MAX_RESULT_BYTES", 10)
        httpx_mock.set_handler(
            lambda request: httpx.Response(
                200, content=b"\x89PNG" + b"\x00" * 20, headers={"Content-Type": "image/png"}
            )
        )
        result = {"content": [{"type": "text", "text": "https://cdn.example.com/x.png"}]}
        with pytest.raises(HTTPException) as excinfo:
            await image_service._extract_image(result)
        assert excinfo.value.status_code == 502

    async def test_no_image_anywhere_is_502(self, httpx_mock):
        with pytest.raises(HTTPException) as excinfo:
            await image_service._extract_image({"content": [{"type": "text", "text": "done!"}]})
        assert excinfo.value.status_code == 502
        assert httpx_mock.requests == []


# ── _connector_endpoint ─────────────────────────────────────────────────────


class TestConnectorEndpoint:
    def test_unconfigured_kind_is_409(self, api, auth_headers):
        with pytest.raises(HTTPException) as excinfo:
            image_service._connector_endpoint(user_id_of(api, auth_headers), "custom-ghost")
        assert excinfo.value.status_code == 409
        assert "not enabled" in excinfo.value.detail["message"]

    def test_disabled_connector_is_409(self, api, auth_headers):
        # Registration seeds higgsfield disabled by default.
        with pytest.raises(HTTPException) as excinfo:
            image_service._connector_endpoint(user_id_of(api, auth_headers), "higgsfield")
        assert excinfo.value.status_code == 409

    def test_remote_catalog_entry_yields_url_and_bearer_header(self, api, auth_headers):
        user_id = user_id_of(api, auth_headers)
        set_connector(user_id, "higgsfield", config={"token": "tok-123"})
        url, headers = image_service._connector_endpoint(user_id, "higgsfield")
        assert url == CATALOG["higgsfield"].url
        assert headers == {"Authorization": "Bearer tok-123"}

    def test_local_catalog_entry_is_409(self, api, auth_headers):
        # fetch is a local stdio server and seeded enabled.
        with pytest.raises(HTTPException) as excinfo:
            image_service._connector_endpoint(user_id_of(api, auth_headers), "fetch")
        assert excinfo.value.status_code == 409
        assert "local MCP server" in excinfo.value.detail

    def test_remote_entry_without_url_is_409(self, api, auth_headers):
        # zapier's URL is account-specific; enabled without one cannot be dialed.
        user_id = user_id_of(api, auth_headers)
        set_connector(user_id, "zapier", config={})
        with pytest.raises(HTTPException) as excinfo:
            image_service._connector_endpoint(user_id, "zapier")
        assert excinfo.value.status_code == 409
        assert "no MCP URL" in excinfo.value.detail

    def test_custom_connector_uses_its_mcp_block_verbatim(self, api, auth_headers):
        user_id = user_id_of(api, auth_headers)
        set_connector(
            user_id,
            "custom-my-server",
            config={
                "mcp": {
                    "url": "https://mcp.mine.example/mcp",
                    "headers": {"X-Key": "raw-secret"},
                }
            },
        )
        url, headers = image_service._connector_endpoint(user_id, "custom-my-server")
        assert url == "https://mcp.mine.example/mcp"
        assert headers == {"X-Key": "raw-secret"}

    def test_custom_connector_without_mcp_block_is_404(self, api, auth_headers):
        user_id = user_id_of(api, auth_headers)
        set_connector(user_id, "custom-empty", config={})
        with pytest.raises(HTTPException) as excinfo:
            image_service._connector_endpoint(user_id, "custom-empty")
        assert excinfo.value.status_code == 404

    def test_enabled_row_of_unknown_kind_is_404(self, api, auth_headers):
        user_id = user_id_of(api, auth_headers)
        set_connector(user_id, "mystery", config={})
        with pytest.raises(HTTPException) as excinfo:
            image_service._connector_endpoint(user_id, "mystery")
        assert excinfo.value.status_code == 404

    def test_another_users_connector_does_not_leak(
        self, api, auth_headers, second_user_headers
    ):
        set_connector(
            user_id_of(api, auth_headers), "higgsfield", config={"token": "tok-123"}
        )
        with pytest.raises(HTTPException) as excinfo:
            image_service._connector_endpoint(
                user_id_of(api, second_user_headers), "higgsfield"
            )
        assert excinfo.value.status_code == 409


# ── generate_local + provider routing ───────────────────────────────────────


def image_lease(base_url: str = "http://forge-engine-imagegen-gpu0:8084/v1") -> Lease:
    return Lease(
        model_id=1,
        model_name="SDXL Turbo",
        model_slug="sdxl-turbo",
        engine=EngineKind.imagegen,
        state="ready",
        base_url=base_url,
    )


class TestGenerateLocal:
    @pytest.mark.parametrize("provider", ["", "local"])
    async def test_409_when_no_image_model_is_loaded(self, monkeypatch, provider):
        monkeypatch.setattr(engine_manager, "ready_image_lease", lambda: None)
        with pytest.raises(HTTPException) as excinfo:
            await image_service.generate(1, "a fox", provider, "1024x1024")
        assert excinfo.value.status_code == 409
        assert excinfo.value.detail["message"] == "no image model is loaded"

    async def test_dials_the_lease_with_the_openai_images_contract(
        self, httpx_mock, monkeypatch
    ):
        monkeypatch.setattr(engine_manager, "ready_image_lease", image_lease)
        httpx_mock.set_handler(
            lambda request: httpx.Response(200, json={"data": [{"b64_json": PNG_B64}]})
        )
        data, mime = await image_service.generate(1, "a red fox", "local", "512x512")
        assert (data, mime) == (PNG, "image/png")

        (request,) = httpx_mock.requests
        # lease.base_url already ends in /v1 (see engine_base_url).
        assert str(request.url) == (
            "http://forge-engine-imagegen-gpu0:8084/v1/images/generations"
        )
        assert json.loads(request.content) == {
            "prompt": "a red fox",
            "n": 1,
            "size": "512x512",
            "response_format": "b64_json",
        }

    async def test_engine_error_status_is_502(self, httpx_mock, monkeypatch):
        monkeypatch.setattr(engine_manager, "ready_image_lease", image_lease)
        httpx_mock.set_handler(lambda request: httpx.Response(500, text="cuda oom"))
        with pytest.raises(HTTPException) as excinfo:
            await image_service.generate(1, "a fox", "local", "1024x1024")
        assert excinfo.value.status_code == 502
        assert "cuda oom" in excinfo.value.detail

    async def test_malformed_engine_response_is_502(self, httpx_mock, monkeypatch):
        monkeypatch.setattr(engine_manager, "ready_image_lease", image_lease)
        httpx_mock.set_handler(lambda request: httpx.Response(200, json={"data": []}))
        with pytest.raises(HTTPException) as excinfo:
            await image_service.generate(1, "a fox", "local", "1024x1024")
        assert excinfo.value.status_code == 502

    async def test_unreachable_engine_is_502(self, httpx_mock, monkeypatch):
        monkeypatch.setattr(engine_manager, "ready_image_lease", image_lease)

        def unreachable(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        httpx_mock.set_handler(unreachable)
        with pytest.raises(HTTPException) as excinfo:
            await image_service.generate(1, "a fox", "local", "1024x1024")
        assert excinfo.value.status_code == 502

    async def test_other_providers_route_to_the_connector_path(self, monkeypatch):
        calls: list[tuple] = []

        async def fake_connector(user_id, kind, prompt):
            calls.append((user_id, kind, prompt))
            return b"webp-bytes", "image/webp"

        monkeypatch.setattr(image_service, "generate_via_connector", fake_connector)
        result = await image_service.generate(7, "a fox", "higgsfield", "1024x1024")
        assert result == (b"webp-bytes", "image/webp")
        assert calls == [(7, "higgsfield", "a fox")]


# ── generate_via_connector end-to-end (fake MCP server) ─────────────────────


class TestGenerateViaConnector:
    @pytest.fixture
    def user_id(self, api, auth_headers) -> int:
        user_id = user_id_of(api, auth_headers)
        set_connector(user_id, "higgsfield", config={"token": "tok-123"})
        return user_id

    @pytest.fixture
    def mcp_server(self, httpx_mock) -> FakeMCPServer:
        server = FakeMCPServer()
        server.call_result = {
            "content": [{"type": "image", "data": PNG_B64, "mimeType": "image/png"}]
        }
        httpx_mock.set_handler(server.handle)
        return server

    async def test_picks_a_tool_calls_it_and_extracts_the_image(
        self, user_id, mcp_server
    ):
        data, mime = await image_service.generate_via_connector(
            user_id, "higgsfield", "a red fox"
        )
        assert (data, mime) == (PNG, "image/png")
        # The connector's auth header rode every MCP request.
        assert all(
            request.headers["authorization"] == "Bearer tok-123"
            for request in mcp_server.requests
        )
        call = mcp_server.messages[-1]
        assert call["params"] == {
            "name": "generate_image",
            "arguments": {"prompt": "a red fox"},
        }

    async def test_no_image_tool_is_409_listing_the_tools(self, user_id, mcp_server):
        mcp_server.tools = [{"name": "generate_video"}, {"name": "upscale_image"}]
        with pytest.raises(HTTPException) as excinfo:
            await image_service.generate_via_connector(user_id, "higgsfield", "a fox")
        assert excinfo.value.status_code == 409
        assert "generate_video" in excinfo.value.detail

    async def test_mcp_errors_become_502_naming_the_connector(self, user_id, mcp_server):
        mcp_server.responders["tools/call"] = (
            lambda request_id, payload: httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32000, "message": "generation failed"},
                },
            )
        )
        with pytest.raises(HTTPException) as excinfo:
            await image_service.generate_via_connector(user_id, "higgsfield", "a fox")
        assert excinfo.value.status_code == 502
        assert "higgsfield" in excinfo.value.detail
        assert "generation failed" in excinfo.value.detail
