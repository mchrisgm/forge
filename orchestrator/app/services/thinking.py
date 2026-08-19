"""Thinking-level directives — how much reasoning a model spends on a query.

Open-weight model families control reasoning differently, so one UI knob maps
to per-family mechanisms:

- Qwen3 family: the official soft switches `/think` and `/no_think` appended
  to the user turn, plus a brief system nudge for depth.
- gpt-oss (harmony format): a `Reasoning: low|medium|high` system line — the
  documented way to set its reasoning effort.
- Everything else: plain-language system directives (works on any instruct
  model; models without hidden reasoning simply answer more or less tersely).

`auto` means no directives at all — the model's native default behavior.
Pure logic, unit-tested; used by the task runner, the engine chat proxy, and
exposed to the PWA via GET /api/models/{id}/thinking/{level}.
"""

import re
from dataclasses import dataclass

from ..models import ModelEntry, ThinkingLevel

_QWEN3_RE = re.compile(r"qwen\s*3|qwen3", re.IGNORECASE)
_GPT_OSS_RE = re.compile(r"gpt[-_]?oss", re.IGNORECASE)
_DEEPSEEK_R1_RE = re.compile(r"deepseek[-_]?r1|[-_]r1[-_]", re.IGNORECASE)


@dataclass(frozen=True)
class ThinkingDirectives:
    system: str = ""       # extra system-prompt text ("" = none)
    user_suffix: str = ""  # appended to the user turn ("" = none)

    def as_dict(self) -> dict:
        return {"system": self.system, "user_suffix": self.user_suffix}


def model_thinking_family(model: ModelEntry) -> str:
    haystack = f"{model.family} {model.display_name} {model.hf_repo}"
    if _QWEN3_RE.search(haystack):
        return "qwen3"
    if _GPT_OSS_RE.search(haystack):
        return "gpt-oss"
    if _DEEPSEEK_R1_RE.search(haystack):
        return "always-thinking"  # R1-style: reasons regardless; only depth hints help
    return "generic"


_GENERIC_SYSTEM = {
    ThinkingLevel.off: (
        "Answer directly and concisely. Do not produce extended step-by-step "
        "reasoning before the answer."
    ),
    ThinkingLevel.low: (
        "Think briefly before answering — a few short reasoning steps at most."
    ),
    ThinkingLevel.high: (
        "Reason carefully and thoroughly before answering. Work through the "
        "problem step by step and double-check your conclusion."
    ),
}


def directives_for(model: ModelEntry, level: ThinkingLevel) -> ThinkingDirectives:
    if level == ThinkingLevel.auto:
        return ThinkingDirectives()
    family = model_thinking_family(model)

    if family == "qwen3":
        if level == ThinkingLevel.off:
            return ThinkingDirectives(user_suffix=" /no_think")
        if level == ThinkingLevel.low:
            return ThinkingDirectives(
                system="Keep your thinking brief — a few steps at most.",
                user_suffix=" /think",
            )
        return ThinkingDirectives(
            system="Think deeply and thoroughly before answering.",
            user_suffix=" /think",
        )

    if family == "gpt-oss":
        effort = {
            ThinkingLevel.off: "low",
            ThinkingLevel.low: "low",
            ThinkingLevel.high: "high",
        }[level]
        return ThinkingDirectives(system=f"Reasoning: {effort}")

    if family == "always-thinking" and level == ThinkingLevel.off:
        # R1-style models cannot switch reasoning off; the closest honest
        # mapping is "keep it short".
        return ThinkingDirectives(system=_GENERIC_SYSTEM[ThinkingLevel.low])

    return ThinkingDirectives(system=_GENERIC_SYSTEM[level])


def apply_to_prompt(prompt: str, directives: ThinkingDirectives) -> str:
    return prompt + directives.user_suffix if directives.user_suffix else prompt


def apply_to_openai_messages(messages: list[dict], directives: ThinkingDirectives) -> list[dict]:
    """Inject directives into an OpenAI chat-completions message list."""
    if not directives.system and not directives.user_suffix:
        return messages
    out = [dict(m) for m in messages]
    if directives.system:
        for message in out:
            if message.get("role") == "system":
                message["content"] = f"{message.get('content', '')}\n\n{directives.system}".strip()
                break
        else:
            out.insert(0, {"role": "system", "content": directives.system})
    if directives.user_suffix:
        for message in reversed(out):
            if message.get("role") == "user" and isinstance(message.get("content"), str):
                message["content"] = message["content"] + directives.user_suffix
                break
    return out
