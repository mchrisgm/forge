"""Headroom completion-path routing: the toggle, the cached health probe,
the /v1 chaining + /v1-direct loop-free path, and the Settings surface.
No real headroom exists in tests — which also exercises the automatic
degrade-to-direct fallback the design promises."""

import httpx
import pytest

from app import db as db_module
from app.services import routing


@pytest.fixture(autouse=True)
def fresh_probe():
    routing.reset_probe()
    yield
    routing.reset_probe()


class TestToggle:
    def test_defaults_to_the_env_setting(self, api):
        assert routing.enabled() is True  # config default headroom_enabled=True

    def test_setting_row_overrides(self, api):
        db_module.set_setting("headroom_enabled", "false")
        assert routing.enabled() is False
        db_module.set_setting("headroom_enabled", "true")
        assert routing.enabled() is True

    def test_garbage_setting_falls_back_to_default(self, api):
        db_module.set_setting("headroom_enabled", "banana")
        assert routing.enabled() is True


class TestHealthProbe:
    async def test_healthy_when_models_answers_200(self, api, httpx_mock):
        httpx_mock.set_handler(
            lambda request: httpx.Response(200, json={"object": "list", "data": []})
        )
        assert await routing.healthy() is True
        assert "/models" in str(httpx_mock.requests[0].url)

    async def test_unreachable_proxy_is_unhealthy(self, api, httpx_mock):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down", request=request)

        httpx_mock.set_handler(handler)
        assert await routing.healthy() is False

    async def test_result_is_cached_within_ttl(self, api, httpx_mock):
        httpx_mock.set_handler(
            lambda request: httpx.Response(200, json={"data": []})
        )
        assert await routing.healthy() is True
        assert await routing.healthy() is True
        assert len(httpx_mock.requests) == 1  # second call served from cache

    async def test_reset_probe_forces_a_fresh_check(self, api, httpx_mock):
        httpx_mock.set_handler(lambda request: httpx.Response(200, json={}))
        await routing.healthy()
        routing.reset_probe()
        await routing.healthy()
        assert len(httpx_mock.requests) == 2


class TestCompletionBaseUrl:
    async def test_active_routes_to_headroom(self, api, httpx_mock):
        httpx_mock.set_handler(lambda request: httpx.Response(200, json={}))
        url = await routing.completion_base_url("http://engine:8082/v1")
        assert url == "http://headroom:8787/v1"

    async def test_disabled_toggle_stays_direct(self, api, httpx_mock):
        db_module.set_setting("headroom_enabled", "false")
        url = await routing.completion_base_url("http://engine:8082/v1")
        assert url == "http://engine:8082/v1"
        assert httpx_mock.requests == []  # no probe when disabled

    async def test_unhealthy_proxy_degrades_to_direct(self, api, httpx_mock):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down", request=request)

        httpx_mock.set_handler(handler)
        url = await routing.completion_base_url("http://engine:8082/v1")
        assert url == "http://engine:8082/v1"


class TestRouterChaining:
    def test_v1_forwards_to_headroom_when_active(self, api, monkeypatch):
        calls: list[tuple[str, str]] = []

        async def fake_active() -> bool:
            return True

        async def fake_proxy(base_url: str, path: str, body: bytes):
            calls.append((base_url, path))
            return {"proxied": True}

        from app.routers import openai_router

        monkeypatch.setattr(openai_router.routing, "active", fake_active)
        monkeypatch.setattr(openai_router, "proxy_openai_request", fake_proxy)
        resp = api.post("/v1/chat/completions", json={"model": "anything"})
        assert resp.status_code == 200
        assert calls == [("http://headroom:8787/v1", "chat/completions")]

    def test_v1_direct_never_chains_even_when_active(self, api, monkeypatch):
        async def fake_active() -> bool:  # pragma: no cover - must not be hit
            raise AssertionError("/v1-direct consulted the headroom routing")

        from app.routers import openai_router

        monkeypatch.setattr(openai_router.routing, "active", fake_active)
        # No lease is serving, so the direct path 404s with the slug message —
        # proving it took the resolve-lease branch, not the headroom branch.
        resp = api.post("/v1-direct/chat/completions", json={"model": "nope"})
        assert resp.status_code == 404
        assert "not being served" in resp.json()["detail"]

    def test_v1_direct_models_matches_v1(self, api):
        assert (
            api.get("/v1-direct/models").json() == api.get("/v1/models").json()
        )


class TestSettingsSurface:
    def test_get_reports_headroom_status(self, api, auth_headers):
        body = api.get("/api/settings", headers=auth_headers).json()
        assert body["headroom"]["enabled"] is True
        assert body["headroom"]["url"] == "http://headroom:8787/v1"
        assert body["headroom"]["healthy"] is False  # nothing listening in tests

    def test_patch_toggles_and_reports(self, api, auth_headers):
        body = api.patch(
            "/api/settings", json={"headroom_enabled": False}, headers=auth_headers
        ).json()
        assert body["headroom"]["enabled"] is False
        assert body["headroom"]["healthy"] is None  # not probed when disabled
        body = api.patch(
            "/api/settings", json={"headroom_enabled": True}, headers=auth_headers
        ).json()
        assert body["headroom"]["enabled"] is True
