"""Background chat generation: a job survives a detached client, a returning
client re-attaches to replay + live tokens, concurrent jobs load-balance across
leases, and a full lane queues rather than oversubscribing."""

import asyncio
import json

import pytest
from sqlmodel import select

from app import db as db_module
from app.models import ChatMessage, Conversation, EngineKind
from app.services import chat_jobs, chat_service
from app.services.chat_jobs import ChatJobManager, chat_job_manager, lease_capacity
from app.services.engine_manager import Lease


def make_lease(slug: str, base_url: str, engine=EngineKind.llamacpp, gpu: int = 0) -> Lease:
    return Lease(
        model_id=1,
        model_name=slug,
        model_slug=slug,
        engine=engine,
        gpu_ids=[gpu],
        state="ready",
        base_url=base_url,
    )


def frame(piece: str) -> str:
    return "data: " + json.dumps({"choices": [{"delta": {"content": piece}}]}) + "\n\n"


def install_stream(monkeypatch, pieces, hold: asyncio.Event | None = None):
    """Fake stream_completion. When `hold` is given, the generator emits the
    pieces then blocks before [DONE] until the event is set — so the job stays
    'running' (holding its lease slot) for as long as the test wants."""

    async def fake_stream(base_url, model_slug, messages, collected):
        for p in pieces:
            collected.append(p)
            yield frame(p)
        if hold is not None:
            await hold.wait()
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(chat_service, "stream_completion", fake_stream)


def use_leases(monkeypatch, leases):
    monkeypatch.setattr(
        chat_jobs.engine_manager, "ready_text_leases", lambda: list(leases)
    )


@pytest.fixture(autouse=True)
def clean_registry():
    """Isolate the process-global job registry and per-lease slot budgets."""
    chat_job_manager._jobs.clear()
    chat_jobs._lease_slots.clear()
    yield
    for job in list(chat_job_manager._jobs.values()):
        if job.task:
            job.task.cancel()
    chat_job_manager._jobs.clear()
    chat_jobs._lease_slots.clear()


def make_conversation(user_id: int = 1) -> str:
    conv = Conversation(user_id=user_id, title="t")
    with db_module.write_session() as db:
        db.add(conv)
        db.flush()
        return conv.id


async def wait_done(job, timeout: float = 3.0):
    await asyncio.wait_for(job._done.wait(), timeout)


async def _collect(aiter) -> list[str]:
    return [f async for f in aiter]


# ── detach survival ─────────────────────────────────────────────────────────


class TestDetachSurvival:
    async def test_job_completes_and_persists_with_no_subscriber(
        self, api, monkeypatch
    ):
        # Nobody ever subscribes (the client "left"): the job must still run to
        # completion and persist the assistant message.
        install_stream(monkeypatch, ["Hello ", "world"])
        lease = make_lease("m", "http://engine:1")
        use_leases(monkeypatch, [lease])
        conv_id = make_conversation()

        job = chat_job_manager.start(
            conversation_id=conv_id,
            user_id=1,
            lease=lease,
            model_slug="m",
            messages=[],
        )
        await wait_done(job)

        assert job.state == "done"
        assert "".join(job.collected) == "Hello world"
        with db_module.read_session() as db:
            rows = db.exec(
                select(ChatMessage).where(ChatMessage.conversation_id == conv_id)
            ).all()
        assert [r.content for r in rows] == ["Hello world"]
        assert job.assistant_message_id == rows[0].id

    async def test_post_exchange_runs_after_the_client_is_gone(
        self, api, monkeypatch
    ):
        install_stream(monkeypatch, ["hi"])
        lease = make_lease("m", "http://engine:1")
        use_leases(monkeypatch, [lease])
        conv_id = make_conversation()
        called: list[str] = []

        async def post_exchange(text: str):
            called.append(text)

        monkeypatch.setattr(
            chat_jobs.memory,
            "schedule_background",
            lambda coro: asyncio.ensure_future(coro),
        )
        job = chat_job_manager.start(
            conversation_id=conv_id,
            user_id=1,
            lease=lease,
            model_slug="m",
            messages=[],
            post_exchange=post_exchange,
        )
        await wait_done(job)
        await asyncio.sleep(0)  # let the scheduled coro run
        assert called == ["hi"]


# ── re-attach ───────────────────────────────────────────────────────────────


class TestReattach:
    async def test_subscribe_after_completion_replays_every_frame(
        self, api, monkeypatch
    ):
        install_stream(monkeypatch, ["a", "b"])
        lease = make_lease("m", "http://engine:1")
        use_leases(monkeypatch, [lease])
        conv_id = make_conversation()
        job = chat_job_manager.start(
            conversation_id=conv_id, user_id=1, lease=lease, model_slug="m", messages=[]
        )
        await wait_done(job)

        frames = [f async for f in job.subscribe()]
        # both deltas, [DONE], and the forge.done frame — nothing lost.
        assert frame("a") in frames and frame("b") in frames
        assert any('"forge": "done"' in f for f in frames)

    async def test_two_live_subscribers_each_get_all_frames(self, api, monkeypatch):
        hold = asyncio.Event()
        install_stream(monkeypatch, ["x", "y"], hold=hold)
        lease = make_lease("m", "http://engine:1")
        use_leases(monkeypatch, [lease])
        conv_id = make_conversation()
        job = chat_job_manager.start(
            conversation_id=conv_id, user_id=1, lease=lease, model_slug="m", messages=[]
        )

        got_a: list[str] = []
        got_b: list[str] = []

        async def drain(sink):
            async for f in job.subscribe():
                sink.append(f)

        task_a = asyncio.ensure_future(drain(got_a))
        await asyncio.sleep(0.02)  # both attach while running
        task_b = asyncio.ensure_future(drain(got_b))
        await asyncio.sleep(0.02)
        hold.set()
        await asyncio.wait_for(asyncio.gather(task_a, task_b), 3.0)

        for got in (got_a, got_b):
            assert frame("x") in got and frame("y") in got
            assert any('"forge": "done"' in f for f in got)


# ── load balancing ──────────────────────────────────────────────────────────


class TestLoadBalancing:
    def test_selects_the_least_loaded_lease_for_the_slug(self, api, monkeypatch):
        a = make_lease("m", "http://a")
        b = make_lease("m", "http://b")  # same model on two GPUs
        use_leases(monkeypatch, [a, b])
        mgr = ChatJobManager()

        # Fake one running job pinned to lease a.
        busy = chat_jobs.ChatJob("c-busy", 1, a)
        busy.state = "running"
        mgr._jobs["c-busy"] = busy

        assert mgr.select_lease("m").base_url == "http://b"

    def test_pinned_slug_only_matches_its_lease(self, api, monkeypatch):
        a = make_lease("alpha", "http://a")
        b = make_lease("beta", "http://b")
        use_leases(monkeypatch, [a, b])
        mgr = ChatJobManager()
        assert mgr.select_lease("beta").base_url == "http://b"
        assert mgr.select_lease("missing") is None

    def test_no_ready_lease_returns_none(self, api, monkeypatch):
        use_leases(monkeypatch, [])
        assert ChatJobManager().select_lease("m") is None

    def test_airllm_capacity_is_one(self):
        assert lease_capacity(make_lease("m", "http://a", engine=EngineKind.airllm)) == 1


# ── queueing (no oversubscription) ──────────────────────────────────────────


class TestQueueing:
    async def test_second_job_on_a_full_single_slot_lane_is_queued(
        self, api, monkeypatch
    ):
        hold = asyncio.Event()
        install_stream(monkeypatch, ["one"], hold=hold)
        lease = make_lease("m", "http://air", engine=EngineKind.airllm)  # capacity 1
        use_leases(monkeypatch, [lease])
        conv_a = make_conversation()
        conv_b = make_conversation()

        job_a = chat_job_manager.start(
            conversation_id=conv_a, user_id=1, lease=lease, model_slug="m", messages=[]
        )
        # Let A acquire the single slot and start running.
        for _ in range(50):
            await asyncio.sleep(0.01)
            if job_a.state == "running":
                break
        assert job_a.state == "running"

        job_b = chat_job_manager.start(
            conversation_id=conv_b, user_id=1, lease=lease, model_slug="m", messages=[]
        )
        await asyncio.sleep(0.02)
        # B cannot run yet — it announced 'queued' and is waiting for the slot.
        assert job_b.state == "queued"
        assert any('"forge": "queued"' in f for f in job_b.frames)

        hold.set()  # A finishes, frees the slot; B proceeds
        await wait_done(job_a)
        await wait_done(job_b)
        assert job_b.state == "done"


# ── wedged engine (safety net) ──────────────────────────────────────────────


class TestWedgedEngine:
    async def test_silent_engine_is_aborted_frees_slot_and_persists_partial(
        self, api, monkeypatch
    ):
        # An engine that emits a token then goes silent forever must not hang
        # the job, its lease slot, or its subscribers: the idle timeout aborts
        # it, the partial reply is persisted, and the lane is freed.
        from app.config import get_settings

        monkeypatch.setattr(get_settings(), "chat_stream_idle_timeout_s", 0.15)
        stall = asyncio.Event()  # never set — the engine wedges

        async def wedged(base_url, model_slug, messages, collected):
            collected.append("partial ")
            yield frame("partial ")
            await stall.wait()
            yield "data: [DONE]\n\n"  # never reached

        monkeypatch.setattr(chat_service, "stream_completion", wedged)
        lease = make_lease("m", "http://engine:1")
        use_leases(monkeypatch, [lease])
        conv_id = make_conversation()

        job = chat_job_manager.start(
            conversation_id=conv_id, user_id=1, lease=lease, model_slug="m", messages=[]
        )
        # A subscriber attaches and must terminate (not hang) when aborted.
        frames = await asyncio.wait_for(_collect(job.subscribe()), 3.0)

        assert job.state == "error"
        assert "no output" in job.error
        assert frame("partial ") in frames
        assert any('"forge": "done"' in f for f in frames)
        with db_module.read_session() as db:
            rows = db.exec(
                select(ChatMessage).where(ChatMessage.conversation_id == conv_id)
            ).all()
        assert [r.content for r in rows] == ["partial "]  # server-side, not lost

        # The slot was released: a fresh job on the same lease runs to completion.
        install_stream(monkeypatch, ["ok"])
        conv2 = make_conversation()
        job2 = chat_job_manager.start(
            conversation_id=conv2, user_id=1, lease=lease, model_slug="m", messages=[]
        )
        await wait_done(job2)
        assert job2.state == "done"


class TestEngineErrorState:
    async def test_engine_error_frame_ends_job_in_error_state(self, api, monkeypatch):
        # stream_completion reports a connect/HTTP failure as a data frame and
        # returns normally; the job must still end 'error', not a false 'done'.
        async def erroring(base_url, model_slug, messages, collected):
            yield 'data: {"error": "engine error 500: boom"}\n\n'

        monkeypatch.setattr(chat_service, "stream_completion", erroring)
        lease = make_lease("m", "http://engine:1")
        use_leases(monkeypatch, [lease])
        conv_id = make_conversation()
        job = chat_job_manager.start(
            conversation_id=conv_id, user_id=1, lease=lease, model_slug="m", messages=[]
        )
        await wait_done(job)
        assert job.state == "error"
        assert "boom" in job.error


# ── reattach guard (no silent message loss) ─────────────────────────────────


class TestReattachGuard:
    def test_matching_turn_reattaches_to_the_running_job(
        self, api, auth_headers, monkeypatch
    ):
        lease = make_lease("m", "http://engine:1")
        use_leases(monkeypatch, [lease])
        conv = api.post("/api/chat/conversations", json={}, headers=auth_headers).json()
        me = api.get("/api/users/me", headers=auth_headers).json()["id"]

        # A job answering the turn "first", already finished so subscribe() ends.
        job = chat_jobs.ChatJob(conv["id"], me, lease)
        job.state = "running"
        job.turn_key = "[]\nfirst"
        job.frames = [frame("hi"), 'data: {"forge": "done"}\n\n']
        job._done.set()
        chat_job_manager._jobs[conv["id"]] = job

        r = api.post(
            f"/api/chat/conversations/{conv['id']}/messages",
            json={"content": "first"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert "hi" in r.text  # replayed the in-flight stream, no second job

    def test_different_message_while_running_is_rejected_not_dropped(
        self, api, auth_headers, monkeypatch
    ):
        lease = make_lease("m", "http://engine:1")
        use_leases(monkeypatch, [lease])
        conv = api.post("/api/chat/conversations", json={}, headers=auth_headers).json()
        me = api.get("/api/users/me", headers=auth_headers).json()["id"]

        job = chat_jobs.ChatJob(conv["id"], me, lease)
        job.state = "running"
        job.turn_key = "[]\nfirst"
        chat_job_manager._jobs[conv["id"]] = job

        # A genuinely different message can't run until the reply lands — it must
        # 409, not be swallowed by the reattach shortcut.
        r = api.post(
            f"/api/chat/conversations/{conv['id']}/messages",
            json={"content": "a totally different question"},
            headers=auth_headers,
        )
        assert r.status_code == 409
        with db_module.read_session() as db:
            rows = db.exec(
                select(ChatMessage).where(
                    ChatMessage.conversation_id == conv["id"]
                )
            ).all()
        assert rows == []  # nothing persisted for the rejected turn


# ── active endpoint ─────────────────────────────────────────────────────────


class TestActiveEndpoint:
    def test_active_reports_only_the_callers_running_conversations(
        self, api, auth_headers, second_user_headers, monkeypatch
    ):
        lease = make_lease("m", "http://engine:1")
        use_leases(monkeypatch, [lease])
        me = api.get("/api/users/me", headers=auth_headers).json()["id"]
        conv = api.post("/api/chat/conversations", json={}, headers=auth_headers).json()

        # A running job for my conversation, parked directly in the registry
        # (no live stream needed — GET /active reads the registry).
        job = chat_jobs.ChatJob(conv["id"], me, lease)
        job.state = "running"
        chat_job_manager._jobs[conv["id"]] = job
        # And a running job that belongs to nobody's owned set here.
        stray = chat_jobs.ChatJob("someone-elses", 999, lease)
        stray.state = "running"
        chat_job_manager._jobs["someone-elses"] = stray

        mine = api.get("/api/chat/active", headers=auth_headers).json()
        assert [a["conversation_id"] for a in mine] == [conv["id"]]
        assert mine[0]["state"] == "running"

        # The other user sees none of my generations.
        theirs = api.get("/api/chat/active", headers=second_user_headers).json()
        assert theirs == []


# ── real stop (cancel) ──────────────────────────────────────────────────────


class TestCancel:
    async def test_cancel_stops_the_job_and_persists_the_partial(
        self, api, monkeypatch
    ):
        hold = asyncio.Event()  # engine "hangs" mid-generation, never finishes
        install_stream(monkeypatch, ["partial "], hold=hold)
        lease = make_lease("m", "http://engine:1")
        use_leases(monkeypatch, [lease])
        conv_id = make_conversation()
        job = chat_job_manager.start(
            conversation_id=conv_id, user_id=1, lease=lease, model_slug="m", messages=[]
        )
        for _ in range(100):
            await asyncio.sleep(0.01)
            if job.collected:
                break

        status = chat_job_manager.cancel(conv_id)
        assert status is not None
        await wait_done(job)

        assert job.state == "error"
        assert job.error == "cancelled"
        with db_module.read_session() as db:
            rows = db.exec(
                select(ChatMessage).where(ChatMessage.conversation_id == conv_id)
            ).all()
        assert [r.content for r in rows] == ["partial "]  # partial kept
        # Terminal: a re-attach replays and ENDS instead of resuming.
        frames = [f async for f in job.subscribe()]
        assert any('"forge": "done"' in f for f in frames)

    async def test_cancel_with_nothing_running_returns_none(self, api):
        assert chat_job_manager.cancel("nope") is None

    def test_cancel_endpoint_requires_ownership(
        self, api, auth_headers, second_user_headers, monkeypatch
    ):
        lease = make_lease("m", "http://engine:1")
        use_leases(monkeypatch, [lease])
        conv = api.post("/api/chat/conversations", json={}, headers=auth_headers).json()
        r = api.post(
            f"/api/chat/conversations/{conv['id']}/cancel", headers=second_user_headers
        )
        assert r.status_code == 404
        # Owner with nothing generating gets an honest 409.
        r = api.post(
            f"/api/chat/conversations/{conv['id']}/cancel", headers=auth_headers
        )
        assert r.status_code == 409
