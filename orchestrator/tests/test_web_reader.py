"""web_reader.read_page against a fake Scrapling MCP server: the fast lane,
auto-escalation to the stealth browser (tool failure, HTTP error status, and
JS-empty 200s), forced modes, result parsing (structured output and text-block
fallbacks), the ~150KB content cap, URL/mode validation, and the 502 surface
when the scrapling service is unreachable."""

import json

import httpx
import pytest
from fastapi import HTTPException

from app.services import web_reader
from app.services import web_reader as _wr
from app.services.web_reader import (
    MAX_CONTENT_CHARS,
    MAX_URL_CHARS,
    TRUNCATION_MARKER,
    read_page,
)
from tests.test_mcp_client import FakeMCPServer


class TestUrlSsrfGuard:
    @pytest.mark.parametrize(
        "url",
        [
            "http://orchestrator:8000/v1-direct/models",  # bare service name
            "http://smolvm:9000/api/v1/machines",
            "http://localhost/x",
            "http://127.0.0.1:8000/api/health",
            "http://169.254.169.254/latest/meta-data/",  # cloud IMDS
            "http://192.168.1.10/admin",
            "http://10.0.0.5/x",
            "http://[::1]/x",
            "http://host.internal/x",
        ],
    )
    def test_internal_and_private_hosts_are_refused(self, url):
        with pytest.raises(HTTPException) as exc:
            _wr._validate_url(url)
        assert exc.value.status_code == 400

    def test_public_hosts_pass(self, monkeypatch):
        # Stub DNS so the test doesn't hit the network; 93.184.216.34 is public.
        monkeypatch.setattr(
            _wr.socket,
            "getaddrinfo",
            lambda host, port: [(2, 1, 6, "", ("93.184.216.34", 0))],
        )
        assert _wr._validate_url("https://example.com/page") == (
            "https://example.com/page"
        )

    def test_public_name_resolving_internally_is_refused(self, monkeypatch):
        # DNS-rebinding: a public-looking name whose A record is private.
        monkeypatch.setattr(
            _wr.socket,
            "getaddrinfo",
            lambda host, port: [(2, 1, 6, "", ("192.168.0.9", 0))],
        )
        with pytest.raises(HTTPException):
            _wr._validate_url("https://sneaky.example.com/x")

URL = "https://example.com/article"
LONG_MD = "# Example Domain\n\n" + "This paragraph carries real page content. " * 20
STEALTH_MD = "# Rendered By Browser\n\n" + "Content only the browser could see. " * 20


def fetch_result(status: int = 200, markdown: str = LONG_MD, url: str = URL) -> dict:
    """A scrapling fetch-tool result: ResponseModel as structured output plus
    its JSON serialization as a text block (what the server actually sends)."""
    model = {"status": status, "content": [markdown], "url": url}
    return {
        "content": [{"type": "text", "text": json.dumps(model)}],
        "structuredContent": model,
    }


TOOL_ERROR = {"isError": True, "content": [{"type": "text", "text": "fetch blew up"}]}


class ScraplingFake:
    """Routes tools/call by tool name on top of FakeMCPServer."""

    def __init__(self, server: FakeMCPServer) -> None:
        self.server = server
        self.results: dict[str, dict] = {}
        self.calls: list[tuple[str, dict]] = []
        server.responders["tools/call"] = self._respond

    def _respond(self, request_id: int, payload: dict) -> httpx.Response:
        name = payload["params"]["name"]
        self.calls.append((name, payload["params"]["arguments"]))
        result = self.results[name]
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": request_id, "result": result}
        )

    def called_tools(self) -> list[str]:
        return [name for name, _ in self.calls]


@pytest.fixture
def scrapling(httpx_mock) -> ScraplingFake:
    server = FakeMCPServer()
    httpx_mock.set_handler(server.handle)
    return ScraplingFake(server)


# ── happy paths ─────────────────────────────────────────────────────────────


class TestFastLane:
    async def test_auto_uses_get_and_returns_the_markdown(self, scrapling):
        scrapling.results["get"] = fetch_result()
        page = await read_page(URL)
        assert page == {
            "url": URL,
            "mode_used": "fast",
            "title": "Example Domain",
            "markdown": LONG_MD,
            "truncated": False,
        }
        assert scrapling.called_tools() == ["get"]

    async def test_get_is_asked_for_main_content_markdown(self, scrapling):
        scrapling.results["get"] = fetch_result()
        await read_page(URL)
        (name, arguments) = scrapling.calls[0]
        assert name == "get"
        assert arguments["url"] == URL
        assert arguments["extraction_type"] == "markdown"
        assert arguments["main_content_only"] is True

    async def test_title_is_empty_without_a_heading(self, scrapling):
        scrapling.results["get"] = fetch_result(markdown="plain text " * 40)
        page = await read_page(URL)
        assert page["title"] == ""

    async def test_url_is_trimmed_before_use(self, scrapling):
        scrapling.results["get"] = fetch_result()
        page = await read_page(f"  {URL}  ")
        assert page["url"] == URL
        assert scrapling.calls[0][1]["url"] == URL


class TestResultParsing:
    async def test_text_block_json_is_parsed_without_structured_content(
        self, scrapling
    ):
        result = fetch_result()
        del result["structuredContent"]
        scrapling.results["get"] = result
        page = await read_page(URL)
        assert page["markdown"] == LONG_MD
        assert page["mode_used"] == "fast"

    async def test_plain_text_blocks_are_joined_verbatim(self, scrapling):
        text = "not json, just markdown content. " * 20
        scrapling.results["get"] = {"content": [{"type": "text", "text": text}]}
        page = await read_page(URL)
        assert page["markdown"] == text

    async def test_multiple_content_chunks_are_joined(self, scrapling):
        chunk = "Selected element content that is long enough to count. " * 5
        model = {"status": 200, "content": [chunk, chunk], "url": URL}
        scrapling.results["get"] = {"content": [], "structuredContent": model}
        page = await read_page(URL)
        assert page["markdown"] == f"{chunk}\n{chunk}"


# ── auto escalation ─────────────────────────────────────────────────────────


class TestAutoEscalation:
    async def test_tool_failure_escalates_to_stealthy_fetch(self, scrapling):
        scrapling.results["get"] = TOOL_ERROR
        scrapling.results["stealthy_fetch"] = fetch_result(markdown=STEALTH_MD)
        page = await read_page(URL)
        assert page["mode_used"] == "stealth"
        assert page["markdown"] == STEALTH_MD
        assert scrapling.called_tools() == ["get", "stealthy_fetch"]

    async def test_error_status_escalates_even_with_long_content(self, scrapling):
        blocked = "# Access denied\n\n" + "Checking your browser before access. " * 20
        scrapling.results["get"] = fetch_result(status=403, markdown=blocked)
        scrapling.results["stealthy_fetch"] = fetch_result(markdown=STEALTH_MD)
        page = await read_page(URL)
        assert page["mode_used"] == "stealth"
        assert page["markdown"] == STEALTH_MD

    async def test_js_empty_200_escalates(self, scrapling):
        scrapling.results["get"] = fetch_result(markdown="Enable JavaScript.")
        scrapling.results["stealthy_fetch"] = fetch_result(markdown=STEALTH_MD)
        page = await read_page(URL)
        assert page["mode_used"] == "stealth"
        assert scrapling.called_tools() == ["get", "stealthy_fetch"]

    async def test_stealth_is_asked_to_solve_cloudflare(self, scrapling):
        scrapling.results["get"] = TOOL_ERROR
        scrapling.results["stealthy_fetch"] = fetch_result(markdown=STEALTH_MD)
        await read_page(URL)
        name, arguments = scrapling.calls[-1]
        assert name == "stealthy_fetch"
        assert arguments["solve_cloudflare"] is True
        assert arguments["extraction_type"] == "markdown"
        assert arguments["main_content_only"] is True

    async def test_good_fast_content_never_escalates(self, scrapling):
        scrapling.results["get"] = fetch_result()
        await read_page(URL, mode="auto")
        assert scrapling.called_tools() == ["get"]

    async def test_stealth_failure_after_escalation_is_502(self, scrapling):
        scrapling.results["get"] = TOOL_ERROR
        scrapling.results["stealthy_fetch"] = TOOL_ERROR
        with pytest.raises(HTTPException) as excinfo:
            await read_page(URL)
        assert excinfo.value.status_code == 502


# ── forced modes ────────────────────────────────────────────────────────────


class TestForcedModes:
    async def test_fast_returns_short_content_without_escalating(self, scrapling):
        scrapling.results["get"] = fetch_result(markdown="tiny")
        page = await read_page(URL, mode="fast")
        assert page["mode_used"] == "fast"
        assert page["markdown"] == "tiny"
        assert scrapling.called_tools() == ["get"]

    async def test_fast_tool_failure_is_502(self, scrapling):
        scrapling.results["get"] = TOOL_ERROR
        with pytest.raises(HTTPException) as excinfo:
            await read_page(URL, mode="fast")
        assert excinfo.value.status_code == 502
        assert "fetch blew up" in excinfo.value.detail
        assert scrapling.called_tools() == ["get"]

    async def test_stealth_skips_the_fast_lane(self, scrapling):
        scrapling.results["stealthy_fetch"] = fetch_result(markdown=STEALTH_MD)
        page = await read_page(URL, mode="stealth")
        assert page["mode_used"] == "stealth"
        assert scrapling.called_tools() == ["stealthy_fetch"]


# ── content cap ─────────────────────────────────────────────────────────────


class TestContentCap:
    async def test_oversized_content_is_cut_with_a_marker(self, scrapling):
        huge = "# Big Page\n\n" + "x" * (MAX_CONTENT_CHARS + 5000)
        scrapling.results["get"] = fetch_result(markdown=huge)
        page = await read_page(URL)
        assert page["truncated"] is True
        assert page["markdown"].endswith(TRUNCATION_MARKER)
        assert len(page["markdown"]) == MAX_CONTENT_CHARS + len(TRUNCATION_MARKER)
        assert page["markdown"].startswith("# Big Page")

    async def test_content_at_the_cap_is_untouched(self, scrapling):
        exact = "y" * MAX_CONTENT_CHARS
        scrapling.results["get"] = fetch_result(markdown=exact)
        page = await read_page(URL)
        assert page["truncated"] is False
        assert page["markdown"] == exact


# ── validation and the error surface ────────────────────────────────────────


class TestValidation:
    @pytest.mark.parametrize(
        "bad",
        ["", "   ", "ftp://example.com/file", "javascript:alert(1)", "example.com",
         "http://", "https://" + "a" * MAX_URL_CHARS],
    )
    async def test_bad_urls_are_400_before_any_request(self, httpx_mock, bad):
        with pytest.raises(HTTPException) as excinfo:
            await read_page(bad)
        assert excinfo.value.status_code == 400
        assert httpx_mock.requests == []

    async def test_unknown_mode_is_400(self, httpx_mock):
        with pytest.raises(HTTPException) as excinfo:
            await read_page(URL, mode="turbo")
        assert excinfo.value.status_code == 400
        assert "mode" in excinfo.value.detail
        assert httpx_mock.requests == []

    async def test_unreachable_service_is_502(self, httpx_mock):
        def refuse(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        httpx_mock.set_handler(refuse)
        with pytest.raises(HTTPException) as excinfo:
            await read_page(URL)
        assert excinfo.value.status_code == 502
        assert "scrapling" in excinfo.value.detail

    async def test_the_configured_endpoint_is_dialed(self, scrapling, httpx_mock):
        from app.config import get_settings

        scrapling.results["get"] = fetch_result()
        await read_page(URL)
        assert all(
            str(request.url) == get_settings().scrapling_mcp_url
            for request in httpx_mock.requests
        )

    async def test_custom_endpoint_setting_is_honored(
        self, scrapling, httpx_mock, monkeypatch
    ):
        from app import config

        monkeypatch.setenv("FORGE_SCRAPLING_MCP_URL", "http://elsewhere:9000/mcp")
        config.get_settings.cache_clear()
        scrapling.results["get"] = fetch_result()
        await read_page(URL)
        assert str(httpx_mock.requests[0].url) == "http://elsewhere:9000/mcp"


class TestModuleConstants:
    def test_the_cap_is_about_150kb(self):
        assert web_reader.MAX_CONTENT_CHARS == 150_000
