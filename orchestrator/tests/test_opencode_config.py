"""Tests for the per-session opencode.json renderer (PLAN §6.3)."""

import json

import pytest

from app.config import Settings
from app.models import Connector, ConnectorKind, EngineKind, ModelEntry, ToolCallFormat
from app.opencode_config import (
    OPENCODE_PROVIDER,
    SKILLS_MCP_PATH,
    airllm_blocked,
    opencode_model_id,
    render_mcp_block,
    render_opencode_config,
    render_opencode_config_json,
)


def make_model(**overrides) -> ModelEntry:
    defaults = dict(
        id=1,
        hf_repo="Qwen/Qwen2.5-Coder-14B-Instruct-GGUF",
        display_name="Qwen2.5 Coder 14B Instruct",
        engine=EngineKind.llamacpp,
        tool_call_format=ToolCallFormat.hermes,
    )
    defaults.update(overrides)
    return ModelEntry(**defaults)


def connector(kind: ConnectorKind, enabled: bool, config_json: str = "{}") -> Connector:
    return Connector(kind=kind, enabled=enabled, config_json=config_json)


ALL_ENABLED = [connector(kind, True) for kind in ConnectorKind]


# ── provider block (PLAN §6.3 shape) ────────────────────────────────────────


class TestProviderBlock:
    def test_shape_for_llamacpp_lane(self):
        config = render_opencode_config(make_model(), settings=Settings())
        provider = config["provider"][OPENCODE_PROVIDER]
        assert provider["npm"] == "@ai-sdk/openai-compatible"
        assert provider["name"] == "Forge local"
        assert provider["options"]["baseURL"] == "http://forge-engine-llamacpp:8081/v1"
        model_key = "qwen2-5-coder-14b-instruct"
        assert set(provider["models"]) == {model_key}
        assert provider["models"][model_key] == {
            "name": "Qwen2.5 Coder 14B Instruct",
            "tool_call": True,
        }
        assert config["model"] == f"{OPENCODE_PROVIDER}/{model_key}"

    @pytest.mark.parametrize(
        ("engine", "port"),
        [
            (EngineKind.llamacpp, 8081),
            (EngineKind.vllm, 8082),
            (EngineKind.airllm, 8083),
        ],
    )
    def test_base_url_follows_engine_lane_and_port(self, engine, port):
        config = render_opencode_config(make_model(engine=engine), settings=Settings())
        base_url = config["provider"][OPENCODE_PROVIDER]["options"]["baseURL"]
        assert base_url == f"http://forge-engine-{engine.value}:{port}/v1"

    def test_base_url_honors_port_settings(self):
        settings = Settings(llamacpp_port=9999)
        config = render_opencode_config(make_model(), settings=settings)
        base_url = config["provider"][OPENCODE_PROVIDER]["options"]["baseURL"]
        assert base_url == "http://forge-engine-llamacpp:9999/v1"

    def test_tools_false_when_tool_call_format_none(self):
        config = render_opencode_config(
            make_model(tool_call_format=ToolCallFormat.none), settings=Settings()
        )
        models = config["provider"][OPENCODE_PROVIDER]["models"]
        assert all(entry["tool_call"] is False for entry in models.values())
        # And OpenCode's enforced ruleset strips every tool for no-tool models.
        assert config["tools"] == {"*": False}

    @pytest.mark.parametrize(
        "fmt", [ToolCallFormat.hermes, ToolCallFormat.qwen, ToolCallFormat.llama3]
    )
    def test_tools_true_for_real_tool_formats(self, fmt):
        config = render_opencode_config(make_model(tool_call_format=fmt), settings=Settings())
        (entry,) = config["provider"][OPENCODE_PROVIDER]["models"].values()
        assert entry["tool_call"] is True
        assert "tools" not in config

    def test_no_mcp_block_when_connectors_not_provided(self):
        config = render_opencode_config(make_model(), settings=Settings())
        assert "mcp" not in config


class TestModelSlug:
    def test_slug_is_lowercase_hyphenated(self):
        assert opencode_model_id(make_model()) == "qwen2-5-coder-14b-instruct"

    def test_slug_strips_edge_separators(self):
        model = make_model(display_name="  GPT-OSS 20B!  ")
        assert opencode_model_id(model) == "gpt-oss-20b"

    def test_unsluggable_name_falls_back_to_model_id(self):
        model = make_model(id=7, display_name="###")
        assert opencode_model_id(model) == "model-7"


# ── mcp block per connector state ───────────────────────────────────────────


class TestMcpBlock:
    def test_all_five_connectors_always_present(self):
        mcp = render_mcp_block(ALL_ENABLED)
        assert set(mcp) == {"github", "fetch", "searxng", "playwright", "skills"}

    @pytest.mark.parametrize("kind", list(ConnectorKind))
    def test_each_connector_flag_follows_its_row(self, kind):
        for enabled in (True, False):
            rows = [
                connector(k, enabled if k == kind else True) for k in ConnectorKind
            ]
            mcp = render_mcp_block(rows)
            assert mcp[kind.value]["enabled"] is enabled, (kind, enabled)

    def test_defaults_when_rows_missing(self):
        # No rows at all: github defaults off (needs a PAT), the rest on.
        mcp = render_mcp_block([])
        assert mcp["github"]["enabled"] is False
        for name in ("fetch", "searxng", "playwright", "skills"):
            assert mcp[name]["enabled"] is True, name

    def test_local_connector_commands(self):
        mcp = render_mcp_block(ALL_ENABLED)
        assert mcp["github"]["type"] == "local"
        assert mcp["github"]["command"] == ["github-mcp-server", "stdio"]
        assert mcp["fetch"]["command"] == ["uvx", "mcp-server-fetch"]
        assert mcp["searxng"]["command"] == ["uvx", "mcp-searxng"]
        assert mcp["searxng"]["environment"] == {"SEARXNG_URL": "http://searxng:8080"}
        assert mcp["skills"]["command"] == ["python3", SKILLS_MCP_PATH]

    def test_playwright_is_remote_sse(self):
        mcp = render_mcp_block(ALL_ENABLED)
        assert mcp["playwright"] == {
            "type": "remote",
            "url": "http://mcp-playwright:8931/mcp",
            "enabled": True,
        }

    def test_disabled_connectors_stay_listed_but_off(self):
        rows = [connector(kind, False) for kind in ConnectorKind]
        mcp = render_mcp_block(rows)
        assert set(mcp) == {"github", "fetch", "searxng", "playwright", "skills"}
        assert all(entry["enabled"] is False for entry in mcp.values())


# ── secrets: env indirection only, never literals ───────────────────────────


class TestSecretIndirection:
    SECRET = "ghp_totally-secret-token-123"

    def _rows(self):
        return [
            connector(
                ConnectorKind.github, True, config_json=json.dumps({"token": self.SECRET})
            )
        ] + [connector(k, True) for k in ConnectorKind if k != ConnectorKind.github]

    def test_github_env_uses_placeholder(self):
        mcp = render_mcp_block(self._rows())
        assert mcp["github"]["environment"] == {
            "GITHUB_PERSONAL_ACCESS_TOKEN": "{env:GITHUB_PAT}"
        }

    def test_rendered_json_never_contains_the_token(self):
        rendered = render_opencode_config_json(
            make_model(), connectors=self._rows(), settings=Settings()
        )
        assert self.SECRET not in rendered
        assert "{env:GITHUB_PAT}" in rendered
        # And it round-trips as valid JSON with the mcp block attached.
        parsed = json.loads(rendered)
        assert parsed["mcp"]["github"]["enabled"] is True


# ── airllm session block (PLAN §2: chat-only lane) ──────────────────────────


class TestAirllmBlocked:
    def test_airllm_models_are_blocked(self):
        assert airllm_blocked(make_model(engine=EngineKind.airllm)) is True

    @pytest.mark.parametrize("engine", [EngineKind.llamacpp, EngineKind.vllm])
    def test_other_lanes_are_allowed(self, engine):
        assert airllm_blocked(make_model(engine=engine)) is False
