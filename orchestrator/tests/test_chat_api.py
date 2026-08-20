"""Chat API tests: conversation CRUD with per-user ownership isolation,
message streaming with the engine mocked, persistence + token estimates,
temporary (incognito) chats that store nothing and read no memory, lease
resolution failures, and attachment ownership enforcement."""

import json

import pytest
from sqlmodel import select

from app import db as db_module
from app.models import ChatMessage, Conversation, EngineKind
from app.services import chat_service, memory
from app.services.engine_manager import Lease, engine_manager

from .conftest import add_model

CHAT_SLUG = "chat-model"
STREAM_PIECES = ("Hello", " world")
STREAM_TEXT = "".join(STREAM_PIECES)


def serve(slug: str = CHAT_SLUG, state: str = "ready", model_id: int | None = None) -> Lease:
    # A real serving lease always has a matching downloaded ModelEntry — create
    # one (unless the caller pinned an id) so slug resolution and auto-routing,
    # which pick from *downloaded* models, can find it.
    if model_id is None:
        model_id = add_model(display_name=slug.replace("-", " ").title(), params_b=14.0)
    lease = Lease(
        model_id=model_id,
        model_name="Chat Model",
        model_slug=slug,
        engine=EngineKind.llamacpp,
        gpu_ids=[0],
        state=state,
        container_id=f"c-{slug}",
        base_url="http://forge-engine-llamacpp-gpu0:8081/v1",
    )
    engine_manager._leases = {0: lease}
    return lease


def sse_payloads(text: str) -> list[str]:
    return [
        chunk[len("data: "):]
        for chunk in text.split("\n\n")
        if chunk.startswith("data: ")
    ]


def user_id_of(api, headers) -> int:
    return api.get("/api/users/me", headers=headers).json()["id"]


@pytest.fixture
def stream_stub(monkeypatch) -> list[dict]:
    """Replace chat_service.stream_completion with an async generator that
    yields OpenAI-style SSE frames and fills `collected` like the real one."""
    calls: list[dict] = []

    async def fake_stream(base_url, model_slug, messages, collected):
        calls.append(
            {"base_url": base_url, "model_slug": model_slug, "messages": messages}
        )
        for piece in STREAM_PIECES:
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
def scheduled(monkeypatch) -> list:
    """Capture (and cancel) the post-exchange background work so tests never
    dial the model for titles / compression / extraction."""
    coros: list = []

    def fake_schedule(coro):
        coros.append(coro)
        coro.close()

    monkeypatch.setattr(memory, "schedule_background", fake_schedule)
    return coros


@pytest.fixture
def retrieve_spy(monkeypatch) -> list:
    calls: list = []

    def fake_retrieve(user_id, query, token_budget=None):
        calls.append((user_id, query))
        return []

    monkeypatch.setattr(memory, "retrieve", fake_retrieve)
    return calls


def create_conversation(api, headers, **body) -> dict:
    resp = api.post("/api/chat/conversations", json=body, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── conversation CRUD ───────────────────────────────────────────────────────


class TestConversationCrud:
    def test_create_defaults(self, api, auth_headers):
        conversation = create_conversation(api, auth_headers)
        assert conversation["title"] == "New chat"
        assert conversation["model_slug"] == ""
        assert conversation["thinking"] == "auto"
        assert conversation["memory_enabled"] is True
        assert conversation["archived"] is False
        assert conversation["summarized_until"] == 0

    def test_create_with_fields(self, api, auth_headers):
        conversation = create_conversation(
            api, auth_headers, title="  Big plans  ", model_slug="m1", thinking="high"
        )
        assert conversation["title"] == "Big plans"
        assert conversation["model_slug"] == "m1"
        assert conversation["thinking"] == "high"

    def test_list_excludes_archived_by_default(self, api, auth_headers):
        keep = create_conversation(api, auth_headers, title="keep")
        gone = create_conversation(api, auth_headers, title="gone")
        api.patch(
            f"/api/chat/conversations/{gone['id']}",
            json={"archived": True},
            headers=auth_headers,
        )
        listed = api.get("/api/chat/conversations", headers=auth_headers).json()
        assert [c["id"] for c in listed] == [keep["id"]]
        archived = api.get(
            "/api/chat/conversations",
            params={"archived": True},
            headers=auth_headers,
        ).json()
        assert [c["id"] for c in archived] == [gone["id"]]
        # The heavy rolling summary never rides the listing payload.
        assert "summary" not in listed[0]

    def test_get_includes_messages(self, api, auth_headers):
        conversation = create_conversation(api, auth_headers)
        body = api.get(
            f"/api/chat/conversations/{conversation['id']}", headers=auth_headers
        ).json()
        assert body["id"] == conversation["id"]
        assert body["messages"] == []

    def test_patch_updates_fields(self, api, auth_headers):
        conversation = create_conversation(api, auth_headers)
        resp = api.patch(
            f"/api/chat/conversations/{conversation['id']}",
            json={"title": "Renamed", "thinking": "off", "memory_enabled": False},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["title"] == "Renamed"
        assert body["thinking"] == "off"
        assert body["memory_enabled"] is False
        assert body["updated_at"] >= conversation["updated_at"]

    def test_delete_removes_conversation_and_messages(self, api, auth_headers):
        conversation = create_conversation(api, auth_headers)
        with db_module.write_session() as db:
            db.add(
                ChatMessage(
                    conversation_id=conversation["id"], role="user", content="hi"
                )
            )
        resp = api.delete(
            f"/api/chat/conversations/{conversation['id']}", headers=auth_headers
        )
        assert resp.status_code == 200
        with db_module.read_session() as db:
            assert db.get(Conversation, conversation["id"]) is None
            assert (
                db.exec(
                    select(ChatMessage).where(
                        ChatMessage.conversation_id == conversation["id"]
                    )
                ).all()
                == []
            )

    def test_unknown_conversation_is_404(self, api, auth_headers):
        assert (
            api.get(
                "/api/chat/conversations/no-such-id", headers=auth_headers
            ).status_code
            == 404
        )


class TestOwnershipIsolation:
    def test_other_users_conversation_is_404_everywhere(
        self, api, auth_headers, second_user_headers
    ):
        conversation = create_conversation(api, auth_headers)
        url = f"/api/chat/conversations/{conversation['id']}"
        assert api.get(url, headers=second_user_headers).status_code == 404
        assert (
            api.patch(
                url, json={"title": "stolen"}, headers=second_user_headers
            ).status_code
            == 404
        )
        assert api.delete(url, headers=second_user_headers).status_code == 404
        assert (
            api.post(
                f"{url}/messages",
                json={"content": "hi"},
                headers=second_user_headers,
            ).status_code
            == 404
        )
        # And nothing was deleted or renamed by those attempts.
        mine = api.get(url, headers=auth_headers).json()
        assert mine["title"] == "New chat"

    def test_listings_are_per_user(self, api, auth_headers, second_user_headers):
        create_conversation(api, auth_headers, title="mine")
        create_conversation(api, second_user_headers, title="theirs")
        mine = api.get("/api/chat/conversations", headers=auth_headers).json()
        theirs = api.get("/api/chat/conversations", headers=second_user_headers).json()
        assert [c["title"] for c in mine] == ["mine"]
        assert [c["title"] for c in theirs] == ["theirs"]


# ── messaging ───────────────────────────────────────────────────────────────


class TestSendMessage:
    def test_streams_frames_and_persists_the_exchange(
        self, api, auth_headers, stream_stub, scheduled, retrieve_spy
    ):
        lease = serve()
        # Explicit selection of the serving model: no routing narration, the
        # frame sequence is status → deltas → [DONE] → done.
        conversation = create_conversation(api, auth_headers, model_slug=CHAT_SLUG)
        resp = api.post(
            f"/api/chat/conversations/{conversation['id']}/messages",
            json={"content": "What is up?"},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"].startswith("text/event-stream")

        payloads = sse_payloads(resp.text)
        # A status frame narrates prompt processing, then the upstream frames
        # forwarded verbatim, then [DONE], then forge.done.
        status = json.loads(payloads[0])
        assert status["forge"] == "status"
        assert "processing" in status["detail"]
        deltas = [
            json.loads(p)["choices"][0]["delta"]["content"]
            for p in payloads[1:3]
        ]
        assert deltas == list(STREAM_PIECES)
        assert payloads[3] == "[DONE]"
        done = json.loads(payloads[4])
        assert done["forge"] == "done"
        assert done["conversation_id"] == conversation["id"]

        # Both turns persisted with token estimates.
        with db_module.read_session() as db:
            messages = sorted(
                db.exec(
                    select(ChatMessage).where(
                        ChatMessage.conversation_id == conversation["id"]
                    )
                ).all(),
                key=lambda m: m.id,
            )
        assert [m.role for m in messages] == ["user", "assistant"]
        assert messages[0].content == "What is up?"
        assert messages[0].token_estimate == memory.estimate_tokens("What is up?")
        assert messages[1].content == STREAM_TEXT
        assert messages[1].token_estimate == memory.estimate_tokens(STREAM_TEXT)
        assert done["assistant_message_id"] == messages[1].id

        # Conversation bookkeeping: keeps the explicit slug, bumped updated_at.
        after = api.get(
            f"/api/chat/conversations/{conversation['id']}", headers=auth_headers
        ).json()
        assert after["model_slug"] == lease.model_slug
        assert after["updated_at"] >= conversation["updated_at"]

        # The engine was dialed through the lease with persona + user turn.
        (call,) = stream_stub
        assert call["base_url"] == lease.base_url
        assert call["model_slug"] == lease.model_slug
        assert call["messages"][0]["role"] == "system"
        assert call["messages"][-1] == {"role": "user", "content": "What is up?"}

        # Memory was consulted and post-exchange work was scheduled.
        assert retrieve_spy == [(user_id_of(api, auth_headers), "What is up?")]
        assert len(scheduled) == 1

    def test_memory_disabled_conversation_skips_retrieval(
        self, api, auth_headers, stream_stub, scheduled, retrieve_spy
    ):
        serve()
        conversation = create_conversation(api, auth_headers, memory_enabled=False)
        resp = api.post(
            f"/api/chat/conversations/{conversation['id']}/messages",
            json={"content": "no memory please"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert retrieve_spy == []

    def test_empty_message_is_400(self, api, auth_headers, stream_stub):
        serve()
        conversation = create_conversation(api, auth_headers)
        resp = api.post(
            f"/api/chat/conversations/{conversation['id']}/messages",
            json={"content": "   "},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_409_when_no_model_downloaded(self, api, auth_headers, stream_stub):
        # Auto (the default) needs at least one downloaded model to route to.
        conversation = create_conversation(api, auth_headers)
        resp = api.post(
            f"/api/chat/conversations/{conversation['id']}/messages",
            json={"content": "anyone home?"},
            headers=auth_headers,
        )
        assert resp.status_code == 409
        assert resp.json()["detail"]["message"] == "no model is ready to route to"
        # Nothing was persisted for the failed send.
        with db_module.read_session() as db:
            assert (
                db.exec(
                    select(ChatMessage).where(
                        ChatMessage.conversation_id == conversation["id"]
                    )
                ).all()
                == []
            )

    def test_409_when_selected_model_not_downloaded(
        self, api, auth_headers, stream_stub
    ):
        # A conversation pinned to a model that no longer exists (deleted after
        # selection) refuses the send rather than silently routing elsewhere.
        serve()  # a different model is downloaded, but not the pinned one
        conversation = create_conversation(
            api, auth_headers, model_slug="ghost-model"
        )
        resp = api.post(
            f"/api/chat/conversations/{conversation['id']}/messages",
            json={"content": "anyone home?"},
            headers=auth_headers,
        )
        assert resp.status_code == 409
        assert resp.json()["detail"]["message"] == "that model isn't downloaded"
        with db_module.read_session() as db:
            assert (
                db.exec(
                    select(ChatMessage).where(
                        ChatMessage.conversation_id == conversation["id"]
                    )
                ).all()
                == []
            )

    def test_other_users_attachment_is_404(
        self, api, auth_headers, second_user_headers, stream_stub
    ):
        serve()
        upload = api.post(
            "/api/files",
            files={"file": ("notes.txt", b"my secret notes", "text/plain")},
            headers=second_user_headers,
        ).json()
        conversation = create_conversation(api, auth_headers)
        resp = api.post(
            f"/api/chat/conversations/{conversation['id']}/messages",
            json={"content": "leak it", "attachment_ids": [upload["id"]]},
            headers=auth_headers,
        )
        assert resp.status_code == 404
        assert upload["id"] in resp.json()["detail"]
        with db_module.read_session() as db:
            assert (
                db.exec(
                    select(ChatMessage).where(
                        ChatMessage.conversation_id == conversation["id"]
                    )
                ).all()
                == []
            )

    def test_own_attachment_is_inlined_and_recorded(
        self, api, auth_headers, stream_stub, scheduled, retrieve_spy
    ):
        serve()
        upload = api.post(
            "/api/files",
            files={"file": ("snippet.py", b"print('hello')\n", "text/x-python")},
            headers=auth_headers,
        ).json()
        conversation = create_conversation(api, auth_headers)
        resp = api.post(
            f"/api/chat/conversations/{conversation['id']}/messages",
            json={"content": "review this", "attachment_ids": [upload["id"]]},
            headers=auth_headers,
        )
        assert resp.status_code == 200

        (call,) = stream_stub
        prompt = call["messages"][-1]["content"]
        assert "snippet.py" in prompt and "print('hello')" in prompt

        body = api.get(
            f"/api/chat/conversations/{conversation['id']}", headers=auth_headers
        ).json()
        user_turn = body["messages"][0]
        assert [a["id"] for a in user_turn["attachments"]] == [upload["id"]]
        assert user_turn["attachments"][0]["filename"] == "snippet.py"
        assert user_turn["attachments"][0]["kind"] == "text"


# ── temporary (incognito) chat ──────────────────────────────────────────────


class TestTemporaryChat:
    def test_streams_but_persists_nothing_and_reads_no_memory(
        self, api, auth_headers, stream_stub, scheduled, retrieve_spy
    ):
        serve()
        resp = api.post(
            "/api/chat/temporary",
            json={
                "messages": [
                    {"role": "user", "content": "earlier"},
                    {"role": "assistant", "content": "reply"},
                    {"role": "user", "content": "secret question"},
                ]
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        payloads = sse_payloads(resp.text)
        assert json.loads(payloads[-1]) == {"forge": "done", "temporary": True}

        # NOTHING was stored: no conversation, no messages, no memory reads,
        # no background extraction.
        with db_module.read_session() as db:
            assert db.exec(select(Conversation)).all() == []
            assert db.exec(select(ChatMessage)).all() == []
        assert retrieve_spy == []
        assert scheduled == []

        # The client-kept history still reached the engine.
        (call,) = stream_stub
        contents = [m.get("content") for m in call["messages"]]
        assert "earlier" in contents and contents[-1] == "secret question"

    def test_empty_messages_is_400(self, api, auth_headers, stream_stub):
        serve()
        resp = api.post(
            "/api/chat/temporary", json={"messages": []}, headers=auth_headers
        )
        assert resp.status_code == 400

    def test_409_when_nothing_is_serving(self, api, auth_headers, stream_stub):
        resp = api.post(
            "/api/chat/temporary",
            json={"messages": [{"role": "user", "content": "hi"}]},
            headers=auth_headers,
        )
        assert resp.status_code == 409
        assert resp.json()["detail"]["message"] == "no model is serving this chat"


# ── chat status ─────────────────────────────────────────────────────────────


class TestChatStatus:
    def test_requires_auth(self, api):
        assert api.get("/api/chat/status").status_code == 401

    def test_lists_serving_leases(self, api, auth_headers):
        serve()
        body = api.get("/api/chat/status", headers=auth_headers).json()
        assert [lease["model_slug"] for lease in body["serving"]] == [CHAT_SLUG]

    def test_empty_when_nothing_is_ready(self, api, auth_headers):
        body = api.get("/api/chat/status", headers=auth_headers).json()
        assert body == {
            "serving": [],
            "image": None,
            "auto": {"available": False, "router_model": "", "router_ready": False},
        }
