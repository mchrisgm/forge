"""Connector catalog — every MCP connector Forge can wire into sessions.

Two tiers share one framework and one DB table:

- **Core** (PLAN §1.8): github, fetch, searxng, playwright, skills — the
  built-ins that ship enabled (github off until a PAT is set).
- **Integrations**: every officially hosted, publicly reachable remote MCP
  server we could verify (Claude's connector directory and beyond), plus a
  handful of high-value official local servers. Remote entries authenticate
  with a token pasted in the UI (an API key, PAT, or OAuth access token
  obtained from the service — noted per entry; auth_header/auth_scheme cover
  vendors that don't use plain `Authorization: Bearer`); local stdio servers
  are configured via env vars.

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
    # For remote entries with a "token" auth field: whether to send it as a
    # request header at all, which header carries it, and the scheme prefix
    # ("" = the raw token, e.g. X-API-Key style headers).
    bearer: bool = True
    auth_header: str = "Authorization"
    auth_scheme: str = "Bearer"
    # Static, non-secret companion headers some vendors require next to the
    # auth header (e.g. Close's mandatory Close-Scope). Sent only when the
    # auth header is — they qualify the authenticated request.
    extra_headers: dict[str, str] = field(default_factory=dict)
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
    CatalogEntry(
        id="make",
        name="Make",
        description="Run your Make (Integromat) scenarios as agent tools.",
        category="productivity",
        mcp_type="remote",
        url="",  # account-specific: zone URL with the MCP token embedded
        auth_fields=(
            AuthField(
                "url",
                "Your Make MCP URL",
                secret=True,
                placeholder="https://<zone>.make.com/mcp/u/<token>/sse",
            ),
        ),
        bearer=False,
        auth_note=(
            "Make issues a per-account MCP URL embedding an MCP token (Profile → API "
            "access → Add token, scope mcp:use): https://<zone>.make.com/mcp/u/<token>/sse "
            "— paste it here. (mcp.make.com is the OAuth alternative for interactive "
            "clients.)"
        ),
        docs_url="https://developers.make.com/mcp-server",
    ),
    CatalogEntry(
        id="fireflies",
        name="Fireflies",
        description="Search meeting transcripts, summaries, and action items.",
        category="productivity",
        mcp_type="remote",
        url="https://api.fireflies.ai/mcp",
        auth_fields=(AuthField("token", "API key"),),
        auth_note=(
            "Fireflies API key (app.fireflies.ai → Integrations → Fireflies API) as "
            "bearer; OAuth is also supported."
        ),
        docs_url="https://docs.fireflies.ai/getting-started/mcp-configuration",
    ),
    CatalogEntry(
        id="dart",
        name="Dart",
        description="Tasks, docs, and projects in Dart's AI project manager.",
        category="productivity",
        mcp_type="remote",
        url="https://mcp.dartai.com/mcp",
        auth_fields=(AuthField("token", "Auth token", placeholder="dsa_…"),),
        auth_note=(
            "Authentication token from app.dartai.com → Settings → Account (dsa_…) "
            "as bearer."
        ),
        docs_url="https://help.dartai.com/en/articles/10733406-dart-mcp",
    ),
    CatalogEntry(
        id="miro",
        name="Miro",
        description="Read and create content on Miro boards.",
        category="productivity",
        mcp_type="remote",
        url="https://mcp.miro.com",
        auth_fields=TOKEN,
        auth_note=(
            "OAuth 2.1 only — the server signs in with your Miro account and is scoped "
            "to the team you pick during consent; paste an OAuth access token."
        ),
        docs_url="https://developers.miro.com/docs/miro-mcp",
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
    CatalogEntry(
        id="github-remote",
        name="GitHub (remote)",
        description="GitHub's hosted MCP server — repos, issues, PRs, and Actions.",
        category="developer",
        mcp_type="remote",
        url="https://api.githubcopilot.com/mcp/",
        auth_fields=(AuthField("token", "Personal access token", placeholder="ghp_…"),),
        auth_note=(
            "PAT as bearer works (OAuth is the interactive default); tool visibility "
            "follows the token's scopes. Hosted alternative to the core GitHub "
            "connector — unlike the local server it cannot enable git push."
        ),
        docs_url="https://github.com/github/github-mcp-server",
    ),
    CatalogEntry(
        id="gitlab",
        name="GitLab",
        description="Projects, issues, and merge requests on gitlab.com.",
        category="developer",
        mcp_type="remote",
        url="https://gitlab.com/api/v4/mcp",
        auth_fields=TOKEN,
        auth_note=(
            "OAuth 2.1 only — paste an OAuth access token; PAT support is still an "
            "open GitLab issue. Self-managed GitLab (≥18.2) exposes the same "
            "/api/v4/mcp path — add yours as a custom connector."
        ),
        docs_url="https://docs.gitlab.com/user/model_context_protocol/mcp_server/",
    ),
    CatalogEntry(
        id="deepwiki",
        name="DeepWiki",
        description="Ask questions about any public GitHub repo (AI-generated wikis).",
        category="developer",
        mcp_type="remote",
        url="https://mcp.deepwiki.com/mcp",
        auth_note="No auth needed — free public server by Devin/Cognition.",
        docs_url="https://docs.devin.ai/work-with-devin/deepwiki-mcp",
    ),
    CatalogEntry(
        id="context7",
        name="Context7",
        description="Up-to-date, version-specific docs for thousands of libraries.",
        category="developer",
        mcp_type="remote",
        url="https://mcp.context7.com/mcp",
        auth_fields=(AuthField("token", "API key (optional)", placeholder="ctx7sk-…"),),
        auth_header="CONTEXT7_API_KEY",
        auth_scheme="",
        auth_note=(
            "Works without a key (tight rate limits); a free key from "
            "context7.com/dashboard is sent as a CONTEXT7_API_KEY header."
        ),
        docs_url="https://github.com/upstash/context7",
    ),
    CatalogEntry(
        id="microsoft-learn",
        name="Microsoft Learn",
        description="Search official Microsoft/Azure docs and code samples.",
        category="developer",
        mcp_type="remote",
        url="https://learn.microsoft.com/api/mcp",
        auth_note="No auth needed — public docs server.",
        docs_url="https://github.com/MicrosoftDocs/mcp",
    ),
    CatalogEntry(
        id="aws-knowledge",
        name="AWS Knowledge",
        description="AWS docs, API references, What's New, and architecture guidance.",
        category="developer",
        mcp_type="remote",
        url="https://knowledge-mcp.global.api.aws",
        auth_note="No auth needed — public AWS-hosted server (rate-limited).",
        docs_url="https://awslabs.github.io/mcp/servers/aws-knowledge-mcp-server/",
    ),
    CatalogEntry(
        id="astro-docs",
        name="Astro docs",
        description="Search the Astro web-framework documentation.",
        category="developer",
        mcp_type="remote",
        url="https://mcp.docs.astro.build/mcp",
        auth_note="No auth needed — public docs server.",
        docs_url="https://github.com/withastro/docs-mcp",
    ),
    CatalogEntry(
        id="twilio-docs",
        name="Twilio docs",
        description="Search Twilio, SendGrid, and Segment API docs and OpenAPI specs.",
        category="developer",
        mcp_type="remote",
        url="https://mcp.twilio.com/docs",
        auth_note=(
            "No auth needed — public docs/spec search (beta); it does not execute "
            "API calls against your Twilio account."
        ),
        docs_url="https://www.twilio.com/docs/ai/mcp",
    ),
    CatalogEntry(
        id="cloudflare-bindings",
        name="Cloudflare Workers",
        description="Build and manage Workers, KV, R2, and D1 in your Cloudflare account.",
        category="developer",
        mcp_type="remote",
        url="https://bindings.mcp.cloudflare.com/mcp",
        auth_fields=TOKEN,
        auth_note="OAuth-only — sign in with your Cloudflare account; paste an OAuth access token.",
        docs_url="https://github.com/cloudflare/mcp-server-cloudflare",
    ),
    CatalogEntry(
        id="cloudflare-observability",
        name="Cloudflare observability",
        description="Workers logs, analytics, and debugging for your Cloudflare account.",
        category="developer",
        mcp_type="remote",
        url="https://observability.mcp.cloudflare.com/mcp",
        auth_fields=TOKEN,
        auth_note="OAuth-only — sign in with your Cloudflare account; paste an OAuth access token.",
        docs_url="https://github.com/cloudflare/mcp-server-cloudflare",
    ),
    CatalogEntry(
        id="cloudflare-radar",
        name="Cloudflare Radar",
        description="Internet traffic insights, trends, and URL scanning via Radar.",
        category="developer",
        mcp_type="remote",
        url="https://radar.mcp.cloudflare.com/mcp",
        auth_fields=TOKEN,
        auth_note=(
            "OAuth-only — sign in with a (free) Cloudflare account; paste an OAuth "
            "access token."
        ),
        docs_url="https://github.com/cloudflare/mcp-server-cloudflare",
    ),
    CatalogEntry(
        id="cloudflare-browser",
        name="Cloudflare browser rendering",
        description="Fetch pages, take screenshots, and convert to markdown in the cloud.",
        category="developer",
        mcp_type="remote",
        url="https://browser.mcp.cloudflare.com/mcp",
        auth_fields=TOKEN,
        auth_note="OAuth-only — sign in with your Cloudflare account; paste an OAuth access token.",
        docs_url="https://github.com/cloudflare/mcp-server-cloudflare",
    ),
    CatalogEntry(
        id="neon",
        name="Neon",
        description="Create and query Neon serverless Postgres projects and branches.",
        category="developer",
        mcp_type="remote",
        url="https://mcp.neon.tech/mcp",
        auth_fields=(AuthField("token", "API key", placeholder="napi_…"),),
        auth_note=(
            "Neon API key (console.neon.tech → Account settings → API keys) as "
            "bearer; OAuth is also supported."
        ),
        docs_url="https://neon.com/docs/ai/neon-mcp-server",
    ),
    CatalogEntry(
        id="prisma-postgres",
        name="Prisma Postgres",
        description="Manage Prisma Postgres databases, backups, and connection strings.",
        category="developer",
        mcp_type="remote",
        url="https://mcp.prisma.io/mcp",
        auth_fields=TOKEN,
        auth_note=(
            "OAuth-only — authenticates against the Prisma Console in a browser "
            "popup; paste an OAuth access token."
        ),
        docs_url="https://www.prisma.io/docs/postgres/integrations/mcp-server",
    ),
    CatalogEntry(
        id="render",
        name="Render",
        description="Services, deploys, logs, and Postgres on Render.",
        category="developer",
        mcp_type="remote",
        url="https://mcp.render.com/mcp",
        auth_fields=(AuthField("token", "API key", placeholder="rnd_…"),),
        auth_note=(
            "Render API key (Account settings → API keys) as bearer — documented for "
            "CI/headless use; OAuth is the interactive default."
        ),
        docs_url="https://render.com/docs/mcp-server",
    ),
    CatalogEntry(
        id="heroku",
        name="Heroku",
        description="Apps, dynos, add-ons, and logs on Heroku.",
        category="developer",
        mcp_type="remote",
        url="https://mcp.heroku.com/mcp",
        auth_fields=TOKEN,
        auth_note=(
            "OAuth-only — the hosted server signs in via id.heroku.com; paste an "
            "OAuth access token. (The local heroku-mcp-server npm package takes an "
            "API key instead — add it as a custom connector if you prefer keys.)"
        ),
        docs_url="https://devcenter.heroku.com/articles/heroku-remote-mcp-server",
    ),
    CatalogEntry(
        id="buildkite",
        name="Buildkite",
        description="Pipelines, builds, jobs, and test results in Buildkite.",
        category="developer",
        mcp_type="remote",
        url="https://mcp.buildkite.com/direct",
        auth_fields=(AuthField("token", "API access token", placeholder="bkua_…"),),
        auth_note=(
            "API access token (buildkite.com/user/api-access-tokens) as bearer "
            "against the token pass-through /direct endpoint; token scopes gate the "
            "tools. Append /readonly via a custom connector to force read-only; "
            "the /mcp endpoint is the interactive OAuth flow."
        ),
        docs_url="https://buildkite.com/docs/apis/mcp-server",
    ),
    CatalogEntry(
        id="circleci",
        name="CircleCI",
        description="Investigate failed builds, logs, and flaky tests in CircleCI.",
        category="developer",
        mcp_type="remote",
        url="https://mcp.circleci.com/v1/mcp",
        auth_fields=TOKEN,
        auth_note=(
            "OAuth-only — the hosted server requires CircleCI sign-in; paste an "
            "OAuth access token. (The deprecated npm server took PATs; CircleCI now "
            "points headless users at its CLI MCP instead.)"
        ),
        docs_url="https://circleci.com/docs/guides/toolkit/circleci-mcp-overview/",
    ),
    CatalogEntry(
        id="semgrep",
        name="Semgrep",
        description="Scan code for security vulnerabilities with Semgrep rules.",
        category="developer",
        mcp_type="remote",
        url="https://mcp.semgrep.ai/mcp",
        auth_fields=TOKEN,
        auth_note=(
            "Sign-in now required (the once-open beta endpoint returns 401 "
            "anonymously) — OAuth via semgrep.dev; paste an OAuth access token. "
            "semgrep.dev API tokens are not documented to work here."
        ),
        docs_url="https://github.com/semgrep/mcp",
    ),
    CatalogEntry(
        id="exa",
        name="Exa",
        description="AI-native web search, crawling, and company/code research.",
        category="developer",
        mcp_type="remote",
        url="https://mcp.exa.ai/mcp",
        auth_fields=(AuthField("token", "API key (optional)"),),
        auth_note=(
            "Works anonymously with rate limits; an Exa API key (dashboard.exa.ai) "
            "as bearer unlocks higher limits and the full tool set."
        ),
        docs_url="https://docs.exa.ai/reference/exa-mcp",
    ),
    CatalogEntry(
        id="tavily",
        name="Tavily",
        description="Real-time web search, extract, map, and crawl for agents.",
        category="developer",
        mcp_type="remote",
        url="https://mcp.tavily.com/mcp",
        auth_fields=(AuthField("token", "API key", placeholder="tvly-…"),),
        auth_note=(
            "Tavily API key (app.tavily.com) sent as bearer; OAuth is also "
            "supported. (The documented ?tavilyApiKey= URL variant works as a "
            "custom connector too.)"
        ),
        docs_url="https://docs.tavily.com/documentation/mcp",
    ),
    CatalogEntry(
        id="firecrawl",
        name="Firecrawl",
        description="Scrape, crawl, and extract websites as clean markdown.",
        category="developer",
        mcp_type="remote",
        url="",  # account-specific: the API key is a URL path segment
        auth_fields=(
            AuthField(
                "url",
                "Your Firecrawl MCP URL",
                secret=True,
                placeholder="https://mcp.firecrawl.dev/<api-key>/v2/mcp",
            ),
        ),
        bearer=False,
        auth_note=(
            "Firecrawl embeds your API key (firecrawl.dev/app → API keys) in the "
            "endpoint path: https://mcp.firecrawl.dev/<key>/v2/mcp — paste the full "
            "URL here and treat it as a secret."
        ),
        docs_url="https://docs.firecrawl.dev/mcp-server",
    ),
    CatalogEntry(
        id="apify",
        name="Apify",
        description="Run 6,000+ Apify Actors: scrapers, crawlers, and automations.",
        category="developer",
        mcp_type="remote",
        url="https://mcp.apify.com",
        auth_fields=(AuthField("token", "API token", placeholder="apify_api_…"),),
        auth_note=(
            "Apify API token (console.apify.com → Settings → API & integrations) as "
            "bearer; OAuth is also supported. Actor runs bill your Apify account."
        ),
        docs_url="https://docs.apify.com/platform/integrations/mcp",
    ),
    CatalogEntry(
        id="browserbase",
        name="Browserbase",
        description="Cloud browser automation with Stagehand: navigate, act, extract.",
        category="developer",
        mcp_type="remote",
        url="",  # account-specific: the API key travels as a query parameter
        auth_fields=(
            AuthField(
                "url",
                "Your Browserbase MCP URL",
                secret=True,
                placeholder="https://mcp.browserbase.com/mcp?browserbaseApiKey=<key>",
            ),
        ),
        bearer=False,
        auth_note=(
            "Browserbase puts the API key (Dashboard → Settings) in the URL query: "
            "paste your full endpoint here and treat it as a secret. Sessions bill "
            "your Browserbase project."
        ),
        docs_url="https://docs.browserbase.com/integrations/mcp/setup",
    ),
    CatalogEntry(
        id="globalping",
        name="Globalping",
        description="Run ping, traceroute, DNS, and HTTP probes from a global network.",
        category="developer",
        mcp_type="remote",
        url="https://mcp.globalping.dev/mcp",
        auth_fields=(AuthField("token", "API token"),),
        auth_note=(
            "Globalping API token (dash.globalping.io → Tokens) as bearer, or OAuth "
            "— the hosted endpoint rejects unauthenticated sessions."
        ),
        docs_url="https://github.com/jsdelivr/globalping-mcp-server",
    ),
    CatalogEntry(
        id="grafana",
        name="Grafana Cloud",
        description="Dashboards, datasources, incidents, and alerts in Grafana Cloud.",
        category="developer",
        mcp_type="remote",
        url="https://mcp.grafana.com/mcp",
        auth_fields=TOKEN,
        auth_note=(
            "OAuth 2.1 only — sign in with your Grafana Cloud account; paste an "
            "OAuth access token. Self-hosted Grafana: run the local mcp-grafana "
            "binary with a service-account token as a custom connector instead."
        ),
        docs_url="https://grafana.com/docs/grafana-cloud/ai-tools/mcp-servers/cloud-mcp/",
    ),
    CatalogEntry(
        id="honeycomb",
        name="Honeycomb",
        description="Query observability data, triggers, and SLOs in Honeycomb.",
        category="developer",
        mcp_type="remote",
        url="https://mcp.honeycomb.io/mcp",
        auth_fields=(AuthField("token", "Management API key", placeholder="<id>:<secret>"),),
        auth_note=(
            "Enterprise feature. Management API key (ID:secret pair with 'Model "
            "Context Protocol' + 'Environments' scopes) as bearer, or OAuth. EU "
            "workspaces: mcp.eu1.honeycomb.io via a custom connector."
        ),
        docs_url="https://docs.honeycomb.io/integrations/mcp/configuration-guide",
    ),
    CatalogEntry(
        id="postman",
        name="Postman",
        description="Collections, workspaces, and environments in Postman.",
        category="developer",
        mcp_type="remote",
        url="https://mcp.postman.com/mcp",
        auth_fields=(AuthField("token", "API key", placeholder="PMAK-…"),),
        auth_note=(
            "Postman API key (Settings → API keys) as bearer, or OAuth. Smaller "
            "/minimal and /code tool sets plus the EU host (mcp.eu.postman.com) are "
            "available via custom connectors."
        ),
        docs_url=(
            "https://learning.postman.com/docs/reference/postman-api/"
            "postman-mcp-server/postman-mcp-remote-server"
        ),
    ),
    CatalogEntry(
        id="sanity",
        name="Sanity",
        description="Query and edit content in your Sanity datasets.",
        category="developer",
        mcp_type="remote",
        url="https://mcp.sanity.io",
        auth_fields=(AuthField("token", "API token", placeholder="sk…"),),
        auth_note=(
            "Sanity API token (a robot token from sanity.io/manage, or `sanity debug "
            "--secrets`) as bearer; OAuth is the interactive default. Tool access "
            "follows the token's role."
        ),
        docs_url="https://www.sanity.io/docs/ai/mcp-server",
    ),
    CatalogEntry(
        id="replicate",
        name="Replicate",
        description="Search and run thousands of AI models on Replicate.",
        category="developer",
        mcp_type="remote",
        url="https://mcp.replicate.com/mcp",
        auth_fields=TOKEN,
        auth_note=(
            "Auth runs through Replicate's web flow, where you paste your API token "
            "(r8_…) once; sending r8_ tokens directly as bearer is not documented — "
            "paste an OAuth access token if you have one. Model runs bill your "
            "Replicate account."
        ),
        docs_url="https://replicate.com/docs/reference/mcp",
    ),
    CatalogEntry(
        id="vapi",
        name="Vapi",
        description="Manage voice-AI assistants, phone numbers, and calls in Vapi.",
        category="developer",
        mcp_type="remote",
        url="https://mcp.vapi.ai/mcp",
        auth_fields=(AuthField("token", "API key"),),
        auth_note="Vapi API key (dashboard.vapi.ai → Org → API keys) as bearer.",
        docs_url="https://docs.vapi.ai/sdk/mcp-server",
    ),
    CatalogEntry(
        id="bright-data",
        name="Bright Data",
        description="Scrape and search the public web at scale without being blocked.",
        category="developer",
        mcp_type="remote",
        url="",  # account-specific: the API token travels as a query parameter
        auth_fields=(
            AuthField(
                "url",
                "Your Bright Data MCP URL",
                secret=True,
                placeholder="https://mcp.brightdata.com/mcp?token=<api-token>",
            ),
        ),
        bearer=False,
        auth_note=(
            "Bright Data puts the API token (brightdata.com → Settings → API tokens) "
            "in the URL query — paste the full URL here and treat it as a secret; "
            "append &pro=1 for the paid full tool set."
        ),
        docs_url="https://github.com/brightdata/brightdata-mcp",
    ),
    CatalogEntry(
        id="pinecone",
        name="Pinecone Assistant",
        description="Retrieve grounded context from your Pinecone Assistants.",
        category="developer",
        mcp_type="remote",
        url="",  # account-specific: each assistant has its own endpoint
        auth_fields=(
            AuthField(
                "url",
                "Assistant MCP URL",
                secret=False,
                placeholder="https://<assistant-host>/mcp/assistants/<assistant>",
            ),
            AuthField("token", "API key", placeholder="pcsk_…"),
        ),
        auth_note=(
            "Paste your assistant's endpoint (host shown on its Pinecone console "
            "page) plus a Pinecone API key as bearer."
        ),
        docs_url="https://docs.pinecone.io/guides/assistant/mcp-server",
    ),
    CatalogEntry(
        id="algolia",
        name="Algolia",
        description="Explore Algolia indices, analytics, and search configuration.",
        category="developer",
        mcp_type="remote",
        url="https://mcp.algolia.com/mcp",
        auth_fields=TOKEN,
        auth_note=(
            "OAuth-only — signs in with your Algolia identity (read-only tools); "
            "paste an OAuth access token."
        ),
        docs_url="https://www.algolia.com/doc/guides/model-context-protocol/productivity-mcp",
    ),
    CatalogEntry(
        id="stytch",
        name="Stytch",
        description="Manage Stytch auth projects, redirect URLs, and email templates.",
        category="developer",
        mcp_type="remote",
        url="https://mcp.stytch.dev/mcp",
        auth_fields=TOKEN,
        auth_note=(
            "OAuth 2.1 only (dynamic client registration) — paste an OAuth access "
            "token from your Stytch workspace sign-in."
        ),
        docs_url="https://stytch.com/docs/workspace-management/stytch-mcp",
    ),
    CatalogEntry(
        id="jam",
        name="Jam",
        description="Load Jam bug recordings — video, console, and network — into context.",
        category="developer",
        mcp_type="remote",
        url="https://mcp.jam.dev/mcp",
        auth_fields=TOKEN,
        auth_note="OAuth-only — sign in with your Jam account; paste an OAuth access token.",
        docs_url="https://jam.dev/docs/jam-mcp",
    ),
    CatalogEntry(
        id="mongodb",
        name="MongoDB",
        description="Query and manage MongoDB databases (Atlas or self-hosted).",
        category="developer",
        mcp_type="local",
        command=("npx", "-y", "mongodb-mcp-server@2.1.0"),
        environment={
            "MDB_MCP_CONNECTION_STRING": "{env:FORGE_CONN_MONGODB_CONNECTION_STRING}",
            "MDB_MCP_READ_ONLY": "{env:FORGE_CONN_MONGODB_READ_ONLY}",
        },
        auth_fields=(
            AuthField(
                "connection_string",
                "Connection string",
                placeholder="mongodb+srv://…",
            ),
            AuthField(
                "read_only",
                "Read-only (true / false)",
                secret=False,
                placeholder="true",
            ),
        ),
        auth_note=(
            "Official MongoDB server. Connection string for your deployment; set "
            "read-only to true to block writes. Atlas admin tools additionally need "
            "service-account credentials — see the docs."
        ),
        docs_url="https://github.com/mongodb-js/mongodb-mcp-server",
    ),
    CatalogEntry(
        id="redis",
        name="Redis",
        description="Search, query, and manage data in any Redis instance.",
        category="developer",
        mcp_type="local",
        command=("uvx", "--from", "redis-mcp-server==0.5.1", "redis-mcp-server"),
        environment={
            "REDIS_HOST": "{env:FORGE_CONN_REDIS_HOST}",
            "REDIS_PORT": "{env:FORGE_CONN_REDIS_PORT}",
            "REDIS_USERNAME": "{env:FORGE_CONN_REDIS_USERNAME}",
            "REDIS_PWD": "{env:FORGE_CONN_REDIS_PWD}",
        },
        auth_fields=(
            AuthField("host", "Host", secret=False, placeholder="redis.example.com"),
            AuthField("port", "Port", secret=False, placeholder="6379"),
            AuthField("username", "Username", secret=False, placeholder="default"),
            AuthField("pwd", "Password"),
        ),
        auth_note=(
            "Official Redis server — point it at any reachable Redis (Cloud or "
            "self-hosted). TLS, Cluster, and Sentinel need extra env vars — see the "
            "docs."
        ),
        docs_url="https://github.com/redis/mcp-redis",
    ),
    CatalogEntry(
        id="shopify-dev",
        name="Shopify dev docs",
        description="Search Shopify dev docs and Admin GraphQL schema; validate queries.",
        category="developer",
        mcp_type="local",
        command=("npx", "-y", "@shopify/dev-mcp@1.14.5"),
        auth_note="No auth needed — official docs/schema server, no store access.",
        docs_url="https://github.com/Shopify/dev-mcp",
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
    CatalogEntry(
        id="webflow",
        name="Webflow",
        description="Sites, CMS collections, and pages in Webflow.",
        category="design",
        mcp_type="remote",
        url="https://mcp.webflow.com/mcp",
        auth_fields=TOKEN,
        auth_note=(
            "OAuth-only — only site owners/admins can authorize; paste an OAuth "
            "access token. Site tokens work for Webflow's Data API but not the MCP."
        ),
        docs_url="https://developers.webflow.com/mcp/reference/how-it-works",
    ),
    CatalogEntry(
        id="wix",
        name="Wix",
        description="Manage Wix sites, content, bookings, and e-commerce.",
        category="design",
        mcp_type="remote",
        url="https://mcp.wix.com/mcp",
        auth_fields=TOKEN,
        auth_note=(
            "OAuth is the standard flow — paste an OAuth access token. API-key auth "
            "exists but needs an extra wix-account-id header; use a custom connector "
            "for that pair."
        ),
        docs_url="https://dev.wix.com/docs/sdk/articles/use-the-wix-mcp/about-the-wix-mcp",
    ),
    CatalogEntry(
        id="heygen",
        name="HeyGen",
        description="Generate avatar videos with HeyGen.",
        category="design",
        mcp_type="remote",
        url="https://mcp.heygen.com/mcp",
        auth_fields=TOKEN,
        auth_note=(
            "OAuth-only — signs in with your HeyGen account; paste an OAuth access "
            "token. Generations spend your plan credits. (X-Api-Key auth belongs to "
            "HeyGen's plain API, not the MCP.)"
        ),
        docs_url="https://developers.heygen.com/mcp/overview",
    ),
    CatalogEntry(
        id="cloudinary",
        name="Cloudinary",
        description="Upload, search, and manage media assets in Cloudinary.",
        category="design",
        mcp_type="remote",
        url="https://asset-management.mcp.cloudinary.com/mcp",
        auth_fields=TOKEN,
        auth_note=(
            "OAuth by default — paste an OAuth access token. API-key auth needs "
            "three CLOUDINARY_* headers, which requires a custom connector. Sibling "
            "hosted servers (environment-config, structured-metadata, analysis) "
            "follow the same pattern."
        ),
        docs_url="https://github.com/cloudinary/mcp-servers",
    ),
    CatalogEntry(
        id="elevenlabs",
        name="ElevenLabs",
        description="Text-to-speech, voice cloning, and audio tools from ElevenLabs.",
        category="design",
        mcp_type="local",
        command=("uvx", "elevenlabs-mcp==0.12.2"),
        environment={"ELEVENLABS_API_KEY": "{env:FORGE_CONN_ELEVENLABS_TOKEN}"},
        auth_fields=(AuthField("token", "API key", placeholder="sk_…"),),
        auth_note=(
            "ElevenLabs API key (elevenlabs.io → Developers → API keys). The "
            "official local server is used because the hosted MCP "
            "(api.elevenlabs.io/v1/mcp) is OAuth-only and covers agent management "
            "only. Generation spends your plan credits."
        ),
        docs_url="https://github.com/elevenlabs/elevenlabs-mcp",
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
    CatalogEntry(
        id="attio",
        name="Attio",
        description="Records, lists, and notes in the Attio CRM.",
        category="business",
        mcp_type="remote",
        url="https://mcp.attio.com/mcp",
        auth_fields=TOKEN,
        auth_note=(
            "OAuth-only — sign in with your Attio account (reads auto-approved, "
            "writes confirmed); paste an OAuth access token."
        ),
        docs_url="https://docs.attio.com/mcp/overview",
    ),
    CatalogEntry(
        id="close",
        name="Close",
        description="Leads, contacts, opportunities, and activities in Close CRM.",
        category="business",
        mcp_type="remote",
        url="https://mcp.close.com/mcp",
        auth_fields=(AuthField("token", "API key", placeholder="api_…"),),
        auth_header="Close-API-Key",
        auth_scheme="",
        extra_headers={"Close-Scope": "mcp.read"},
        auth_note=(
            "Close API key (Settings → Developer → API keys) in a Close-API-Key "
            "header. Forge pins the required Close-Scope header to mcp.read "
            "(read-only); for writes use a custom connector with Close-Scope: "
            "mcp.write_safe or mcp.write_destructive. OAuth is also supported."
        ),
        docs_url="https://developer.close.com/mcp",
    ),
    CatalogEntry(
        id="paddle",
        name="Paddle",
        description="Products, prices, subscriptions, and reports in Paddle Billing.",
        category="business",
        mcp_type="remote",
        url="https://mcp.paddle.com/mcp",
        auth_fields=(AuthField("token", "API key", placeholder="pdl_live_…"),),
        auth_note=(
            "Live API key (Paddle → Developer tools → Authentication) as bearer; "
            "OAuth is also supported. Sandbox keys only work against Paddle's "
            "sandbox MCP host — see the docs."
        ),
        docs_url="https://developer.paddle.com/sdks/ai/paddle-mcp",
    ),
    CatalogEntry(
        id="pagerduty",
        name="PagerDuty",
        description="Incidents, services, schedules, and on-calls in PagerDuty.",
        category="business",
        mcp_type="remote",
        url="https://mcp.pagerduty.com/mcp",
        auth_fields=TOKEN,
        auth_note=(
            "Paste an OAuth access token as bearer (App OAuth via client "
            "credentials works headlessly). User API tokens use PagerDuty's "
            "'Token token=…' scheme, which needs a custom connector. EU accounts: "
            "mcp.eu.pagerduty.com."
        ),
        docs_url="https://support.pagerduty.com/main/docs/pagerduty-mcp-server",
    ),
    CatalogEntry(
        id="coingecko",
        name="CoinGecko",
        description="Live and historical crypto prices, DeFi pools, and NFT data.",
        category="business",
        mcp_type="remote",
        url="https://mcp.api.coingecko.com/mcp",
        auth_note=(
            "No auth needed — free keyless public beta (rate-limited). Paid plans "
            "get higher limits on the Pro host (mcp.pro-api.coingecko.com) via a "
            "custom connector."
        ),
        docs_url="https://docs.coingecko.com/docs/mcp-server",
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
            token_ref = f"{{env:{env_var(entry.id, 'token')}}}"
            value = (
                f"{entry.auth_scheme} {token_ref}" if entry.auth_scheme else token_ref
            )
            block["headers"] = {**entry.extra_headers, entry.auth_header: value}
        return block
    block = {
        "type": "local",
        "command": list(entry.command),
        "enabled": bool(enabled),
    }
    if entry.environment:
        block["environment"] = dict(entry.environment)
    return block


def request_headers(entry: CatalogEntry, config: dict) -> dict[str, str]:
    """Auth headers for an ORCHESTRATOR-side call to a remote entry — carries
    the real secret value (render_block's {env:...} indirection only exists
    for configs written into session containers)."""
    if entry.mcp_type != "remote":
        return {}
    token = config.get("token")
    has_token = any(f.key == "token" for f in entry.auth_fields)
    if not (token and has_token and entry.bearer):
        return {}
    value = f"{entry.auth_scheme} {token}" if entry.auth_scheme else str(token)
    return {**entry.extra_headers, entry.auth_header: value}


def secret_env_for(entry: CatalogEntry, config: dict) -> dict[str, str]:
    """Container env vars carrying this connector's configured field values."""
    env: dict[str, str] = {}
    for auth_field in entry.auth_fields:
        value = config.get(auth_field.key)
        if value:
            env[env_var(entry.id, auth_field.key)] = str(value)
    return env
