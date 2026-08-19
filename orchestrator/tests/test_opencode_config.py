"""Tests for the per-session opencode.json renderer (PLAN §6.3) and the
connector-catalog MCP framework. The MCP tests exercise the framework
mechanics (env indirection, headers, enable flags, custom passthrough) rather
than pinning specific integration endpoints, which change."""

import json

import pytest

from app.config import Settings
from app.connector_catalog import CATALOG, CORE, INTEGRATIONS, env_var
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

ROUTER_BASE_URL = "http://orchestrator:8000/v1"


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


def connector(kind: str, enabled: bool, config_json: str = "{}") -> Connector:
    return Connector(kind=kind, enabled=enabled, config_json=config_json)


CORE_KINDS = {entry.id for entry in CORE}
ALL_CORE_ENABLED = [connector(kind.value, True) for kind in ConnectorKind]


def remote_entry_with_token():
    """Any catalog integration that is remote, bearer-authed, and has a fixed
    URL — the framework path under test, whichever service it happens to be."""
    return next(
        entry
        for entry in INTEGRATIONS
        if entry.mcp_type == "remote"
        and entry.bearer
        and entry.url
        and any(f.key == "token" for f in entry.auth_fields)
    )


# ── provider block (PLAN §6.3 shape) ────────────────────────────────────────


class TestProviderBlock:
    def test_shape_for_llamacpp_lane(self):
        config = render_opencode_config(make_model(), settings=Settings())
        provider = config["provider"][OPENCODE_PROVIDER]
        assert provider["npm"] == "@ai-sdk/openai-compatible"
        assert provider["name"] == "Forge local"
        assert provider["options"]["baseURL"] == ROUTER_BASE_URL
        model_key = "qwen2-5-coder-14b-instruct"
        assert set(provider["models"]) == {model_key}
        assert provider["models"][model_key] == {
            "name": "Qwen2.5 Coder 14B Instruct",
            "tool_call": True,
        }
        assert config["model"] == f"{OPENCODE_PROVIDER}/{model_key}"

    @pytest.mark.parametrize("engine", list(EngineKind))
    def test_base_url_is_the_orchestrator_router_for_every_lane(self, engine):
        """Sessions never talk to engine containers directly: every lane's
        provider points at the orchestrator's /v1 model router, which resolves
        the slug to whichever GPU lease serves it."""
        config = render_opencode_config(make_model(engine=engine), settings=Settings())
        base_url = config["provider"][OPENCODE_PROVIDER]["options"]["baseURL"]
        assert base_url == ROUTER_BASE_URL

    def test_base_url_honors_orchestrator_internal_url_setting(self):
        settings = Settings(orchestrator_internal_url="http://forge-orch:9000")
        config = render_opencode_config(make_model(), settings=settings)
        base_url = config["provider"][OPENCODE_PROVIDER]["options"]["baseURL"]
        assert base_url == "http://forge-orch:9000/v1"

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


# ── mcp block: core five ────────────────────────────────────────────────────


class TestMcpCore:
    def test_core_five_render_with_expected_commands(self):
        mcp = render_mcp_block(ALL_CORE_ENABLED)
        assert set(mcp) == {"github", "fetch", "searxng", "playwright", "skills"}
        assert mcp["github"]["type"] == "local"
        assert mcp["github"]["command"] == ["github-mcp-server", "stdio"]
        assert mcp["fetch"]["command"] == ["uvx", "mcp-server-fetch"]
        assert mcp["searxng"]["command"] == ["uvx", "mcp-searxng"]
        assert mcp["searxng"]["environment"] == {"SEARXNG_URL": "http://searxng:8080"}
        assert mcp["skills"]["command"] == ["python3", SKILLS_MCP_PATH]
        assert mcp["playwright"]["type"] == "remote"
        assert mcp["playwright"]["url"]  # fixed internal endpoint, no auth headers
        assert "headers" not in mcp["playwright"]

    @pytest.mark.parametrize("kind", list(ConnectorKind))
    def test_each_connector_flag_follows_its_row(self, kind):
        for enabled in (True, False):
            rows = [
                connector(k.value, enabled if k == kind else True)
                for k in ConnectorKind
            ]
            mcp = render_mcp_block(rows)
            assert mcp[kind.value]["enabled"] is enabled, (kind, enabled)

    def test_disabled_connectors_stay_listed_but_off(self):
        rows = [connector(kind.value, False) for kind in ConnectorKind]
        mcp = render_mcp_block(rows)
        assert set(mcp) == {"github", "fetch", "searxng", "playwright", "skills"}
        assert all(entry["enabled"] is False for entry in mcp.values())


# ── mcp block: catalog framework mechanics ──────────────────────────────────


class TestMcpFramework:
    def test_remote_entry_with_token_gains_bearer_header(self):
        entry = remote_entry_with_token()
        rows = [
            connector(entry.id, True, config_json=json.dumps({"token": "sekrit-123"}))
        ]
        block = render_mcp_block(rows)[entry.id]
        assert block["type"] == "remote"
        assert block["url"] == entry.url
        assert block["enabled"] is True
        assert block["headers"] == {
            "Authorization": f"Bearer {{env:{env_var(entry.id, 'token')}}}"
        }
        # The literal secret never lands in the rendered config.
        assert "sekrit-123" not in json.dumps(block)

    def test_remote_entry_without_token_has_no_headers(self):
        entry = remote_entry_with_token()
        block = render_mcp_block([connector(entry.id, True)])[entry.id]
        assert "headers" not in block

    def test_disabled_catalog_entry_renders_enabled_false(self):
        entry = remote_entry_with_token()
        rows = [
            connector(entry.id, False, config_json=json.dumps({"token": "sekrit"}))
        ]
        assert render_mcp_block(rows)[entry.id]["enabled"] is False

    def test_custom_row_passes_its_mcp_block_through(self):
        custom_mcp = {"type": "remote", "url": "https://mcp.example.com/mcp"}
        rows = [
            connector("custom-example", True, config_json=json.dumps({"mcp": custom_mcp}))
        ]
        block = render_mcp_block(rows)["custom-example"]
        assert block["type"] == "remote"
        assert block["url"] == "https://mcp.example.com/mcp"
        assert block["enabled"] is True

        rows[0].enabled = False
        assert render_mcp_block(rows)["custom-example"]["enabled"] is False

    def test_unknown_kinds_are_skipped(self):
        rows = [
            connector("no-such-connector", True),
            connector("custom-broken", True),  # custom without an mcp block
            connector("custom-badjson", True, config_json="{not json"),
        ]
        assert render_mcp_block(rows) == {}


# ── seeding: one row per catalog entry, per user ────────────────────────────


def _make_user(username: str) -> int:
    from app.db import write_session
    from app.models import User

    with write_session() as db:
        user = User(username=username)
        db.add(user)
        db.flush()
        return user.id


class TestSeeding:
    def test_on_user_created_seeds_every_catalog_entry(self, db_ready):
        from sqlmodel import select

        from app.db import read_session
        from app.services.user_service import on_user_created

        user_id = _make_user("seedy")
        on_user_created(user_id)
        with read_session() as db:
            rows = {
                row.kind: row
                for row in db.exec(
                    select(Connector).where(Connector.user_id == user_id)
                ).all()
            }

        assert set(rows) == set(CATALOG)
        # Core defaults: github off until a PAT is set, the other four on.
        assert rows["github"].enabled is False
        for name in ("fetch", "searxng", "playwright", "skills"):
            assert rows[name].enabled is True, name
        # Integrations exist but start disabled until configured.
        for entry in INTEGRATIONS:
            assert rows[entry.id].enabled is False, entry.id

    def test_seed_is_idempotent_and_keeps_user_edits(self, db_ready):
        from sqlmodel import select

        from app.db import read_session, write_session
        from app.services.user_service import on_user_created

        user_id = _make_user("seedy")
        on_user_created(user_id)
        with write_session() as db:
            row = db.exec(
                select(Connector).where(
                    Connector.kind == "github", Connector.user_id == user_id
                )
            ).one()
            row.enabled = True
            db.add(row)

        on_user_created(user_id)
        with read_session() as db:
            rows = db.exec(
                select(Connector).where(Connector.user_id == user_id)
            ).all()
            github = [r for r in rows if r.kind == "github"]
        assert len(rows) == len(CATALOG)  # no duplicates
        assert len(github) == 1 and github[0].enabled is True

    def test_each_user_gets_their_own_rows(self, db_ready):
        """Connectors are per-user now: two users hold the same kinds side by
        side (the old UNIQUE(kind) constraint is gone)."""
        from sqlmodel import select

        from app.db import read_session
        from app.services.user_service import on_user_created

        first = _make_user("first")
        second = _make_user("second")
        on_user_created(first)
        on_user_created(second)
        with read_session() as db:
            rows = db.exec(select(Connector)).all()
        by_user: dict[int, set[str]] = {}
        for row in rows:
            by_user.setdefault(row.user_id, set()).add(row.kind)
        assert by_user[first] == set(CATALOG)
        assert by_user[second] == set(CATALOG)


# ── secrets: env indirection only, never literals ───────────────────────────


class TestSecretIndirection:
    SECRET = "ghp_totally-secret-token-123"

    def _rows(self):
        return [
            connector(
                ConnectorKind.github.value,
                True,
                config_json=json.dumps({"token": self.SECRET}),
            )
        ] + [
            connector(k.value, True)
            for k in ConnectorKind
            if k != ConnectorKind.github
        ]

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
