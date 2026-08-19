"""POST /api/chat/read_page: auth, validation, the response shape (attachment
meta + read metadata), persistence and per-user ownership of the generated .md
upload, failure atomicity, and one end-to-end pass through the real web_reader
against a fake Scrapling MCP server."""

import pytest
from sqlmodel import select

from app import db as db_module
from app.models import Upload
from app.services import web_reader

from .test_web_reader import URL, FakeMCPServer, ScraplingFake, fetch_result

MARKDOWN = "# Example Domain\n\nThis domain is for use in examples.\n"


def user_id_of(api, headers) -> int:
    return api.get("/api/users/me", headers=headers).json()["id"]


@pytest.fixture
def read_page_stub(monkeypatch) -> list[dict]:
    """Replace web_reader.read_page with a recorder returning fixed markdown."""
    calls: list[dict] = []

    async def fake_read_page(url: str, mode: str = "auto") -> dict:
        calls.append({"url": url, "mode": mode})
        return {
            "url": url,
            "mode_used": "fast",
            "title": "Example Domain",
            "markdown": MARKDOWN,
            "truncated": False,
        }

    monkeypatch.setattr(web_reader, "read_page", fake_read_page)
    return calls


class TestValidation:
    def test_requires_auth(self, api, read_page_stub):
        assert api.post("/api/chat/read_page", json={"url": URL}).status_code == 401
        assert read_page_stub == []

    @pytest.mark.parametrize("bad", ["", "ftp://example.com/x", "example.com"])
    def test_invalid_url_is_400_and_stores_nothing(self, api, auth_headers, bad):
        resp = api.post(
            "/api/chat/read_page", json={"url": bad}, headers=auth_headers
        )
        assert resp.status_code == 400
        with db_module.read_session() as db:
            assert db.exec(select(Upload)).all() == []

    def test_invalid_mode_is_400(self, api, auth_headers):
        resp = api.post(
            "/api/chat/read_page",
            json={"url": URL, "mode": "turbo"},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "mode" in resp.json()["detail"]


class TestHappyPath:
    def test_returns_attachment_meta_and_read_metadata(
        self, api, auth_headers, read_page_stub
    ):
        resp = api.post(
            "/api/chat/read_page", json={"url": URL}, headers=auth_headers
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["url"] == URL
        assert body["mode_used"] == "fast"
        assert body["truncated"] is False
        upload = body["upload"]
        assert upload["kind"] == "text"
        assert upload["mime"] == "text/markdown"
        assert upload["filename"].endswith(".md")
        assert upload["generated"] is True
        assert upload["prompt"] == URL
        assert upload["size_bytes"] == len(MARKDOWN.encode())
        assert read_page_stub == [{"url": URL, "mode": "auto"}]

    def test_mode_is_forwarded(self, api, auth_headers, read_page_stub):
        resp = api.post(
            "/api/chat/read_page",
            json={"url": URL, "mode": "stealth"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert read_page_stub == [{"url": URL, "mode": "stealth"}]

    def test_upload_is_persisted_and_serves_to_its_owner_only(
        self, api, auth_headers, second_user_headers, read_page_stub
    ):
        body = api.post(
            "/api/chat/read_page", json={"url": URL}, headers=auth_headers
        ).json()
        upload_id = body["upload"]["id"]

        with db_module.read_session() as db:
            row = db.get(Upload, upload_id)
        assert row is not None
        assert row.user_id == user_id_of(api, auth_headers)

        fetched = api.get(f"/api/files/{upload_id}", headers=auth_headers)
        assert fetched.status_code == 200
        assert fetched.content.decode() == MARKDOWN
        # The other user can neither fetch nor see it.
        assert (
            api.get(f"/api/files/{upload_id}", headers=second_user_headers).status_code
            == 404
        )


class TestFailures:
    def test_unreachable_scrapling_service_is_502_and_stores_nothing(
        self, api, auth_headers, httpx_mock
    ):
        import httpx

        def refuse(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        httpx_mock.set_handler(refuse)
        resp = api.post(
            "/api/chat/read_page", json={"url": URL}, headers=auth_headers
        )
        assert resp.status_code == 502
        assert "scrapling" in resp.json()["detail"]
        with db_module.read_session() as db:
            assert db.exec(select(Upload)).all() == []

    def test_empty_page_content_is_502(self, api, auth_headers, monkeypatch):
        async def empty_read_page(url: str, mode: str = "auto") -> dict:
            return {
                "url": url,
                "mode_used": "fast",
                "title": "",
                "markdown": "   ",
                "truncated": False,
            }

        monkeypatch.setattr(web_reader, "read_page", empty_read_page)
        resp = api.post(
            "/api/chat/read_page", json={"url": URL}, headers=auth_headers
        )
        assert resp.status_code == 502
        with db_module.read_session() as db:
            assert db.exec(select(Upload)).all() == []


class TestEndToEnd:
    def test_real_web_reader_through_the_fake_mcp_server(
        self, api, auth_headers, httpx_mock
    ):
        server = FakeMCPServer()
        httpx_mock.set_handler(server.handle)
        scrapling = ScraplingFake(server)
        scrapling.results["get"] = fetch_result()

        resp = api.post(
            "/api/chat/read_page", json={"url": URL}, headers=auth_headers
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["mode_used"] == "fast"
        assert scrapling.called_tools() == ["get"]
        fetched = api.get(f"/api/files/{body['upload']['id']}", headers=auth_headers)
        assert fetched.status_code == 200
        assert fetched.content.decode().startswith("# Example Domain")
