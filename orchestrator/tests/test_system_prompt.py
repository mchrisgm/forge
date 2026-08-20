"""The chat system prompt: a strong built-in default injected for every text
model, admin-editable on the Settings page, restorable to default."""

from app.db import set_setting
from app.models import User
from app.services import chat_service
from app.services.chat_service import DEFAULT_SYSTEM_PROMPT, build_system_prompt


def make_user() -> User:
    return User(id=1, username="t", password_hash="x", personal_instructions="")


class TestCurrentPrompt:
    def test_default_applies_when_no_override(self, api):
        set_setting("chat_system_prompt", "")
        messages = build_system_prompt(make_user(), memory_entries=[])
        assert messages[0]["role"] == "system"
        assert DEFAULT_SYSTEM_PROMPT in messages[0]["content"]
        assert "direct answers" in DEFAULT_SYSTEM_PROMPT.lower()

    def test_override_replaces_the_default(self, api):
        set_setting("chat_system_prompt", "You are a pirate. Answer in rhyme.")
        try:
            messages = build_system_prompt(make_user(), memory_entries=[])
            assert "You are a pirate" in messages[0]["content"]
            assert DEFAULT_SYSTEM_PROMPT not in messages[0]["content"]
        finally:
            set_setting("chat_system_prompt", "")

    def test_whitespace_override_falls_back_to_default(self, api):
        set_setting("chat_system_prompt", "   \n  ")
        try:
            assert chat_service.current_system_prompt() == DEFAULT_SYSTEM_PROMPT
        finally:
            set_setting("chat_system_prompt", "")


class TestSettingsSurface:
    def test_get_exposes_prompt_default_and_customized_flag(self, api, auth_headers):
        body = api.get("/api/settings", headers=auth_headers).json()
        assert body["chat_system_prompt"] == DEFAULT_SYSTEM_PROMPT
        assert body["chat_system_prompt_customized"] is False
        assert body["chat_system_prompt_default"] == DEFAULT_SYSTEM_PROMPT

    def test_patch_sets_and_empty_restores(self, api, auth_headers):
        r = api.patch(
            "/api/settings",
            json={"chat_system_prompt": "Be terse."},
            headers=auth_headers,
        )
        assert r.json()["chat_system_prompt"] == "Be terse."
        assert r.json()["chat_system_prompt_customized"] is True

        r = api.patch(
            "/api/settings", json={"chat_system_prompt": ""}, headers=auth_headers
        )
        assert r.json()["chat_system_prompt"] == DEFAULT_SYSTEM_PROMPT
        assert r.json()["chat_system_prompt_customized"] is False

    def test_saving_the_default_verbatim_counts_as_not_customized(
        self, api, auth_headers
    ):
        r = api.patch(
            "/api/settings",
            json={"chat_system_prompt": DEFAULT_SYSTEM_PROMPT},
            headers=auth_headers,
        )
        assert r.json()["chat_system_prompt_customized"] is False

    def test_non_admin_cannot_change_the_prompt(
        self, api, auth_headers, second_user_headers
    ):
        r = api.patch(
            "/api/settings",
            json={"chat_system_prompt": "evil"},
            headers=second_user_headers,
        )
        assert r.status_code == 403
