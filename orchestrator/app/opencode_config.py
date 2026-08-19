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
from .connector_catalog import CATALOG, SKILLS_MCP_PATH, render_block
from .models import Connector, EngineKind, ModelEntry

OPENCODE_PROVIDER = "forge-local"

__all__ = [
    "OPENCODE_PROVIDER",
    "SKILLS_MCP_PATH",
    "airllm_blocked",
    "opencode_model_id",
    "render_mcp_block",
    "render_opencode_config",
    "render_opencode_config_json",
]


def opencode_model_id(model: ModelEntry) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", model.display_name.lower()).strip("-")
    return slug or f"model-{model.id}"


def engine_base_url_for(model: ModelEntry, settings: Settings | None = None) -> str:
    """Sessions talk to the orchestrator's /v1 model router, never to engine
    containers directly — the router resolves the request's model slug to
    whichever GPU lease serves it, so engine placement (and multi-GPU) is
    invisible to OpenCode."""
    settings = settings or get_settings()
    return f"{settings.orchestrator_internal_url}/v1"


def render_mcp_block(connectors: list[Connector]) -> dict[str, Any]:
    """One MCP entry per connector row: catalog entries (core + integrations)
    render from their templates with {env:...} secret indirection; custom rows
    carry their own MCP block in config_json."""
    mcp: dict[str, Any] = {}
    for connector in connectors:
        try:
            config = json.loads(connector.config_json or "{}")
        except json.JSONDecodeError:
            config = {}
        entry = CATALOG.get(connector.kind)
        if entry is not None:
            mcp[connector.kind] = render_block(entry, connector.enabled, config)
        elif connector.kind.startswith("custom-") and isinstance(config.get("mcp"), dict):
            block = dict(config["mcp"])
            block["enabled"] = bool(connector.enabled)
            mcp[connector.kind] = block
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
    """Lanes that can never power a coding session: AirLLM (chat-only,
    PLAN §2) and imagegen (not a language model at all)."""
    return model.engine in (EngineKind.airllm, EngineKind.imagegen)
