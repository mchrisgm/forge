"""Connector catalog rendering: opencode.json mcp blocks (secrets always via
{env:...} indirection) and orchestrator-side request headers (real secret
values), across auth header/scheme variants. Entries are constructed locally
so these tests hold no matter what the shipped catalog contains."""

import json

from app.connector_catalog import (
    AuthField,
    CatalogEntry,
    env_var,
    render_block,
    request_headers,
)

TOKEN_FIELDS = (AuthField("token", "Access token"),)


def remote_entry(**overrides) -> CatalogEntry:
    defaults = dict(
        id="acme",
        name="Acme",
        description="A made-up remote MCP service.",
        category="developer",
        mcp_type="remote",
        url="https://mcp.acme.dev/mcp",
        auth_fields=TOKEN_FIELDS,
    )
    defaults.update(overrides)
    return CatalogEntry(**defaults)


def local_entry(**overrides) -> CatalogEntry:
    defaults = dict(
        id="acme-local",
        name="Acme local",
        description="A made-up local MCP server.",
        category="developer",
        mcp_type="local",
        command=("npx", "-y", "acme-mcp@1.0.0"),
        environment={"ACME_TOKEN": "{env:FORGE_CONN_ACME_LOCAL_TOKEN}"},
    )
    defaults.update(overrides)
    return CatalogEntry(**defaults)


class TestEnvVar:
    def test_slugs_the_entry_id_and_uppercases_the_field(self):
        assert env_var("acme", "token") == "FORGE_CONN_ACME_TOKEN"
        assert env_var("hugging-face", "token") == "FORGE_CONN_HUGGING_FACE_TOKEN"
        assert env_var("custom-my server", "url") == "FORGE_CONN_CUSTOM_MY_SERVER_URL"


class TestRenderBlock:
    def test_bearer_default_wraps_the_env_reference(self):
        block = render_block(remote_entry(), True, {"token": "sek-123"})
        assert block["type"] == "remote"
        assert block["url"] == "https://mcp.acme.dev/mcp"
        assert block["enabled"] is True
        assert block["headers"] == {
            "Authorization": "Bearer {env:FORGE_CONN_ACME_TOKEN}"
        }
        # The secret itself never enters the rendered config.
        assert "sek-123" not in json.dumps(block)

    def test_custom_header_with_empty_scheme_is_the_raw_env_reference(self):
        entry = remote_entry(auth_header="X-Api-Key", auth_scheme="")
        block = render_block(entry, True, {"token": "sek-123"})
        assert block["headers"] == {"X-Api-Key": "{env:FORGE_CONN_ACME_TOKEN}"}

    def test_custom_scheme_prefixes_the_env_reference(self):
        entry = remote_entry(auth_scheme="token")
        block = render_block(entry, True, {"token": "sek-123"})
        assert block["headers"] == {
            "Authorization": "token {env:FORGE_CONN_ACME_TOKEN}"
        }

    def test_extra_headers_ride_along_with_the_auth_header(self):
        entry = remote_entry(extra_headers={"X-Scope": "all"})
        block = render_block(entry, True, {"token": "sek-123"})
        assert block["headers"] == {
            "X-Scope": "all",
            "Authorization": "Bearer {env:FORGE_CONN_ACME_TOKEN}",
        }

    def test_no_token_configured_renders_no_headers(self):
        block = render_block(remote_entry(), True, {})
        assert "headers" not in block

    def test_bearer_false_renders_no_headers_even_with_a_token(self):
        block = render_block(remote_entry(bearer=False), True, {"token": "sek-123"})
        assert "headers" not in block

    def test_disabled_entry_renders_disabled(self):
        block = render_block(remote_entry(), False, {"token": "sek-123"})
        assert block["enabled"] is False

    def test_account_specific_url_is_env_indirected(self):
        entry = remote_entry(
            url="",
            auth_fields=(AuthField("url", "Your MCP URL"),),
            bearer=False,
        )
        block = render_block(entry, True, {"url": "https://mcp.acme.dev/u/sek/mcp"})
        assert block["url"] == "{env:FORGE_CONN_ACME_URL}"
        assert block["enabled"] is True
        assert "sek" not in json.dumps(block)

    def test_remote_without_any_url_renders_disabled(self):
        block = render_block(remote_entry(url=""), True, {"token": "sek-123"})
        assert block["url"] == ""
        assert block["enabled"] is False

    def test_local_entry_renders_command_and_environment(self):
        block = render_block(local_entry(), True, {"token": "sek-123"})
        assert block == {
            "type": "local",
            "command": ["npx", "-y", "acme-mcp@1.0.0"],
            "enabled": True,
            "environment": {"ACME_TOKEN": "{env:FORGE_CONN_ACME_LOCAL_TOKEN}"},
        }

    def test_local_entry_without_environment_omits_the_key(self):
        block = render_block(local_entry(environment={}), False, {})
        assert block == {
            "type": "local",
            "command": ["npx", "-y", "acme-mcp@1.0.0"],
            "enabled": False,
        }


class TestRequestHeaders:
    def test_bearer_default_carries_the_real_token(self):
        headers = request_headers(remote_entry(), {"token": "sek-123"})
        assert headers == {"Authorization": "Bearer sek-123"}

    def test_empty_scheme_sends_the_raw_token(self):
        entry = remote_entry(auth_header="X-Api-Key", auth_scheme="")
        assert request_headers(entry, {"token": "sek-123"}) == {"X-Api-Key": "sek-123"}

    def test_extra_headers_accompany_the_auth_header(self):
        entry = remote_entry(extra_headers={"X-Scope": "all"})
        assert request_headers(entry, {"token": "sek-123"}) == {
            "X-Scope": "all",
            "Authorization": "Bearer sek-123",
        }

    def test_local_entry_has_no_request_headers(self):
        assert request_headers(local_entry(), {"token": "sek-123"}) == {}

    def test_no_token_means_no_headers(self):
        assert request_headers(remote_entry(), {}) == {}

    def test_bearer_false_means_no_headers(self):
        assert request_headers(remote_entry(bearer=False), {"token": "sek-123"}) == {}

    def test_entry_without_a_token_field_ignores_a_stray_token(self):
        entry = remote_entry(auth_fields=())
        assert request_headers(entry, {"token": "sek-123"}) == {}
