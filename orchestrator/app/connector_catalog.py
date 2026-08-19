"""Connector catalog — every MCP connector Forge can wire into sessions.

Two tiers share one framework and one DB table:

- **Core** (PLAN §1.8): github, fetch, searxng, playwright, skills — the
  built-ins that ship enabled (github off until a PAT is set).
- **Integrations**: public MCP servers for popular services (the same
  services Claude's connector directory covers, where a public endpoint
  exists). Remote entries authenticate with a bearer token pasted in the UI
  (an API key, PAT, or OAuth access token obtained from the service — noted
  per entry); a few are local stdio servers configured via env vars.

Secrets never enter the rendered opencode.json: token values are passed to
the session container as FORGE_CONN_<ID>_<FIELD> env vars and referenced with
OpenCode's {env:...} indirection (headers and environment blocks support it).

Users can also add arbitrary MCP servers ("custom-<slug>" rows) from the
Connectors page; their full MCP block lives in the row's config_json.
"""

import re
from dataclasses import dataclass, field

SKILLS_MCP_PATH = "/opt/forge/skills_mcp.py"


@dataclass(frozen=True)
class AuthField:
    key: str
    label: str
    secret: bool = True
    placeholder: str = ""


@dataclass(frozen=True)
class CatalogEntry:
    id: str
    name: str
    description: str
    category: str  # core | productivity | developer | design | business | search
    mcp_type: str  # remote | local
    url: str = ""
    command: tuple[str, ...] = ()
    environment: dict[str, str] = field(default_factory=dict)
    auth_fields: tuple[AuthField, ...] = ()
    # For remote entries with a "token" auth field: the Authorization scheme.
    bearer: bool = True
    auth_note: str = ""
    docs_url: str = ""


def env_var(entry_id: str, field_key: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]", "_", entry_id).upper()
    return f"FORGE_CONN_{slug}_{field_key.upper()}"


TOKEN = (AuthField("token", "Access token"),)


CORE: tuple[CatalogEntry, ...] = (
    CatalogEntry(
        id="github",
        name="GitHub",
        description="Repos, issues, and pull requests via the official GitHub MCP server.",
        category="core",
        mcp_type="local",
        command=("github-mcp-server", "stdio"),
        environment={"GITHUB_PERSONAL_ACCESS_TOKEN": "{env:GITHUB_PAT}"},
        auth_fields=(AuthField("token", "Personal access token", placeholder="ghp_…"),),
        auth_note="Fine-grained or classic PAT. Also enables git push from sessions.",
        docs_url="https://github.com/github/github-mcp-server",
    ),
    CatalogEntry(
        id="fetch",
        name="Fetch",
        description="Retrieve web pages as markdown for the agent to read.",
        category="core",
        mcp_type="local",
        command=("uvx", "mcp-server-fetch"),
        docs_url="https://pypi.org/project/mcp-server-fetch/",
    ),
    CatalogEntry(
        id="searxng",
        name="Web search",
        description="Web search through your private SearXNG instance — no tracking.",
        category="core",
        mcp_type="local",
        command=("uvx", "mcp-searxng"),
        environment={"SEARXNG_URL": "http://searxng:8080"},
        docs_url="https://pypi.org/project/mcp-searxng/",
    ),
    CatalogEntry(
        id="playwright",
        name="Browser",
        description="Drive a real browser: open pages, click, fill forms, screenshot.",
        category="core",
        mcp_type="remote",
        url="http://mcp-playwright:8931/mcp",
        docs_url="https://github.com/microsoft/playwright-mcp",
    ),
    CatalogEntry(
        id="skills",
        name="Skills",
        description="Installed Claude Code-format skills, with progressive disclosure.",
        category="core",
        mcp_type="local",
        command=("python3", SKILLS_MCP_PATH),
        environment={"FORGE_DISABLED_SKILLS": "{env:FORGE_DISABLED_SKILLS}"},
    ),
)


# Public integrations. URLs are verified against each vendor's published MCP
# endpoint; entries whose service only issues OAuth tokens say so in
# auth_note — paste an OAuth access token obtained from the vendor (or use a
# PAT/API key where the service accepts one).
INTEGRATIONS: tuple[CatalogEntry, ...] = (
    CatalogEntry(
        id="notion",
        name="Notion",
        description="Search, read, and write Notion pages and databases.",
        category="productivity",
        mcp_type="remote",
        url="https://mcp.notion.com/mcp",
        auth_fields=TOKEN,
        auth_note=(
            "OAuth access token (or internal integration secret) from "
            "notion.so/profile/integrations."
        ),
        docs_url="https://developers.notion.com/docs/mcp",
    ),
    CatalogEntry(
        id="linear",
        name="Linear",
        description="Issues, projects, and cycles in Linear.",
        category="productivity",
        mcp_type="remote",
        url="https://mcp.linear.app/mcp",
        auth_fields=TOKEN,
        auth_note="OAuth access token — Linear's MCP uses OAuth; a personal API key may also work.",
        docs_url="https://linear.app/docs/mcp",
    ),
    CatalogEntry(
        id="figma",
        name="Figma",
        description="Read designs, components, and variables from Figma files.",
        category="design",
        mcp_type="remote",
        url="https://mcp.figma.com/mcp",
        auth_fields=TOKEN,
        auth_note="OAuth access token or a Figma personal access token.",
        docs_url="https://help.figma.com/hc/en-us/articles/32132100833559",
    ),
    CatalogEntry(
        id="sentry",
        name="Sentry",
        description="Query errors, issues, and traces from your Sentry org.",
        category="developer",
        mcp_type="remote",
        url="https://mcp.sentry.dev/mcp",
        auth_fields=TOKEN,
        auth_note="OAuth access token or a Sentry user auth token.",
        docs_url="https://docs.sentry.io/product/sentry-mcp/",
    ),
    CatalogEntry(
        id="stripe",
        name="Stripe",
        description="Customers, invoices, payments, and docs search.",
        category="business",
        mcp_type="remote",
        url="https://mcp.stripe.com",
        auth_fields=(AuthField("token", "API key", placeholder="rk_live_… / sk_test_…"),),
        auth_note="Use a restricted API key; bearer auth is supported directly.",
        docs_url="https://docs.stripe.com/mcp",
    ),
    CatalogEntry(
        id="asana",
        name="Asana",
        description="Tasks and projects in Asana.",
        category="productivity",
        mcp_type="remote",
        url="https://mcp.asana.com/sse",
        auth_fields=TOKEN,
        auth_note="OAuth access token or an Asana personal access token.",
        docs_url="https://developers.asana.com/docs/using-asanas-mcp-server",
    ),
    CatalogEntry(
        id="atlassian",
        name="Atlassian",
        description="Jira issues and Confluence pages.",
        category="productivity",
        mcp_type="remote",
        url="https://mcp.atlassian.com/v1/sse",
        auth_fields=TOKEN,
        auth_note="OAuth access token — Atlassian's remote MCP uses OAuth 2.1.",
        docs_url="https://support.atlassian.com/rovo/docs/getting-started-with-the-atlassian-remote-mcp-server/",
    ),
    CatalogEntry(
        id="intercom",
        name="Intercom",
        description="Conversations and customer data from Intercom.",
        category="business",
        mcp_type="remote",
        url="https://mcp.intercom.com/mcp",
        auth_fields=TOKEN,
        auth_note="OAuth access token or an Intercom access token.",
        docs_url="https://developers.intercom.com/docs/guides/mcp",
    ),
    CatalogEntry(
        id="paypal",
        name="PayPal",
        description="Invoices, payments, and disputes via PayPal's MCP.",
        category="business",
        mcp_type="remote",
        url="https://mcp.paypal.com/mcp",
        auth_fields=TOKEN,
        auth_note="OAuth access token from developer.paypal.com.",
        docs_url="https://developer.paypal.com/tools/mcp-server/",
    ),
    CatalogEntry(
        id="square",
        name="Square",
        description="Payments, catalog, and customers in Square.",
        category="business",
        mcp_type="remote",
        url="https://mcp.squareup.com/sse",
        auth_fields=TOKEN,
        auth_note="OAuth access token or a Square access token.",
        docs_url="https://developer.squareup.com/docs/mcp",
    ),
    CatalogEntry(
        id="plaid",
        name="Plaid",
        description="Inspect Plaid integrations and API usage.",
        category="business",
        mcp_type="remote",
        url="https://api.dashboard.plaid.com/mcp/sse",
        auth_fields=TOKEN,
        auth_note="OAuth access token from the Plaid dashboard.",
        docs_url="https://plaid.com/docs/resources/mcp/",
    ),
    CatalogEntry(
        id="monday",
        name="monday.com",
        description="Boards and items in monday.com.",
        category="productivity",
        mcp_type="remote",
        url="https://mcp.monday.com/sse",
        auth_fields=TOKEN,
        auth_note="OAuth access token or a monday API token.",
        docs_url="https://developer.monday.com/apps/docs/mondaycom-mcp-integration",
    ),
    CatalogEntry(
        id="canva",
        name="Canva",
        description="Create and edit Canva designs.",
        category="design",
        mcp_type="remote",
        url="https://mcp.canva.com/mcp",
        auth_fields=TOKEN,
        auth_note="OAuth access token — Canva's MCP uses OAuth.",
        docs_url="https://www.canva.dev/docs/connect/canva-mcp-server-setup/",
    ),
    CatalogEntry(
        id="cloudflare",
        name="Cloudflare docs",
        description="Search Cloudflare's documentation.",
        category="developer",
        mcp_type="remote",
        url="https://docs.mcp.cloudflare.com/sse",
        auth_note="No auth needed — public docs server.",
        docs_url="https://github.com/cloudflare/mcp-server-cloudflare",
    ),
    CatalogEntry(
        id="hugging-face",
        name="Hugging Face",
        description="Search models, datasets, spaces, and papers on the Hub.",
        category="developer",
        mcp_type="remote",
        url="https://huggingface.co/mcp",
        auth_fields=(AuthField("token", "HF token (optional)", placeholder="hf_…"),),
        auth_note="Works anonymously; add a token for private/gated repos.",
        docs_url="https://huggingface.co/settings/mcp",
    ),
    CatalogEntry(
        id="zapier",
        name="Zapier",
        description="Trigger thousands of app actions through your Zapier MCP endpoint.",
        category="productivity",
        mcp_type="remote",
        url="",  # account-specific: paste your endpoint from mcp.zapier.com
        auth_fields=(
            AuthField("url", "Your Zapier MCP URL", secret=True, placeholder="https://mcp.zapier.com/api/mcp/…"),
        ),
        bearer=False,
        auth_note="Zapier issues a per-account MCP URL (credentials embedded) — paste it here.",
        docs_url="https://zapier.com/mcp",
    ),
    CatalogEntry(
        id="airtable",
        name="Airtable",
        description="Read and write Airtable bases.",
        category="productivity",
        mcp_type="local",
        command=("npx", "-y", "airtable-mcp-server"),
        environment={"AIRTABLE_API_KEY": "{env:FORGE_CONN_AIRTABLE_TOKEN}"},
        auth_fields=(AuthField("token", "Personal access token", placeholder="pat…"),),
        auth_note="Airtable personal access token with the scopes you need.",
        docs_url="https://github.com/domdomegg/airtable-mcp-server",
    ),
    CatalogEntry(
        id="slack",
        name="Slack",
        description="Read channels and post messages as a Slack bot.",
        category="productivity",
        mcp_type="local",
        command=("npx", "-y", "@modelcontextprotocol/server-slack"),
        environment={
            "SLACK_BOT_TOKEN": "{env:FORGE_CONN_SLACK_TOKEN}",
            "SLACK_TEAM_ID": "{env:FORGE_CONN_SLACK_TEAM_ID}",
        },
        auth_fields=(
            AuthField("token", "Bot token", placeholder="xoxb-…"),
            AuthField("team_id", "Team ID", secret=False, placeholder="T01234567"),
        ),
        auth_note="Create a Slack app with a bot token (chat:write, channels:history, …).",
        docs_url="https://github.com/modelcontextprotocol/servers-archived/tree/main/src/slack",
    ),
)


CATALOG: dict[str, CatalogEntry] = {entry.id: entry for entry in (*CORE, *INTEGRATIONS)}

# Enabled out of the box; everything else starts disabled.
DEFAULT_ENABLED: dict[str, bool] = {
    "github": False,  # off until a PAT is configured
    "fetch": True,
    "searxng": True,
    "playwright": True,
    "skills": True,
}


def render_block(entry: CatalogEntry, enabled: bool, config: dict) -> dict:
    """The opencode.json mcp block for one catalog entry. Secret values are
    referenced via {env:...}; the actual values travel as container env vars
    (see session_manager)."""
    if entry.mcp_type == "remote":
        url = entry.url
        if not url and config.get("url"):
            # Account-specific endpoint (e.g. Zapier) — treated as secret, so
            # env-indirect it like a token.
            url = f"{{env:{env_var(entry.id, 'url')}}}"
        block: dict = {"type": "remote", "url": url, "enabled": bool(enabled and url)}
        has_token = any(f.key == "token" for f in entry.auth_fields)
        if has_token and entry.bearer and config.get("token"):
            block["headers"] = {
                "Authorization": f"Bearer {{env:{env_var(entry.id, 'token')}}}"
            }
        return block
    block = {
        "type": "local",
        "command": list(entry.command),
        "enabled": bool(enabled),
    }
    if entry.environment:
        block["environment"] = dict(entry.environment)
    return block


def secret_env_for(entry: CatalogEntry, config: dict) -> dict[str, str]:
    """Container env vars carrying this connector's configured field values."""
    env: dict[str, str] = {}
    for auth_field in entry.auth_fields:
        value = config.get(auth_field.key)
        if value:
            env[env_var(entry.id, auth_field.key)] = str(value)
    return env
