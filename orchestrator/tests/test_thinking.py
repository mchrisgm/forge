"""Thinking-level directives (app/services/thinking.py): family detection,
per-family level mappings, and injection into prompts / OpenAI message lists.
Pure logic — no app, DB, or docker involved."""

import pytest

from app.models import EngineKind, ModelEntry, ThinkingLevel
from app.services.thinking import (
    ThinkingDirectives,
    apply_to_openai_messages,
    apply_to_prompt,
    directives_for,
    model_thinking_family,
)


def make_model(**overrides) -> ModelEntry:
    defaults = dict(
        id=1,
        hf_repo="Qwen/Qwen2.5-Coder-14B-Instruct-GGUF",
        display_name="Qwen2.5 Coder 14B Instruct",
        family="",
        engine=EngineKind.llamacpp,
    )
    defaults.update(overrides)
    return ModelEntry(**defaults)


def qwen3_model() -> ModelEntry:
    return make_model(
        hf_repo="Qwen/Qwen3-Coder-30B-A3B-Instruct",
        display_name="Qwen3 Coder 30B",
    )


def gpt_oss_model() -> ModelEntry:
    return make_model(hf_repo="openai/gpt-oss-20b", display_name="GPT-OSS 20B")


def r1_model() -> ModelEntry:
    return make_model(
        hf_repo="deepseek-ai/DeepSeek-R1-Distill-Qwen-14B",
        display_name="DeepSeek R1 Distill 14B",
    )


# ── family detection ────────────────────────────────────────────────────────


class TestFamilyDetection:
    def test_qwen3_repo(self):
        assert model_thinking_family(qwen3_model()) == "qwen3"

    def test_gpt_oss(self):
        assert model_thinking_family(gpt_oss_model()) == "gpt-oss"

    def test_deepseek_r1_is_always_thinking(self):
        assert model_thinking_family(r1_model()) == "always-thinking"

    def test_qwen25_is_generic(self):
        # Qwen2.5 is NOT Qwen3 — must not match the soft-switch family.
        assert model_thinking_family(make_model()) == "generic"

    def test_family_field_alone_can_classify(self):
        model = make_model(
            hf_repo="somewhere/some-coder", display_name="Some Coder", family="qwen3"
        )
        assert model_thinking_family(model) == "qwen3"


# ── directives per family and level ─────────────────────────────────────────


class TestDirectives:
    @pytest.mark.parametrize(
        "model", [make_model(), qwen3_model(), gpt_oss_model(), r1_model()]
    )
    def test_auto_means_no_directives(self, model):
        directives = directives_for(model, ThinkingLevel.auto)
        assert directives == ThinkingDirectives()
        assert directives.system == ""
        assert directives.user_suffix == ""

    def test_qwen3_off_appends_no_think(self):
        directives = directives_for(qwen3_model(), ThinkingLevel.off)
        assert directives.user_suffix == " /no_think"
        assert directives.system == ""

    def test_qwen3_high_appends_think_with_system_nudge(self):
        directives = directives_for(qwen3_model(), ThinkingLevel.high)
        assert directives.user_suffix == " /think"
        assert directives.system != ""

    def test_qwen3_low_also_uses_think_switch(self):
        directives = directives_for(qwen3_model(), ThinkingLevel.low)
        assert directives.user_suffix == " /think"
        assert "brief" in directives.system.lower()

    @pytest.mark.parametrize(
        ("level", "effort"),
        [
            (ThinkingLevel.off, "low"),
            (ThinkingLevel.low, "low"),
            (ThinkingLevel.high, "high"),
        ],
    )
    def test_gpt_oss_reasoning_line(self, level, effort):
        directives = directives_for(gpt_oss_model(), level)
        assert directives.system == f"Reasoning: {effort}"
        assert directives.user_suffix == ""

    @pytest.mark.parametrize(
        "level", [ThinkingLevel.off, ThinkingLevel.low, ThinkingLevel.high]
    )
    def test_generic_levels_get_plain_language_system(self, level):
        directives = directives_for(make_model(), level)
        assert directives.system != ""
        assert directives.user_suffix == ""

    def test_always_thinking_off_maps_to_brief_not_no_think(self):
        # R1-style models cannot switch reasoning off; "off" degrades to brief.
        directives = directives_for(r1_model(), ThinkingLevel.off)
        assert directives.user_suffix == ""
        assert directives.system != ""
        assert "/no_think" not in directives.system

    def test_as_dict_shape(self):
        directives = directives_for(qwen3_model(), ThinkingLevel.high)
        assert directives.as_dict() == {
            "system": directives.system,
            "user_suffix": directives.user_suffix,
        }


# ── prompt injection ────────────────────────────────────────────────────────


class TestApplyToPrompt:
    def test_suffix_is_appended(self):
        directives = ThinkingDirectives(user_suffix=" /no_think")
        assert apply_to_prompt("fix the bug", directives) == "fix the bug /no_think"

    def test_no_suffix_returns_prompt_unchanged(self):
        assert apply_to_prompt("fix the bug", ThinkingDirectives(system="x")) == "fix the bug"


# ── OpenAI message-list injection ───────────────────────────────────────────


class TestApplyToOpenAIMessages:
    def test_system_inserted_at_front_when_absent(self):
        messages = [{"role": "user", "content": "hello"}]
        out = apply_to_openai_messages(messages, ThinkingDirectives(system="Think hard."))
        assert out[0] == {"role": "system", "content": "Think hard."}
        assert out[1] == {"role": "user", "content": "hello"}

    def test_system_appended_to_existing_system(self):
        messages = [
            {"role": "system", "content": "You are Forge."},
            {"role": "user", "content": "hello"},
        ]
        out = apply_to_openai_messages(messages, ThinkingDirectives(system="Think hard."))
        assert len(out) == 2
        assert out[0]["role"] == "system"
        assert out[0]["content"].startswith("You are Forge.")
        assert out[0]["content"].endswith("Think hard.")

    def test_user_suffix_lands_on_last_user_message_only(self):
        messages = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "second"},
        ]
        out = apply_to_openai_messages(messages, ThinkingDirectives(user_suffix=" /think"))
        assert out[0]["content"] == "first"
        assert out[1]["content"] == "ok"
        assert out[2]["content"] == "second /think"

    def test_original_list_is_not_mutated(self):
        messages = [
            {"role": "system", "content": "You are Forge."},
            {"role": "user", "content": "hello"},
        ]
        snapshot = [dict(m) for m in messages]
        apply_to_openai_messages(
            messages, ThinkingDirectives(system="Think.", user_suffix=" /think")
        )
        assert messages == snapshot

    def test_empty_directives_return_messages_unchanged(self):
        messages = [{"role": "user", "content": "hello"}]
        assert apply_to_openai_messages(messages, ThinkingDirectives()) == messages

    def test_non_string_user_content_is_left_alone(self):
        messages = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
        out = apply_to_openai_messages(messages, ThinkingDirectives(user_suffix=" /think"))
        assert out[0]["content"] == [{"type": "text", "text": "hi"}]
