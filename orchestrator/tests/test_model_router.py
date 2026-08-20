"""Auto-routing tests (model_router): worst-GPU placement, router-model
resolution, the choose_model decision (LLM pick, longest-slug disambiguation,
every fallback path), ensure_serving's no-evict policy, the router container
spawn, the settings knob, and the end-to-end "auto" chat flow with routing
narrated as forge:"status" frames."""

import asyncio
import json

import httpx
import pytest
from sqlmodel import select

from app import db as db_module
from app.db import set_setting
from app.models import ChatMessage, Conversation, EngineKind, ModelEntry
from app.services import chat_service, memory, model_router
from app.services.engine_manager import Lease, LeaseHeldError
from app.services.engine_manager import engine_manager as real_engine_manager

from .conftest import add_model


@pytest.fixture(autouse=True)
def reset_router_state():
    """The router's module-level lease handle must not leak between tests."""
    model_router._router = {"lease": None, "model_id": None}
    yield
    model_router._router = {"lease": None, "model_id": None}


def serve(slug: str, model_id: int = 1, gpu: int = 0) -> Lease:
    lease = Lease(
        model_id=model_id,
        model_name=slug,
        model_slug=slug,
        engine=EngineKind.llamacpp,
        gpu_ids=[gpu],
        state="ready",
        container_id=f"c-{slug}",
        base_url=f"http://forge-engine-llamacpp-gpu{gpu}:8081/v1",
    )
    real_engine_manager._leases[gpu] = lease
    return lease


def sse_payloads(text: str) -> list[str]:
    return [
        chunk[len("data: "):]
        for chunk in text.split("\n\n")
        if chunk.startswith("data: ")
    ]


def router_reply(content: str):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"content": content}}]}
        )

    return handler


class TestWorstGpu:
    def test_smallest_vram_wins(self):
        gpus = [
            {"index": 0, "vram_total_gb": 24.0},
            {"index": 1, "vram_total_gb": 8.0},
            {"index": 2, "vram_total_gb": 12.0},
        ]
        assert model_router.worst_gpu(gpus) == 1

    def test_tie_breaks_to_highest_index(self):
        gpus = [
            {"index": 0, "vram_total_gb": 12.0},
            {"index": 1, "vram_total_gb": 12.0},
        ]
        assert model_router.worst_gpu(gpus) == 1

    def test_empty_defaults_to_zero(self):
        assert model_router.worst_gpu([]) == 0


class TestRouterModelEntry:
    def test_unset_means_disabled(self, db_ready):
        assert model_router.router_model_slug() == ""
        assert model_router.router_model_entry() is None

    def test_resolves_ready_llamacpp_model(self, db_ready):
        model_id = add_model(display_name="Tiny Router 1B", params_b=1.0)
        set_setting("router_model_slug", "tiny-router-1b")
        entry = model_router.router_model_entry()
        assert entry is not None and entry.id == model_id

    def test_ignores_non_llamacpp_and_unknown(self, db_ready):
        add_model(display_name="Tiny Router 1B", engine=EngineKind.vllm)
        set_setting("router_model_slug", "tiny-router-1b")
        assert model_router.router_model_entry() is None
        set_setting("router_model_slug", "no-such-model")
        assert model_router.router_model_entry() is None


class TestChooseModel:
    def run(self, coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    def test_no_candidates_raises(self, db_ready):
        with pytest.raises(RuntimeError, match="no downloaded model is ready"):
            self.run(model_router.choose_model("hi"))

    def test_single_candidate_short_circuits(self, db_ready):
        model_id = add_model(display_name="Only Model")
        model, reason = self.run(model_router.choose_model("hi"))
        assert model.id == model_id
        assert reason == "the only ready model"

    def test_router_unavailable_defaults_to_largest(self, db_ready, monkeypatch):
        add_model(display_name="Small Model", params_b=3.0)
        big_id = add_model(display_name="Big Model", params_b=14.0)

        async def no_router():
            return None

        monkeypatch.setattr(model_router, "ensure_router", no_router)
        model, reason = self.run(model_router.choose_model("hi"))
        assert model.id == big_id
        assert reason == "router model unavailable — defaulted"

    def test_fallback_prefers_already_serving(self, db_ready, monkeypatch):
        small_id = add_model(display_name="Small Model", params_b=3.0)
        add_model(display_name="Big Model", params_b=14.0)
        serve("small-model", model_id=small_id)

        async def no_router():
            return None

        monkeypatch.setattr(model_router, "ensure_router", no_router)
        model, _ = self.run(model_router.choose_model("hi"))
        assert model.id == small_id  # serving beats larger-but-cold

    def _with_router(self, monkeypatch):
        lease = Lease(
            model_id=99,
            model_name="Tiny Router",
            model_slug="tiny-router",
            engine=EngineKind.llamacpp,
            gpu_ids=[],
            state="ready",
            base_url="http://forge-engine-router:8087/v1",
        )

        async def fake_router():
            return lease

        monkeypatch.setattr(model_router, "ensure_router", fake_router)
        return lease

    def test_router_picks_by_slug(self, db_ready, monkeypatch, httpx_mock):
        add_model(display_name="Big Model", params_b=14.0)
        small_id = add_model(display_name="Small Model", params_b=3.0)
        self._with_router(monkeypatch)
        httpx_mock.set_handler(router_reply("small-model"))
        model, reason = self.run(model_router.choose_model("what is 2+2?"))
        assert model.id == small_id
        assert reason == "picked by Tiny Router"
        body = json.loads(httpx_mock.requests[-1].content)
        menu = body["messages"][0]["content"]
        assert "small-model" in menu and "big-model" in menu
        assert body["messages"][1]["content"] == "what is 2+2?"

    def test_longest_slug_wins_substring_clash(self, db_ready, monkeypatch,
                                               httpx_mock):
        add_model(display_name="Coder", params_b=7.0)
        pro_id = add_model(display_name="Coder Pro", params_b=14.0)
        self._with_router(monkeypatch)
        # "coder-pro" contains "coder" — the longer exact slug must win.
        httpx_mock.set_handler(router_reply("I choose coder-pro."))
        model, _ = self.run(model_router.choose_model("write a parser"))
        assert model.id == pro_id

    def test_unparseable_reply_defaults(self, db_ready, monkeypatch, httpx_mock):
        add_model(display_name="Small Model", params_b=3.0)
        big_id = add_model(display_name="Big Model", params_b=14.0)
        self._with_router(monkeypatch)
        httpx_mock.set_handler(router_reply("hmm, tough one"))
        model, reason = self.run(model_router.choose_model("hi"))
        assert model.id == big_id
        assert reason == "router reply unparseable — defaulted"

    def test_router_call_failure_defaults(self, db_ready, monkeypatch,
                                          httpx_mock):
        add_model(display_name="Small Model", params_b=3.0)
        big_id = add_model(display_name="Big Model", params_b=14.0)
        self._with_router(monkeypatch)

        def boom(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="router exploded")

        httpx_mock.set_handler(boom)
        model, reason = self.run(model_router.choose_model("hi"))
        assert model.id == big_id
        assert reason == "router call failed — defaulted"


class TestEnsureRouter:
    def run(self, coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    def test_single_gpu_runs_on_cpu(self, db_ready, fake_docker, monkeypatch,
                                    httpx_mock):
        add_model(display_name="Tiny Router 1B", params_b=1.0)
        set_setting("router_model_slug", "tiny-router-1b")
        real_engine_manager._gpu_count = 1
        httpx_mock.set_handler(lambda request: httpx.Response(200, json={}))

        lease = self.run(model_router.ensure_router())
        assert lease is not None
        assert lease.base_url == "http://forge-engine-router:8087/v1"
        container = fake_docker.containers.get(model_router.ROUTER_CONTAINER)
        assert container.labels[model_router.ROUTER_LABEL] == "1"
        command = container.run_kwargs["command"]
        assert command[command.index("--n-gpu-layers") + 1] == "0"
        assert container.run_kwargs.get("device_requests") is None

    def test_multi_gpu_lands_on_worst(self, db_ready, fake_docker, monkeypatch,
                                      httpx_mock):
        add_model(display_name="Tiny Router 1B", params_b=1.0)
        set_setting("router_model_slug", "tiny-router-1b")
        real_engine_manager._gpu_count = 2
        monkeypatch.setattr(
            model_router,
            "_gpu_stats",
            lambda: [
                {"index": 0, "vram_total_gb": 16.0},
                {"index": 1, "vram_total_gb": 8.0},
            ],
        )
        httpx_mock.set_handler(lambda request: httpx.Response(200, json={}))

        lease = self.run(model_router.ensure_router())
        assert lease is not None
        container = fake_docker.containers.get(model_router.ROUTER_CONTAINER)
        requests = container.run_kwargs["device_requests"]
        assert requests and requests[0].device_ids == ["1"]
        command = container.run_kwargs["command"]
        assert command[command.index("--n-gpu-layers") + 1] == "999"

    def test_unconfigured_returns_none(self, db_ready):
        assert self.run(model_router.ensure_router()) is None

    def test_spawn_failure_returns_none(self, db_ready, fake_docker,
                                        monkeypatch):
        add_model(display_name="Tiny Router 1B", params_b=1.0)
        set_setting("router_model_slug", "tiny-router-1b")
        real_engine_manager._gpu_count = 1
        fake_docker.containers.fail_run = RuntimeError("no docker for you")
        assert self.run(model_router.ensure_router()) is None


class TestEnsureServing:
    def run(self, coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    def test_already_serving_returns_lease(self, db_ready, monkeypatch):
        model_id = add_model(display_name="Chat Model")
        with db_module.read_session() as db:
            model = db.get(ModelEntry, model_id)
        lease = serve("chat-model", model_id=model_id)
        pushes: list[str] = []
        got = self.run(model_router.ensure_serving(model, pushes.append))
        assert got is lease
        assert pushes == []  # nothing to narrate — it was already up

    def test_load_then_ready(self, db_ready, monkeypatch):
        model_id = add_model(display_name="Chat Model")
        with db_module.read_session() as db:
            model = db.get(ModelEntry, model_id)
        loaded = Lease(
            model_id=model_id,
            model_name="Chat Model",
            model_slug="chat-model",
            engine=EngineKind.llamacpp,
            gpu_ids=[0],
            state="ready",
            base_url="http://forge-engine-llamacpp-gpu0:8081/v1",
        )

        async def fake_load(entry):
            assert entry.id == model_id
            return loaded

        monkeypatch.setattr(model_router.engine_manager, "load", fake_load)
        pushes: list[str] = []
        got = self.run(model_router.ensure_serving(model, pushes.append))
        assert got is loaded
        assert any("loading Chat Model" in p for p in pushes)

    def test_load_failure_raises(self, db_ready, monkeypatch):
        model_id = add_model(display_name="Chat Model")
        with db_module.read_session() as db:
            model = db.get(ModelEntry, model_id)
        failed = Lease(
            model_id=model_id,
            model_name="Chat Model",
            model_slug="chat-model",
            engine=EngineKind.llamacpp,
            gpu_ids=[0],
            state="failed",
            error="OOM",
            base_url="http://forge-engine-llamacpp-gpu0:8081/v1",
        )

        async def fake_load(entry):
            return failed

        monkeypatch.setattr(model_router.engine_manager, "load", fake_load)
        with pytest.raises(RuntimeError, match="OOM"):
            self.run(model_router.ensure_serving(model, lambda d: None))

    def test_busy_gpus_fall_back_to_serving(self, db_ready, monkeypatch):
        other = serve("other-model", model_id=7)
        model_id = add_model(display_name="Chat Model")
        with db_module.read_session() as db:
            model = db.get(ModelEntry, model_id)

        async def held(entry):
            raise LeaseHeldError([{"model_name": "Other Model"}])

        monkeypatch.setattr(model_router.engine_manager, "load", held)
        pushes: list[str] = []
        got = self.run(model_router.ensure_serving(model, pushes.append))
        assert got is other
        assert any("every GPU is busy" in p for p in pushes)

    def test_busy_and_nothing_serving_raises(self, db_ready, monkeypatch):
        model_id = add_model(display_name="Chat Model")
        with db_module.read_session() as db:
            model = db.get(ModelEntry, model_id)

        async def held(entry):
            raise LeaseHeldError([{"model_name": "Other Model"}])

        monkeypatch.setattr(model_router.engine_manager, "load", held)
        with pytest.raises(RuntimeError, match="no GPU is free"):
            self.run(model_router.ensure_serving(model, lambda d: None))


class TestSettingsKnob:
    def test_get_and_patch_round_trip(self, api, auth_headers):
        resp = api.get("/api/settings", headers=auth_headers)
        assert resp.json()["router_model_slug"] == ""
        assert resp.json()["router_model_ready"] is False

        resp = api.patch(
            "/api/settings",
            json={"router_model_slug": "tiny-router-1b"},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["router_model_slug"] == "tiny-router-1b"
        assert resp.json()["router_model_ready"] is False  # not downloaded yet

        add_model(display_name="Tiny Router 1B", params_b=1.0)
        resp = api.get("/api/settings", headers=auth_headers)
        assert resp.json()["router_model_ready"] is True

        # Empty string disables LLM routing again.
        resp = api.patch(
            "/api/settings", json={"router_model_slug": ""}, headers=auth_headers
        )
        assert resp.json()["router_model_slug"] == ""

    def test_patch_requires_admin(self, api, auth_headers, second_user_headers):
        resp = api.patch(
            "/api/settings",
            json={"router_model_slug": "x"},
            headers=second_user_headers,
        )
        assert resp.status_code == 403


class TestChatStatusAuto:
    def test_auto_block_reflects_candidates(self, api, auth_headers):
        auto = api.get("/api/chat/status", headers=auth_headers).json()["auto"]
        assert auto == {
            "available": False, "router_model": "", "router_ready": False,
        }

        add_model(display_name="Chat Model")
        auto = api.get("/api/chat/status", headers=auth_headers).json()["auto"]
        assert auto["available"] is True


class TestAutoFlow:
    """End-to-end: a conversation whose model is "auto" routes inside the
    background job and narrates each stage as forge:"status" frames."""

    STREAM_PIECES = ("Routed", " reply")

    @pytest.fixture
    def stream_stub(self, monkeypatch) -> list[dict]:
        calls: list[dict] = []

        async def fake_stream(base_url, model_slug, messages, collected):
            calls.append(
                {"base_url": base_url, "model_slug": model_slug,
                 "messages": messages}
            )
            for piece in self.STREAM_PIECES:
                collected.append(piece)
                yield (
                    "data: "
                    + json.dumps({"choices": [{"delta": {"content": piece}}]})
                    + "\n\n"
                )
            yield "data: [DONE]\n\n"

        monkeypatch.setattr(chat_service, "stream_completion", fake_stream)
        return calls

    @pytest.fixture
    def scheduled(self, monkeypatch) -> list:
        coros: list = []

        def fake_schedule(coro):
            coros.append(coro)
            coro.close()

        monkeypatch.setattr(memory, "schedule_background", fake_schedule)
        return coros

    def _conversation(self, api, auth_headers) -> str:
        resp = api.post(
            "/api/chat/conversations",
            json={"model_slug": "auto"},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["id"]

    def test_no_ready_model_is_409(self, api, auth_headers):
        conversation_id = self._conversation(api, auth_headers)
        resp = api.post(
            f"/api/chat/conversations/{conversation_id}/messages",
            json={"content": "hello"},
            headers=auth_headers,
        )
        assert resp.status_code == 409
        assert "no model is ready to route to" in resp.text

    def test_prepare_failure_reaches_terminal_error(
        self, api, auth_headers, monkeypatch, stream_stub, scheduled
    ):
        add_model(display_name="Chat Model")
        conversation_id = self._conversation(api, auth_headers)

        async def exploding_choose(prompt):
            raise RuntimeError("no GPU is free and nothing is serving")

        monkeypatch.setattr(model_router, "choose_model", exploding_choose)

        resp = api.post(
            f"/api/chat/conversations/{conversation_id}/messages",
            json={"content": "route me"},
            headers=auth_headers,
        )
        assert resp.status_code == 200  # errors surface as stream frames
        payloads = [json.loads(p) for p in sse_payloads(resp.text)
                    if p != "[DONE]"]
        assert any("no GPU is free" in p.get("error", "") for p in payloads)
        assert payloads[-1]["forge"] == "done"
        from app.services.chat_jobs import chat_job_manager

        job = chat_job_manager.get(conversation_id)
        assert job is not None and job.state == "error"

    def test_routed_generation_narrates_and_persists(
        self, api, auth_headers, monkeypatch, stream_stub, scheduled
    ):
        model_id = add_model(display_name="Chat Model")
        lease = serve("chat-model", model_id=model_id)
        conversation_id = self._conversation(api, auth_headers)

        async def fake_choose(prompt):
            with db_module.read_session() as db:
                model = db.get(ModelEntry, model_id)
            return model, "picked by Tiny Router"

        async def fake_ensure(model, push_status):
            push_status(f"loading {model.display_name} onto the GPU")
            return lease

        monkeypatch.setattr(model_router, "choose_model", fake_choose)
        monkeypatch.setattr(model_router, "ensure_serving", fake_ensure)

        resp = api.post(
            f"/api/chat/conversations/{conversation_id}/messages",
            json={"content": "route me"},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        payloads = sse_payloads(resp.text)

        details = [
            json.loads(p)["detail"]
            for p in payloads
            if p != "[DONE]" and json.loads(p).get("forge") == "status"
        ]
        assert details[0] == "choosing the best model for this prompt…"
        assert details[1] == "routed to Chat Model — picked by Tiny Router"
        assert any("loading Chat Model" in d for d in details)
        assert any("prompt sent to chat-model" in d for d in details)

        done = json.loads(payloads[-1])
        assert done["forge"] == "done"
        assert done["assistant_message_id"] is not None  # persisted, no error
        assert stream_stub[0]["model_slug"] == "chat-model"

        with db_module.read_session() as db:
            conversation = db.get(Conversation, conversation_id)
            messages = db.exec(
                select(ChatMessage).where(
                    ChatMessage.conversation_id == conversation_id
                )
            ).all()
        assert conversation.model_slug == "auto"  # stays auto for next turn
        contents = {m.role: m.content for m in messages}
        assert contents["assistant"] == "".join(self.STREAM_PIECES)
