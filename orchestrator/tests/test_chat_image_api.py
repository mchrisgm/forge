"""POST /api/chat/image: validation, ownership, generated-Upload metadata,
persistence of the user+assistant exchange with the image attached to the
assistant turn, and failure atomicity — image_service.generate is stubbed so
no engine or connector is ever dialed. Plus /api/chat/status's image lane."""

import json

import pytest
from sqlmodel import select

from app import db as db_module
from app.models import ChatMessage, Conversation, EngineKind, Upload
from app.services import image_service, memory
from app.services.engine_manager import Lease, engine_manager

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24


def user_id_of(api, headers) -> int:
    return api.get("/api/users/me", headers=headers).json()["id"]


def create_conversation(api, headers, **body) -> dict:
    resp = api.post("/api/chat/conversations", json=body, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def messages_of(conversation_id: str) -> list[ChatMessage]:
    with db_module.read_session() as db:
        return sorted(
            db.exec(
                select(ChatMessage).where(
                    ChatMessage.conversation_id == conversation_id
                )
            ).all(),
            key=lambda m: m.id or 0,
        )


@pytest.fixture
def generate_stub(monkeypatch) -> list[dict]:
    """Replace image_service.generate with a recorder returning a PNG."""
    calls: list[dict] = []

    async def fake_generate(user_id, prompt, provider, size):
        calls.append(
            {"user_id": user_id, "prompt": prompt, "provider": provider, "size": size}
        )
        return PNG, "image/png"

    monkeypatch.setattr(image_service, "generate", fake_generate)
    return calls


class TestValidation:
    def test_requires_auth(self, api, generate_stub):
        assert api.post("/api/chat/image", json={"prompt": "a fox"}).status_code == 401
        assert generate_stub == []

    def test_blank_prompt_is_400(self, api, auth_headers, generate_stub):
        resp = api.post(
            "/api/chat/image", json={"prompt": "   "}, headers=auth_headers
        )
        assert resp.status_code == 400
        assert generate_stub == []

    @pytest.mark.parametrize(
        "size", ["1024", "8x8", "1024x1024x2", "axb", "10000x10000", ""]
    )
    def test_malformed_size_is_400(self, api, auth_headers, generate_stub, size):
        resp = api.post(
            "/api/chat/image",
            json={"prompt": "a fox", "size": size},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "size" in resp.json()["detail"]
        assert generate_stub == []

    def test_unknown_conversation_is_404(self, api, auth_headers, generate_stub):
        resp = api.post(
            "/api/chat/image",
            json={"prompt": "a fox", "conversation_id": "no-such-id"},
            headers=auth_headers,
        )
        assert resp.status_code == 404
        assert generate_stub == []

    def test_other_users_conversation_is_404(
        self, api, auth_headers, second_user_headers, generate_stub
    ):
        theirs = create_conversation(api, second_user_headers)
        resp = api.post(
            "/api/chat/image",
            json={"prompt": "a fox", "conversation_id": theirs["id"]},
            headers=auth_headers,
        )
        assert resp.status_code == 404
        assert generate_stub == []
        assert messages_of(theirs["id"]) == []


class TestStandaloneGeneration:
    def test_returns_the_generated_upload_and_persists_no_messages(
        self, api, auth_headers, generate_stub
    ):
        resp = api.post(
            "/api/chat/image", json={"prompt": "A red fox"}, headers=auth_headers
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        upload = body["upload"]
        assert upload["generated"] is True
        assert upload["prompt"] == "A red fox"
        assert upload["mime"] == "image/png"
        assert upload["kind"] == "image"
        assert upload["filename"] == "a-red-fox.png"
        assert upload["size_bytes"] == len(PNG)
        assert body["conversation_id"] is None
        assert body["user_message_id"] is None
        assert body["assistant_message_id"] is None

        # Provider defaults were forwarded to the service for this user.
        assert generate_stub == [
            {
                "user_id": user_id_of(api, auth_headers),
                "prompt": "A red fox",
                "provider": "local",
                "size": "1024x1024",
            }
        ]

        # No chat rows were created — the upload stands alone and serves.
        with db_module.read_session() as db:
            assert db.exec(select(ChatMessage)).all() == []
            assert db.exec(select(Conversation)).all() == []
        fetched = api.get(f"/api/files/{upload['id']}", headers=auth_headers)
        assert fetched.status_code == 200
        assert fetched.content == PNG

    def test_prompt_is_trimmed_and_provider_size_forwarded(
        self, api, auth_headers, generate_stub
    ):
        resp = api.post(
            "/api/chat/image",
            json={"prompt": "  a fox  ", "provider": "higgsfield", "size": "512x768"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        (call,) = generate_stub
        assert call["prompt"] == "a fox"
        assert call["provider"] == "higgsfield"
        assert call["size"] == "512x768"


class TestConversationRecording:
    def test_persists_the_exchange_with_the_image_attached(
        self, api, auth_headers, generate_stub
    ):
        conversation = create_conversation(api, auth_headers)
        resp = api.post(
            "/api/chat/image",
            json={"prompt": "A red fox", "conversation_id": conversation["id"]},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["conversation_id"] == conversation["id"]

        user_turn, assistant_turn = messages_of(conversation["id"])
        assert body["user_message_id"] == user_turn.id
        assert body["assistant_message_id"] == assistant_turn.id
        assert user_turn.role == "user"
        assert user_turn.content == "A red fox"
        assert user_turn.token_estimate == memory.estimate_tokens("A red fox")
        assert assistant_turn.role == "assistant"
        assert assistant_turn.content == "[Generated image: A red fox]"
        assert json.loads(assistant_turn.attachments_json) == [body["upload"]["id"]]

        # The conversation view returns the attachment meta on the assistant
        # turn, and updated_at was bumped by the exchange.
        after = api.get(
            f"/api/chat/conversations/{conversation['id']}", headers=auth_headers
        ).json()
        (attachment,) = after["messages"][1]["attachments"]
        assert attachment["id"] == body["upload"]["id"]
        assert attachment["generated"] is True
        assert attachment["prompt"] == "A red fox"
        assert attachment["mime"] == "image/png"
        assert after["updated_at"] >= conversation["updated_at"]

    def test_generation_failure_propagates_and_persists_nothing(
        self, api, auth_headers, monkeypatch
    ):
        from fastapi import HTTPException

        async def failing_generate(user_id, prompt, provider, size):
            raise HTTPException(502, "connector 'higgsfield': tool failed")

        monkeypatch.setattr(image_service, "generate", failing_generate)
        conversation = create_conversation(api, auth_headers)
        resp = api.post(
            "/api/chat/image",
            json={"prompt": "a fox", "conversation_id": conversation["id"]},
            headers=auth_headers,
        )
        assert resp.status_code == 502
        assert "higgsfield" in resp.json()["detail"]
        assert messages_of(conversation["id"]) == []
        with db_module.read_session() as db:
            assert db.exec(select(Upload)).all() == []


class TestChatStatusImageLane:
    def test_ready_imagegen_lease_reports_as_image_not_serving(
        self, api, auth_headers
    ):
        lease = Lease(
            model_id=3,
            model_name="SDXL Turbo",
            model_slug="sdxl-turbo",
            engine=EngineKind.imagegen,
            state="ready",
            base_url="http://forge-engine-imagegen-gpu0:8084/v1",
        )
        engine_manager._leases = {0: lease}
        body = api.get("/api/chat/status", headers=auth_headers).json()
        assert body["serving"] == []
        assert body["image"]["model_slug"] == "sdxl-turbo"
        assert body["image"]["engine"] == "imagegen"
