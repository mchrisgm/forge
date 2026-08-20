"""Per-user OAuth sign-in: device flow (GitHub) and PKCE code flow (Hugging
Face) mint tokens into the caller's own connector row; the repo picker lists
the caller's repos with their token; HF searches/downloads run with the
requesting user's access. Flows are user-scoped and single-use."""

import json

import httpx
import pytest
from sqlmodel import select

from app import db as db_module
from app.db import set_setting
from app.models import Connector
from app.services import oauth_flows
from app.services.oauth_flows import store_token, stored_token


class FakeResponse:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPError(f"HTTP {self.status_code}")


class FakeAsyncClient:
    """Routes (method, url) to canned responses; records every call.

    `responses[(method, url)]` is a FakeResponse or a list consumed in order
    (for poll-then-succeed sequences).
    """

    responses: dict = {}
    calls: list = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def _dispatch(self, method, url, **kwargs):
        FakeAsyncClient.calls.append({"method": method, "url": url, **kwargs})
        canned = FakeAsyncClient.responses.get((method, url))
        if canned is None:
            raise httpx.HTTPError(f"no fake response for {method} {url}")
        if isinstance(canned, list):
            return canned.pop(0) if len(canned) > 1 else canned[0]
        return canned

    async def post(self, url, **kwargs):
        return await self._dispatch("POST", url, **kwargs)

    async def get(self, url, **kwargs):
        return await self._dispatch("GET", url, **kwargs)


@pytest.fixture(autouse=True)
def fake_http(monkeypatch):
    FakeAsyncClient.responses = {}
    FakeAsyncClient.calls = []
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    oauth_flows._flows.clear()
    yield
    oauth_flows._flows.clear()


def connector_config(api, headers, kind: str) -> dict:
    me = api.get("/api/users/me", headers=headers).json()
    with db_module.read_session() as db:
        row = db.exec(
            select(Connector).where(
                Connector.kind == kind, Connector.user_id == me["id"]
            )
        ).first()
        return {
            "config": json.loads(row.config_json or "{}") if row else {},
            "enabled": bool(row and row.enabled),
        }


GH_DEVICE = ("POST", "https://github.com/login/device/code")
GH_TOKEN = ("POST", "https://github.com/login/oauth/access_token")
GH_USER = ("GET", "https://api.github.com/user")
GH_REPOS = ("GET", "https://api.github.com/user/repos")
HF_TOKEN = ("POST", "https://huggingface.co/oauth/token")
HF_WHOAMI = ("GET", "https://huggingface.co/api/whoami-v2")


# ── provider discovery ──────────────────────────────────────────────────────


class TestProviders:
    def test_ready_flips_when_the_admin_sets_a_client_id(self, api, auth_headers):
        before = api.get("/api/connectors/oauth/providers", headers=auth_headers).json()
        assert before["github"]["supported"] and not before["github"]["ready"]
        assert before["hugging-face"]["method"] == "code"

        set_setting("github_oauth_client_id", "cid-gh")
        after = api.get("/api/connectors/oauth/providers", headers=auth_headers).json()
        assert after["github"]["ready"]

    def test_settings_page_exposes_and_stores_client_config(self, api, auth_headers):
        r = api.patch(
            "/api/settings",
            json={
                "github_oauth_client_id": "cid-gh",
                "hf_oauth_client_id": "cid-hf",
                "hf_oauth_client_secret": "sec-hf",
            },
            headers=auth_headers,
        )
        assert r.status_code == 200
        oauth = r.json()["oauth"]
        assert oauth["github"]["client_id"] == "cid-gh"
        assert oauth["hugging-face"]["client_id"] == "cid-hf"
        # The secret itself is never echoed back.
        assert oauth["hugging-face"]["has_secret"] is True
        assert "sec-hf" not in json.dumps(oauth)


# ── device flow (GitHub) ────────────────────────────────────────────────────


class TestDeviceFlow:
    def start(self, api, headers) -> dict:
        set_setting("github_oauth_client_id", "cid-gh")
        FakeAsyncClient.responses[GH_DEVICE] = FakeResponse(
            {
                "device_code": "dev-123",
                "user_code": "ABCD-1234",
                "verification_uri": "https://github.com/login/device",
                "interval": 0,  # let tests poll immediately
                "expires_in": 900,
            }
        )
        r = api.post(
            "/api/connectors/github/oauth/start", json={}, headers=headers
        )
        assert r.status_code == 200, r.text
        return r.json()

    def test_unconfigured_client_id_is_a_helpful_409(self, api, auth_headers):
        r = api.post(
            "/api/connectors/github/oauth/start", json={}, headers=auth_headers
        )
        assert r.status_code == 409
        assert "client ID" in r.json()["detail"]

    def test_full_sign_in_stores_the_token_on_my_connector(self, api, auth_headers):
        flow = self.start(api, auth_headers)
        assert flow["user_code"] == "ABCD-1234"

        FakeAsyncClient.responses[GH_TOKEN] = [
            FakeResponse({"error": "authorization_pending"}),
            FakeResponse({"access_token": "gho_secret", "scope": "repo,read:user"}),
        ]
        FakeAsyncClient.responses[GH_USER] = FakeResponse({"login": "octocat"})

        r1 = api.post(
            "/api/connectors/github/oauth/poll",
            json={"flow_id": flow["flow_id"]},
            headers=auth_headers,
        )
        assert r1.json() == {"status": "pending"}
        r2 = api.post(
            "/api/connectors/github/oauth/poll",
            json={"flow_id": flow["flow_id"]},
            headers=auth_headers,
        )
        assert r2.json() == {"status": "connected", "account": "octocat"}

        stored = connector_config(api, auth_headers, "github")
        assert stored["config"]["token"] == "gho_secret"
        assert stored["config"]["oauth"]["account"] == "octocat"
        assert stored["enabled"] is True

        # The connectors list surfaces the connection (and never the token).
        cards = api.get("/api/connectors", headers=auth_headers).json()
        github = next(c for c in cards if c["kind"] == "github")
        assert github["oauth"]["connected"] and github["oauth"]["account"] == "octocat"
        assert github["has_token"] is True
        assert "gho_secret" not in json.dumps(cards)

    def test_flows_are_scoped_to_the_user_who_started_them(
        self, api, auth_headers, second_user_headers
    ):
        flow = self.start(api, auth_headers)
        r = api.post(
            "/api/connectors/github/oauth/poll",
            json={"flow_id": flow["flow_id"]},
            headers=second_user_headers,
        )
        assert r.status_code == 404

    def test_denied_sign_in_ends_the_flow(self, api, auth_headers):
        flow = self.start(api, auth_headers)
        FakeAsyncClient.responses[GH_TOKEN] = FakeResponse({"error": "access_denied"})
        r = api.post(
            "/api/connectors/github/oauth/poll",
            json={"flow_id": flow["flow_id"]},
            headers=auth_headers,
        )
        assert r.status_code == 403
        # Single-use: the flow is gone.
        r2 = api.post(
            "/api/connectors/github/oauth/poll",
            json={"flow_id": flow["flow_id"]},
            headers=auth_headers,
        )
        assert r2.status_code == 404


# ── code flow with PKCE (Hugging Face) ──────────────────────────────────────


class TestCodeFlow:
    def start(self, api, headers) -> dict:
        set_setting("hf_oauth_client_id", "cid-hf")
        r = api.post(
            "/api/connectors/hugging-face/oauth/start",
            json={"redirect_uri": "http://forge.lan:8080/oauth/callback"},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        return r.json()

    def test_authorize_url_carries_state_and_pkce_challenge(self, api, auth_headers):
        flow = self.start(api, auth_headers)
        url = flow["authorize_url"]
        assert url.startswith("https://huggingface.co/oauth/authorize?")
        assert "client_id=cid-hf" in url
        assert f"state={flow['flow_id']}" in url
        assert "code_challenge=" in url and "code_challenge_method=S256" in url

    def test_bad_redirect_uri_is_rejected(self, api, auth_headers):
        set_setting("hf_oauth_client_id", "cid-hf")
        r = api.post(
            "/api/connectors/hugging-face/oauth/start",
            json={"redirect_uri": "http://evil.example/steal"},
            headers=auth_headers,
        )
        assert r.status_code == 400

    def test_exchange_sends_the_verifier_and_stores_the_token(
        self, api, auth_headers
    ):
        flow = self.start(api, auth_headers)
        FakeAsyncClient.responses[HF_TOKEN] = FakeResponse(
            {"access_token": "hf_oauth_secret", "scope": "read-repos"}
        )
        FakeAsyncClient.responses[HF_WHOAMI] = FakeResponse({"name": "mchris"})

        r = api.post(
            "/api/connectors/hugging-face/oauth/exchange",
            json={"code": "auth-code-1", "state": flow["flow_id"]},
            headers=auth_headers,
        )
        assert r.json() == {"status": "connected", "account": "mchris"}

        token_call = next(
            c for c in FakeAsyncClient.calls if c["url"].endswith("/oauth/token")
        )
        form = token_call["data"]
        assert form["code"] == "auth-code-1"
        assert form["code_verifier"]  # PKCE proof travelled
        assert form["redirect_uri"] == "http://forge.lan:8080/oauth/callback"

        stored = connector_config(api, auth_headers, "hugging-face")
        assert stored["config"]["token"] == "hf_oauth_secret"

    def test_forged_or_reused_state_is_rejected(
        self, api, auth_headers, second_user_headers
    ):
        flow = self.start(api, auth_headers)
        # Another user cannot redeem my state.
        r = api.post(
            "/api/connectors/hugging-face/oauth/exchange",
            json={"code": "x", "state": flow["flow_id"]},
            headers=second_user_headers,
        )
        assert r.status_code == 404
        # A made-up state fails.
        r = api.post(
            "/api/connectors/hugging-face/oauth/exchange",
            json={"code": "x", "state": "forged"},
            headers=auth_headers,
        )
        assert r.status_code == 404


# ── disconnect + token slot interplay ───────────────────────────────────────


class TestDisconnect:
    def test_disconnect_clears_token_and_oauth_metadata(self, api, auth_headers):
        me = api.get("/api/users/me", headers=auth_headers).json()
        store_token(me["id"], "github", "gho_x", "octocat", "repo", "device")

        r = api.delete("/api/connectors/github/oauth", headers=auth_headers)
        assert r.json() == {"ok": True}
        stored = connector_config(api, auth_headers, "github")
        assert "token" not in stored["config"]
        assert "oauth" not in stored["config"]

        # Nothing left to disconnect.
        assert (
            api.delete("/api/connectors/github/oauth", headers=auth_headers).status_code
            == 409
        )

    def test_hand_editing_the_token_drops_the_oauth_chip(self, api, auth_headers):
        me = api.get("/api/users/me", headers=auth_headers).json()
        store_token(me["id"], "github", "gho_x", "octocat", "repo", "device")
        api.patch(
            "/api/connectors/github",
            json={"config": {"token": "ghp_manual"}},
            headers=auth_headers,
        )
        stored = connector_config(api, auth_headers, "github")
        assert stored["config"]["token"] == "ghp_manual"
        assert "oauth" not in stored["config"]

    def test_disabled_connector_hides_the_stored_token(self, api, auth_headers):
        me = api.get("/api/users/me", headers=auth_headers).json()
        store_token(me["id"], "github", "gho_x", "octocat", "repo", "device")
        assert stored_token(me["id"], "github") == "gho_x"
        api.patch(
            "/api/connectors/github", json={"enabled": False}, headers=auth_headers
        )
        assert stored_token(me["id"], "github") == ""


# ── repo picker ─────────────────────────────────────────────────────────────


class TestRepoPicker:
    def test_without_a_connection_the_picker_explains_itself(self, api, auth_headers):
        r = api.get("/api/connectors/github/repos", headers=auth_headers)
        assert r.status_code == 409
        assert "Connectors page" in r.json()["detail"]

    def test_lists_public_and_private_repos_with_a_query_filter(
        self, api, auth_headers
    ):
        me = api.get("/api/users/me", headers=auth_headers).json()
        store_token(me["id"], "github", "gho_x", "octocat", "repo", "device")
        FakeAsyncClient.responses[GH_REPOS] = FakeResponse(
            [
                {
                    "full_name": "octocat/forge",
                    "private": True,
                    "default_branch": "main",
                    "description": "my private forge",
                    "pushed_at": "2026-08-19T00:00:00Z",
                    "html_url": "https://github.com/octocat/forge",
                },
                {
                    "full_name": "octocat/hello-world",
                    "private": False,
                    "default_branch": "master",
                    "description": None,
                    "pushed_at": "2026-01-01T00:00:00Z",
                    "html_url": "https://github.com/octocat/hello-world",
                },
            ]
        )
        repos = api.get("/api/connectors/github/repos", headers=auth_headers).json()
        assert [r["full_name"] for r in repos] == [
            "octocat/forge",
            "octocat/hello-world",
        ]
        assert repos[0]["private"] is True
        assert repos[0]["clone_url"] == "https://github.com/octocat/forge.git"
        # Token travelled as the Authorization header, never in the URL.
        call = next(c for c in FakeAsyncClient.calls if "user/repos" in c["url"])
        assert call["headers"]["Authorization"] == "Bearer gho_x"

        filtered = api.get(
            "/api/connectors/github/repos", params={"q": "forge"}, headers=auth_headers
        ).json()
        assert [r["full_name"] for r in filtered] == ["octocat/forge"]

    def test_second_user_has_their_own_connection_state(
        self, api, auth_headers, second_user_headers
    ):
        me = api.get("/api/users/me", headers=auth_headers).json()
        store_token(me["id"], "github", "gho_x", "octocat", "repo", "device")
        # My connection does not leak to the second profile.
        r = api.get("/api/connectors/github/repos", headers=second_user_headers)
        assert r.status_code == 409


# ── per-user Hugging Face access ────────────────────────────────────────────


class TestUserHfToken:
    def test_model_search_runs_with_the_callers_hf_token(
        self, api, auth_headers, monkeypatch
    ):
        me = api.get("/api/users/me", headers=auth_headers).json()
        store_token(me["id"], "hugging-face", "hf_mine", "mchris", "read-repos", "code")
        seen: dict = {}

        def fake_search(query, kind, limit, token=None):
            seen["token"] = token
            return []

        from app.routers import models_api

        monkeypatch.setattr(models_api, "search_hub", fake_search)
        api.get("/api/models/search", params={"q": "llama"}, headers=auth_headers)
        assert seen["token"] == "hf_mine"

    def test_search_without_a_connection_falls_back_to_the_global_token(
        self, api, auth_headers, monkeypatch
    ):
        seen: dict = {}

        def fake_search(query, kind, limit, token=None):
            seen["token"] = token
            return []

        from app.routers import models_api

        monkeypatch.setattr(models_api, "search_hub", fake_search)
        api.get("/api/models/search", params={"q": "llama"}, headers=auth_headers)
        assert seen["token"] is None  # registry applies settings.hf_token itself
