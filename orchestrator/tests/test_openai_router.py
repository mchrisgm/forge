"""The OpenAI-compatible /v1 model router and the /api/engines/chat proxy:
slug -> lease resolution, the unauthenticated-by-design /v1 surface, and
thinking-directive injection before forwarding. Engine leases are faked on the
engine_manager singleton; the upstream proxy is captured, never dialed."""

import json

import pytest
from fastapi import HTTPException
from fastapi.responses import Response

from app.models import EngineKind
from app.routers import engines as engines_router
from app.routers import openai_router
from app.routers.openai_router import resolve_lease
from app.services.engine_manager import Lease, engine_manager

from .conftest import add_model


def make_lease(
    slug: str,
    state: str = "ready",
    gpu: int = 0,
    model_id: int = 1,
    engine: EngineKind = EngineKind.llamacpp,
) -> Lease:
    return Lease(
        model_id=model_id,
        model_name=slug.replace("-", " ").title(),
        model_slug=slug,
        engine=engine,
        gpu_ids=[gpu],
        state=state,
        container_id=f"c-{slug}",
        base_url=f"http://forge-engine-{engine.value}-gpu{gpu}:8081/v1",
    )


def install_leases(*leases: Lease) -> None:
    engine_manager._leases = {lease.gpu_index: lease for lease in leases}


@pytest.fixture
def captured_proxy(monkeypatch):
    """Replace the streaming proxy (in both routers that imported it) with a
    recorder returning a canned response."""
    calls: list[dict] = []

    async def fake_proxy(base_url: str, path: str, body: bytes) -> Response:
        calls.append({"base_url": base_url, "path": path, "body": body})
        return Response(b'{"ok": true}', media_type="application/json")

    monkeypatch.setattr(openai_router, "proxy_openai_request", fake_proxy)
    monkeypatch.setattr(engines_router, "proxy_openai_request", fake_proxy)
    return calls


# ── resolve_lease ───────────────────────────────────────────────────────────


class TestResolveLease:
    def test_slug_match_wins_over_fallback(self):
        first = make_lease("model-a", gpu=0, model_id=1)
        second = make_lease("model-b", gpu=1, model_id=2)
        install_leases(first, second)
        assert resolve_lease("model-b") is second
        assert resolve_lease("model-a") is first

    def test_single_ready_lease_is_the_fallback_for_slugless_requests(self):
        only = make_lease("model-a")
        install_leases(only)
        assert resolve_lease(None) is only

    def test_explicit_unknown_slug_is_404_even_with_one_lease(self):
        """An explicit slug that matches nothing must never be silently
        answered by a different model (review finding)."""
        install_leases(make_lease("model-a"))
        with pytest.raises(HTTPException) as excinfo:
            resolve_lease("model-that-was-unloaded")
        assert excinfo.value.status_code == 404
        assert "model-a" in excinfo.value.detail

    def test_ambiguous_without_slug_is_404_listing_served_slugs(self):
        install_leases(make_lease("model-a", gpu=0), make_lease("model-b", gpu=1))
        with pytest.raises(HTTPException) as excinfo:
            resolve_lease(None)
        assert excinfo.value.status_code == 404
        assert "model-a" in excinfo.value.detail
        assert "model-b" in excinfo.value.detail

    def test_unknown_slug_with_several_served_is_404(self):
        install_leases(make_lease("model-a", gpu=0), make_lease("model-b", gpu=1))
        with pytest.raises(HTTPException) as excinfo:
            resolve_lease("model-c")
        assert excinfo.value.status_code == 404
        assert "'model-c'" in excinfo.value.detail

    def test_nothing_served_is_404(self):
        install_leases()
        with pytest.raises(HTTPException) as excinfo:
            resolve_lease("model-a")
        assert excinfo.value.status_code == 404
        assert "(none)" in excinfo.value.detail

    def test_starting_and_failed_leases_never_resolve(self):
        install_leases(
            make_lease("model-a", state="starting", gpu=0),
            make_lease("model-b", state="failed", gpu=1),
        )
        with pytest.raises(HTTPException):
            resolve_lease("model-a")

    def test_explicit_imagegen_slug_is_404(self):
        """Imagegen leases never answer chat, even when named explicitly."""
        install_leases(make_lease("sdxl-turbo", engine=EngineKind.imagegen))
        with pytest.raises(HTTPException) as excinfo:
            resolve_lease("sdxl-turbo")
        assert excinfo.value.status_code == 404
        assert "(none)" in excinfo.value.detail

    def test_imagegen_lease_is_not_the_slugless_fallback(self):
        install_leases(make_lease("sdxl-turbo", engine=EngineKind.imagegen))
        with pytest.raises(HTTPException) as excinfo:
            resolve_lease(None)
        assert excinfo.value.status_code == 404

    def test_single_text_lease_still_falls_back_beside_an_imagegen_lease(self):
        text = make_lease("model-a", gpu=0, model_id=1)
        install_leases(
            text,
            make_lease("sdxl-turbo", engine=EngineKind.imagegen, gpu=1, model_id=2),
        )
        assert resolve_lease(None) is text


# ── GET /v1/models ──────────────────────────────────────────────────────────


class TestV1Models:
    def test_lists_ready_leases_only_without_auth(self, api):
        install_leases(
            make_lease("model-a", gpu=0),
            make_lease("model-b", state="starting", gpu=1),
        )
        # No Authorization header on purpose: /v1 serves session containers on
        # the internal network and must not require the PWA bearer token.
        resp = api.get("/v1/models")
        assert resp.status_code == 200
        data = resp.json()
        assert data["object"] == "list"
        assert [m["id"] for m in data["data"]] == ["model-a"]
        assert data["data"][0]["owned_by"] == "forge-llamacpp-gpu0"

    def test_api_routes_still_require_auth(self, api):
        # Contrast: the /api surface is bearer-guarded; only /v1 is open.
        assert api.get("/api/engines").status_code == 401

    def test_empty_when_nothing_served(self, api):
        resp = api.get("/v1/models")
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    def test_imagegen_leases_are_excluded_from_the_listing(self, api):
        install_leases(
            make_lease("model-a", gpu=0, model_id=1),
            make_lease("sdxl-turbo", engine=EngineKind.imagegen, gpu=1, model_id=2),
        )
        resp = api.get("/v1/models")
        assert [m["id"] for m in resp.json()["data"]] == ["model-a"]


# ── POST /v1/chat/completions ───────────────────────────────────────────────


class TestV1ChatCompletions:
    def test_routes_by_slug_to_the_serving_lease(self, api, captured_proxy):
        target = make_lease("model-b", gpu=1, model_id=2)
        install_leases(make_lease("model-a", gpu=0), target)

        body = {"model": "model-b", "messages": [{"role": "user", "content": "hi"}]}
        resp = api.post("/v1/chat/completions", json=body)  # no auth header
        assert resp.status_code == 200

        (call,) = captured_proxy
        assert call["base_url"] == target.base_url
        assert call["path"] == "chat/completions"
        assert json.loads(call["body"]) == body  # forwarded verbatim

    def test_unknown_model_is_404_when_ambiguous(self, api, captured_proxy):
        install_leases(make_lease("model-a", gpu=0), make_lease("model-b", gpu=1))
        resp = api.post("/v1/chat/completions", json={"model": "nope"})
        assert resp.status_code == 404
        assert captured_proxy == []


# ── POST /api/engines/chat ──────────────────────────────────────────────────


class TestEngineChat:
    def test_409_when_nothing_is_ready(self, api, auth_headers):
        install_leases(make_lease("model-a", state="starting"))
        resp = api.post(
            "/api/engines/chat",
            headers=auth_headers,
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["message"] == "no engine is serving"
        assert detail["leases"][0]["model_slug"] == "model-a"

    def test_thinking_directives_injected_and_model_replaced_by_slug(
        self, api, auth_headers, captured_proxy
    ):
        model_id = add_model(
            hf_repo="Qwen/Qwen3-Coder-30B-A3B-Instruct",
            display_name="Qwen3 Coder 30B",
            family="qwen3",
        )
        lease = make_lease("qwen3-coder-30b", model_id=model_id)
        install_leases(lease)

        resp = api.post(
            "/api/engines/chat",
            headers=auth_headers,
            json={
                "model": "qwen3-coder-30b",
                "thinking": "high",
                "messages": [{"role": "user", "content": "prove it"}],
            },
        )
        assert resp.status_code == 200, resp.text

        (call,) = captured_proxy
        assert call["base_url"] == lease.base_url
        forwarded = json.loads(call["body"])
        # The engine is dialed with the served slug, never a display name;
        # the thinking knob itself must not leak into the OpenAI body.
        assert forwarded["model"] == "qwen3-coder-30b"
        assert "thinking" not in forwarded
        # Qwen3 high: soft switch on the user turn + a system nudge in front.
        assert forwarded["messages"][-1]["role"] == "user"
        assert forwarded["messages"][-1]["content"].endswith(" /think")
        assert forwarded["messages"][0]["role"] == "system"
        assert forwarded["messages"][0]["content"]

    def test_missing_model_falls_back_to_single_ready_lease(
        self, api, auth_headers, captured_proxy
    ):
        model_id = add_model()
        install_leases(make_lease("qwen2-5-coder-14b-instruct", model_id=model_id))
        resp = api.post(
            "/api/engines/chat",
            headers=auth_headers,
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 200
        (call,) = captured_proxy
        assert json.loads(call["body"])["model"] == "qwen2-5-coder-14b-instruct"

    def test_invalid_thinking_level_is_400(self, api, auth_headers, captured_proxy):
        model_id = add_model()
        install_leases(make_lease("qwen2-5-coder-14b-instruct", model_id=model_id))
        resp = api.post(
            "/api/engines/chat",
            headers=auth_headers,
            json={"thinking": "maximum-overdrive", "messages": []},
        )
        assert resp.status_code == 400
        assert "thinking must be one of" in resp.json()["detail"]
        assert captured_proxy == []

    def test_chat_requires_auth_unlike_v1(self, api):
        install_leases(make_lease("model-a"))
        assert api.post("/api/engines/chat", json={"messages": []}).status_code == 401
