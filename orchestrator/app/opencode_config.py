"""Renders the per-session opencode.json (PLAN §6.3): the forge-local provider
pointing at whichever engine lane serves the session's model, plus MCP blocks
for each enabled connector. The secret-bearing values are passed to the session
container as environment variables and referenced with {env:...} so the config
file itself stays secret-free.
"""

import json
import re
from typing import Any

from .config import Settings, get_settings
from .models import Connector, ConnectorKind, EngineKind, ModelEntry

OPENCODE_PROVIDER = "forge-local"
SKILLS_MCP_PATH = "/opt/forge/skills_mcp.py"


def opencode_model_id(model: ModelEntry) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", model.display_name.lower()).strip("-")
    return slug or f"model-{model.id}"


def engine_base_url_for(model: ModelEntry, settings: Settings | None = None) -> str:
    # Local import to keep this module import-light for tests.
    from .services.engine_manager import engine_base_url

    return engine_base_url(model.engine, settings or get_settings())


def render_mcp_block(connectors: list[Connector]) -> dict[str, Any]:
    enabled = {c.kind: c.enabled for c in connectors}
    mcp: dict[str, Any] = {}

    mcp["github"] = {
        "type": "local",
        "command": ["github-mcp-server", "stdio"],
        "environment": {"GITHUB_PERSONAL_ACCESS_TOKEN": "{env:GITHUB_PAT}"},
        "enabled": bool(enabled.get(ConnectorKind.github, False)),
    }
    mcp["fetch"] = {
        "type": "local",
        "command": ["uvx", "mcp-server-fetch"],
        "enabled": bool(enabled.get(ConnectorKind.fetch, True)),
    }
    mcp["searxng"] = {
        "type": "local",
        "command": ["uvx", "mcp-searxng"],
        "environment": {"SEARXNG_URL": "http://searxng:8080"},
        "enabled": bool(enabled.get(ConnectorKind.searxng, True)),
    }
    mcp["playwright"] = {
        "type": "remote",
        "url": "http://mcp-playwright:8931/mcp",
        "enabled": bool(enabled.get(ConnectorKind.playwright, True)),
    }
    mcp["skills"] = {
        "type": "local",
        "command": ["python3", SKILLS_MCP_PATH],
        # Same {env:...} passthrough pattern as GITHUB_PAT: the value reaches
        # the stdio subprocess from the container env set at spawn time.
        "environment": {"FORGE_DISABLED_SKILLS": "{env:FORGE_DISABLED_SKILLS}"},
        "enabled": bool(enabled.get(ConnectorKind.skills, True)),
    }
    return mcp


def render_opencode_config(
    model: ModelEntry,
    connectors: list[Connector] | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    model_id = opencode_model_id(model)
    supports_tools = model.tool_call_format.value != "none"
    config: dict[str, Any] = {
        "$schema": "https://opencode.ai/config.json",
        "provider": {
            OPENCODE_PROVIDER: {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Forge local",
                "options": {"baseURL": engine_base_url_for(model, settings)},
                "models": {
                    model_id: {
                        "name": model.display_name,
                        # OpenCode's model-capability key is tool_call (a bare
                        # "tools" key is silently ignored by its schema).
                        "tool_call": supports_tools,
                    }
                },
            }
        },
        "model": f"{OPENCODE_PROVIDER}/{model_id}",
    }
    if not supports_tools:
        # Belt and suspenders: OpenCode enforces tool stripping through the
        # permission/tools ruleset, not the capability flag alone.
        config["tools"] = {"*": False}
    if connectors is not None:
        config["mcp"] = render_mcp_block(connectors)
    return config


def render_opencode_config_json(
    model: ModelEntry,
    connectors: list[Connector] | None = None,
    settings: Settings | None = None,
) -> str:
    return json.dumps(render_opencode_config(model, connectors, settings), indent=2)


def airllm_blocked(model: ModelEntry) -> bool:
    """AirLLM lane is chat-only — never a session model (PLAN §2)."""
    return model.engine == EngineKind.airllm
