"""Connector catalog — every MCP connector Forge can wire into sessions.

Two tiers share one framework and one DB table:

- **Core** (PLAN §1.8): github, fetch, searxng, playwright, skills — the
  built-ins that ship enabled (github off until a PAT is set).
- **Integrations**: public MCP servers for popular services (the same
  services Claude's connector directory covers, where a public endpoint
  exists). Remote entries authenticate with a bearer token pasted in the UI
  (an API key, PAT, or OAuth access token obtained from the service — noted
  per entry); several are local stdio servers configured via env vars.

Every remote URL below was probed against the live endpoint and checked
against the vendor's public docs on 2026-08-19; auth notes record what each
vendor actually accepts (several are OAuth-only and say so honestly). Local
commands are pinned to exact published versions so sessions are reproducible.

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
    category: str  # core | productivity | developer | design | business
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


# Public integrations, verified 2026-08-19 (live endpoint probe + vendor docs).
# OAuth-only services are labeled as such: Forge can still talk to them if you
# paste an OAuth access token, but there is no simple API-key path — the
# auth_note is honest about it. Local commands pin exact npm/PyPI versions so
# session containers never pull a surprise release.
INTEGRATIONS: tuple[CatalogEntry, ...] = (
    # ── productivity ────────────────────────────────────────────────────────
    CatalogEntry(
        id="notion",
        name="Notion",
        description="Search, read, and write Notion pages and databases.",
        category="productivity",
        mcp_type="local",
        command=("npx", "-y", "@notionhq/notion-mcp-server@2.5.1"),
        environment={"NOTION_TOKEN": "{env:FORGE_CONN_NOTION_TOKEN}"},
        auth_fields=(AuthField("token", "Integration secret", placeholder="ntn_…"),),
        auth_note=(
            "Internal integration secret from notion.so/profile/integrations; share the "
            "target pages with the integration. (Notion's hosted mcp.notion.com is "
            "OAuth-only, so Forge runs the official local server instead.)"
        ),
        docs_url="https://github.com/makenotion/notion-mcp-server",
    ),
    CatalogEntry(
        id="linear",
        name="Linear",
        description="Issues, projects, and cycles in Linear.",
        category="productivity",
        mcp_type="remote",
        url="https://mcp.linear.app/mcp",
        auth_fields=(AuthField("token", "API key", placeholder="lin_api_…"),),
        auth_note=(
            "Linear API key (Settings → Security & access → API keys) or an OAuth access "
            "token — both are accepted as Authorization: Bearer."
        ),
        docs_url="https://linear.app/docs/mcp",
    ),
    CatalogEntry(
        id="asana",
        name="Asana",
        description="Tasks and projects in Asana.",
        category="productivity",
        mcp_type="remote",
        url="https://mcp.asana.com/v2/mcp",
        auth_fields=TOKEN,
        auth_note=(
            "OAuth-only per Asana's docs — paste an OAuth access token. The old /sse "
            "endpoint is deprecated (shutdown 05/11/2026); this is the v2 endpoint."
        ),
        docs_url="https://developers.asana.com/docs/using-asanas-mcp-server",
    ),
    CatalogEntry(
        id="atlassian",
        name="Atlassian",
        description="Jira issues and Confluence pages.",
        category="productivity",
        mcp_type="remote",
        url="https://mcp.atlassian.com/v1/mcp",
        auth_fields=TOKEN,
        auth_note=(
            "OAuth 2.1 is the primary flow (paste an OAuth access token); Atlassian also "
            "documents optional API-token auth — see their setup guide."
        ),
        docs_url=(
            "https://support.atlassian.com/rovo/docs/"
            "getting-started-with-the-atlassian-remote-mcp-server/"
        ),
    ),
    CatalogEntry(
        id="monday",
        name="monday.com",
        description="Boards and items in monday.com.",
        category="productivity",
        mcp_type="remote",
        url="https://mcp.monday.com/mcp",
        auth_fields=(AuthField("token", "API token"),),
        auth_note=(
            "Personal API token (monday.com → Developers → My access tokens) as bearer, "
            "or OAuth 2.1."
        ),
        docs_url="https://developer.monday.com/api-reference/docs/integrate-with-monday-mcp",
    ),
    CatalogEntry(
        id="todoist",
        name="Todoist",
        description="Tasks, projects, and filters in Todoist.",
        category="productivity",
        mcp_type="remote",
        url="https://ai.todoist.net/mcp",
        auth_fields=(AuthField("token", "API token"),),
        auth_note=(
            "Todoist API token (Settings → Integrations → Developer) sent as bearer; "
            "OAuth is also supported."
        ),
        docs_url="https://github.com/Doist/todoist-mcp",
    ),
    CatalogEntry(
        id="clickup",
        name="ClickUp",
        description="Tasks, lists, docs, and comments in ClickUp.",
        category="productivity",
        mcp_type="remote",
        url="https://mcp.clickup.com/mcp",
        auth_fields=TOKEN,
        auth_note=(
            "OAuth-only (public beta) with a vetted client list — personal API keys are "
            "rejected; paste an OAuth access token and expect friction outside approved "
            "clients."
        ),
        docs_url=(
            "https://developer.clickup.com/docs/connect-an-ai-assistant-to-clickups-mcp-server"
        ),
    ),
    CatalogEntry(
        id="box",
        name="Box",
        description="Search and query files and enterprise content in Box.",
        category="productivity",
        mcp_type="remote",
        url="https://mcp.box.com",
        auth_fields=TOKEN,
        auth_note=(
            "OAuth 2.1 only — no API-key auth; paste an OAuth access token minted for a "
            "Box app with the MCP integration enabled."
        ),
        docs_url="https://developer.box.com/guides/box-mcp/remote/",
    ),
    CatalogEntry(
        id="slack",
        name="Slack",
        description="Read channels, threads, and DMs; posting is off until you allow it.",
        category="productivity",
        mcp_type="local",
        command=("npx", "-y", "slack-mcp-server@1.3.0", "--transport", "stdio"),
        environment={
            "SLACK_MCP_XOXP_TOKEN": "{env:FORGE_CONN_SLACK_XOXP_TOKEN}",
            "SLACK_MCP_XOXB_TOKEN": "{env:FORGE_CONN_SLACK_XOXB_TOKEN}",
            "SLACK_MCP_ADD_MESSAGE_TOOL": "{env:FORGE_CONN_SLACK_ADD_MESSAGE_TOOL}",
        },
        auth_fields=(
            AuthField("xoxp_token", "User OAuth token", placeholder="xoxp-…"),
            AuthField("xoxb_token", "Bot token", placeholder="xoxb-…"),
            AuthField(
                "add_message_tool",
                "Enable posting (true / channel IDs)",
                secret=False,
                placeholder="true or C0123…,C0456…",
            ),
        ),
        auth_note=(
            "Set a user token (xoxp) or a bot token (xoxb) from a Slack app — user tokens "
            "unlock search; bots must be invited to channels. Message posting stays "
            "disabled unless the third field is set."
        ),
        docs_url="https://github.com/korotovsky/slack-mcp-server",
    ),
    CatalogEntry(
        id="airtable",
        name="Airtable",
        description="Read and write Airtable bases.",
        category="productivity",
        mcp_type="local",
        command=("npx", "-y", "airtable-mcp-server@1.14.0"),
        environment={"AIRTABLE_API_KEY": "{env:FORGE_CONN_AIRTABLE_TOKEN}"},
        auth_fields=(AuthField("token", "Personal access token", placeholder="pat…"),),
        auth_note="Airtable personal access token with the scopes you need.",
        docs_url="https://github.com/domdomegg/airtable-mcp-server",
    ),
    CatalogEntry(
        id="gmail",
        name="Gmail",
        description="Read, search, draft, and send Gmail (community server).",
        category="productivity",
        mcp_type="local",
        command=("npx", "-y", "@shinzolabs/gmail-mcp@1.7.4"),
        environment={
            "CLIENT_ID": "{env:FORGE_CONN_GMAIL_CLIENT_ID}",
            "CLIENT_SECRET": "{env:FORGE_CONN_GMAIL_CLIENT_SECRET}",
            "REFRESH_TOKEN": "{env:FORGE_CONN_GMAIL_REFRESH_TOKEN}",
        },
        auth_fields=(
            AuthField("client_id", "OAuth client ID", secret=False),
            AuthField("client_secret", "OAuth client secret"),
            AuthField("refresh_token", "Refresh token"),
        ),
        auth_note=(
            "Experimental — Google has no public remote MCP. Create a Google Cloud OAuth "
            "client (Desktop) with the Gmail API enabled, run `npx @shinzolabs/gmail-mcp "
            "auth` once on your own machine to mint a refresh token, then paste all three "
            "values here."
        ),
        docs_url="https://github.com/shinzo-labs/gmail-mcp",
    ),
    CatalogEntry(
        id="discord",
        name="Discord",
        description="Read channels and send messages through a Discord bot.",
        category="productivity",
        mcp_type="local",
        command=("npx", "-y", "mcp-discord@1.3.4"),
        environment={"DISCORD_TOKEN": "{env:FORGE_CONN_DISCORD_TOKEN}"},
        auth_fields=(AuthField("token", "Bot token"),),
        auth_note=(
            "Bot token from the Discord Developer Portal; invite the bot to your server "
            "with the permissions you want it to have (community server)."
        ),
        docs_url="https://github.com/barryyip0625/mcp-discord",
    ),
    CatalogEntry(
        id="zapier",
        name="Zapier",
        description="Trigger thousands of app actions through your Zapier MCP endpoint.",
        category="productivity",
        mcp_type="remote",
        url="",  # account-specific: paste your endpoint from mcp.zapier.com
        auth_fields=(
            AuthField(
                "url",
                "Your Zapier MCP URL",
                secret=True,
                placeholder="https://mcp.zapier.com/api/mcp/…",
            ),
        ),
        bearer=False,
        auth_note="Zapier issues a per-account MCP URL (credentials embedded) — paste it here.",
        docs_url="https://zapier.com/mcp",
    ),
    # ── developer ───────────────────────────────────────────────────────────
    CatalogEntry(
        id="sentry",
        name="Sentry",
        description="Query errors, issues, and traces from your Sentry org.",
        category="developer",
        mcp_type="remote",
        url="https://mcp.sentry.dev/mcp",
        auth_fields=TOKEN,
        auth_note=(
            "OAuth-only per Sentry's docs — paste an OAuth access token; plain user auth "
            "tokens are not documented to work here."
        ),
        docs_url="https://mcp.sentry.dev/",
    ),
    CatalogEntry(
        id="vercel",
        name="Vercel",
        description="Projects, deployments, logs, and docs search on Vercel.",
        category="developer",
        mcp_type="remote",
        url="https://mcp.vercel.com",
        auth_fields=TOKEN,
        auth_note=(
            "OAuth-only, and Vercel only approves reviewed MCP clients — no API-token "
            "auth; some tools (docs search) work without authentication."
        ),
        docs_url="https://vercel.com/docs/agent-resources/vercel-mcp",
    ),
    CatalogEntry(
        id="netlify",
        name="Netlify",
        description="Create, deploy, and manage Netlify sites and env vars.",
        category="developer",
        mcp_type="local",
        command=("npx", "-y", "@netlify/mcp@1.15.1"),
        environment={"NETLIFY_PERSONAL_ACCESS_TOKEN": "{env:FORGE_CONN_NETLIFY_TOKEN}"},
        auth_fields=(AuthField("token", "Personal access token", placeholder="nfp_…"),),
        auth_note=(
            "Netlify PAT (User settings → OAuth → New access token). The official local "
            "server is used because Netlify's remote MCP is OAuth-only."
        ),
        docs_url="https://docs.netlify.com/build/build-with-ai/netlify-mcp-server/",
    ),
    CatalogEntry(
        id="supabase",
        name="Supabase",
        description="Query databases, manage migrations, and inspect Supabase projects.",
        category="developer",
        mcp_type="remote",
        url="https://mcp.supabase.com/mcp",
        auth_fields=(AuthField("token", "Personal access token", placeholder="sbp_…"),),
        auth_note=(
            "Personal access token (supabase.com/dashboard/account/tokens) as bearer — "
            "documented for CI use; OAuth login is the interactive default."
        ),
        docs_url="https://supabase.com/docs/guides/getting-started/mcp",
    ),
    CatalogEntry(
        id="cloudflare",
        name="Cloudflare docs",
        description="Search Cloudflare's documentation.",
        category="developer",
        mcp_type="remote",
        url="https://docs.mcp.cloudflare.com/mcp",
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
    # ── design ──────────────────────────────────────────────────────────────
    CatalogEntry(
        id="figma",
        name="Figma",
        description="Read designs, components, and variables from Figma files.",
        category="design",
        mcp_type="remote",
        url="https://mcp.figma.com/mcp",
        auth_fields=TOKEN,
        auth_note=(
            "OAuth-only — the remote server authenticates via Figma's OAuth flow; "
            "personal access tokens are not documented to work."
        ),
        docs_url="https://developers.figma.com/docs/figma-mcp-server/",
    ),
    CatalogEntry(
        id="canva",
        name="Canva",
        description="Create and edit Canva designs.",
        category="design",
        mcp_type="remote",
        url="https://mcp.canva.com/mcp",
        auth_fields=TOKEN,
        auth_note="OAuth-only — Canva's MCP authenticates via its OAuth consent flow.",
        docs_url="https://www.canva.com/help/mcp-agent-setup/",
    ),
    CatalogEntry(
        id="higgsfield",
        name="Higgsfield",
        description="Generate images, video, and audio with Higgsfield's AI models.",
        category="design",
        mcp_type="remote",
        url="https://mcp.higgsfield.ai/mcp",
        auth_fields=TOKEN,
        auth_note=(
            "OAuth-only — the hosted MCP signs in with your Higgsfield account "
            "(OAuth 2.1 + PKCE, no API-key path); paste an OAuth access token. "
            "Generations spend your Higgsfield plan credits."
        ),
        docs_url="https://higgsfield.ai/mcp",
    ),
    # ── business ────────────────────────────────────────────────────────────
    CatalogEntry(
        id="stripe",
        name="Stripe",
        description="Customers, invoices, payments, and docs search.",
        category="business",
        mcp_type="remote",
        url="https://mcp.stripe.com",
        auth_fields=(AuthField("token", "API key", placeholder="rk_live_… / sk_test_…"),),
        auth_note=(
            "Restricted API key as bearer is officially supported (recommended for "
            "agents); OAuth is the interactive default."
        ),
        docs_url="https://docs.stripe.com/mcp",
    ),
    CatalogEntry(
        id="intercom",
        name="Intercom",
        description="Conversations and customer data from Intercom.",
        category="business",
        mcp_type="remote",
        url="https://mcp.intercom.com/mcp",
        auth_fields=(AuthField("token", "Access token"),),
        auth_note=(
            "Intercom access token (Developer Hub app) as bearer, or OAuth. EU-hosted "
            "workspaces should use https://mcp.eu.intercom.com/mcp via a custom connector."
        ),
        docs_url="https://developers.intercom.com/docs/guides/mcp",
    ),
    CatalogEntry(
        id="hubspot",
        name="HubSpot",
        description="CRM contacts, companies, deals, and tickets in HubSpot.",
        category="business",
        mcp_type="local",
        command=("npx", "-y", "@hubspot/mcp-server@0.4.0"),
        environment={"PRIVATE_APP_ACCESS_TOKEN": "{env:FORGE_CONN_HUBSPOT_TOKEN}"},
        auth_fields=(AuthField("token", "Private app token", placeholder="pat-na1-…"),),
        auth_note=(
            "Private app access token (Settings → Integrations → Private apps). The "
            "official local server is used because HubSpot's remote MCP is OAuth-only."
        ),
        docs_url="https://developers.hubspot.com/mcp",
    ),
    CatalogEntry(
        id="paypal",
        name="PayPal",
        description="Invoices, payments, and disputes via PayPal's MCP.",
        category="business",
        mcp_type="remote",
        url="https://mcp.paypal.com/mcp",
        auth_fields=TOKEN,
        auth_note=(
            "OAuth access token generated from your PayPal app's client ID + secret "
            "(developer.paypal.com), passed as bearer."
        ),
        docs_url="https://developer.paypal.com/tools/mcp-server/",
    ),
    CatalogEntry(
        id="square",
        name="Square",
        description="Payments, catalog, and customers in Square.",
        category="business",
        mcp_type="remote",
        url="https://mcp.squareup.com/mcp",
        auth_fields=TOKEN,
        auth_note=(
            "OAuth is the documented flow for the remote server — paste an OAuth access "
            "token; developer-dashboard access tokens are not documented to work here."
        ),
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
        auth_note=(
            "OAuth client-credentials token (scope mcp:dashboard) as bearer — tokens "
            "expire after ~15 minutes, so expect frequent re-auth."
        ),
        docs_url="https://plaid.com/docs/resources/mcp/",
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
