# Connectors

Forge wires MCP (Model Context Protocol) servers into every coding session. Two tiers share
one framework: **core** connectors ship with Forge (web fetch, search, browser, skills,
GitHub), and **integrations** cover popular third-party services. Everything is managed from
the **Connectors** page; the agent sees an enabled connector as a set of extra tools.

All remote URLs and auth notes below were verified against the live endpoints and each
vendor's public documentation on 2026-08-19. Local servers are pinned to exact package
versions so sessions are reproducible.

## Catalog

### Core (ship with Forge)

| Connector | Category | Type | Auth needed | Docs |
|---|---|---|---|---|
| GitHub | core | local | Personal access token (`ghp_…` or fine-grained) | [github-mcp-server](https://github.com/github/github-mcp-server) |
| Fetch | core | local | none | [mcp-server-fetch](https://pypi.org/project/mcp-server-fetch/) |
| Web search | core | local | none (uses your SearXNG) | [mcp-searxng](https://pypi.org/project/mcp-searxng/) |
| Browser | core | remote (LAN) | none | [playwright-mcp](https://github.com/microsoft/playwright-mcp) |
| Skills | core | local | none | — |

### Integrations

| Connector | Category | Type | Auth needed | Docs |
|---|---|---|---|---|
| Notion | productivity | local | Internal integration secret (`ntn_…`) | [notion-mcp-server](https://github.com/makenotion/notion-mcp-server) |
| Linear | productivity | remote | API key (`lin_api_…`) or OAuth token | [Linear MCP](https://linear.app/docs/mcp) |
| Asana | productivity | remote | **OAuth-only** | [Asana MCP](https://developers.asana.com/docs/using-asanas-mcp-server) |
| Atlassian | productivity | remote | OAuth token (API-token option documented) | [Atlassian MCP](https://support.atlassian.com/rovo/docs/getting-started-with-the-atlassian-remote-mcp-server/) |
| monday.com | productivity | remote | Personal API token or OAuth | [monday MCP](https://developer.monday.com/api-reference/docs/integrate-with-monday-mcp) |
| Todoist | productivity | remote | API token (or OAuth) | [todoist-mcp](https://github.com/Doist/todoist-mcp) |
| ClickUp | productivity | remote | **OAuth-only** (vetted clients, beta) | [ClickUp MCP](https://developer.clickup.com/docs/connect-an-ai-assistant-to-clickups-mcp-server) |
| Box | productivity | remote | **OAuth-only** | [Box MCP](https://developer.box.com/guides/box-mcp/remote/) |
| Slack | productivity | local | User (`xoxp-…`) or bot (`xoxb-…`) token | [slack-mcp-server](https://github.com/korotovsky/slack-mcp-server) |
| Airtable | productivity | local | Personal access token (`pat…`) | [airtable-mcp-server](https://github.com/domdomegg/airtable-mcp-server) |
| Gmail | productivity | local | OAuth client ID + secret + refresh token (*experimental*) | [gmail-mcp](https://github.com/shinzo-labs/gmail-mcp) |
| Discord | productivity | local | Bot token | [mcp-discord](https://github.com/barryyip0625/mcp-discord) |
| Zapier | productivity | remote | Per-account MCP URL (embeds credentials) | [Zapier MCP](https://zapier.com/mcp) |
| Sentry | developer | remote | **OAuth-only** | [Sentry MCP](https://mcp.sentry.dev/) |
| Vercel | developer | remote | **OAuth-only** (vetted clients) | [Vercel MCP](https://vercel.com/docs/agent-resources/vercel-mcp) |
| Netlify | developer | local | Personal access token (`nfp_…`) | [Netlify MCP](https://docs.netlify.com/build/build-with-ai/netlify-mcp-server/) |
| Supabase | developer | remote | Personal access token (`sbp_…`) or OAuth | [Supabase MCP](https://supabase.com/docs/guides/getting-started/mcp) |
| Cloudflare docs | developer | remote | none | [mcp-server-cloudflare](https://github.com/cloudflare/mcp-server-cloudflare) |
| Hugging Face | developer | remote | optional HF token (`hf_…`) | [HF MCP](https://huggingface.co/settings/mcp) |
| Figma | design | remote | **OAuth-only** | [Figma MCP](https://developers.figma.com/docs/figma-mcp-server/) |
| Canva | design | remote | **OAuth-only** | [Canva MCP setup](https://www.canva.com/help/mcp-agent-setup/) |
| Higgsfield | design | remote | **OAuth-only** (account sign-in, uses plan credits) | [Higgsfield MCP](https://higgsfield.ai/mcp) |
| Stripe | business | remote | Restricted API key (`rk_…`) as bearer | [Stripe MCP](https://docs.stripe.com/mcp) |
| Intercom | business | remote | Access token or OAuth (EU: `mcp.eu.intercom.com`) | [Intercom MCP](https://developers.intercom.com/docs/guides/mcp) |
| HubSpot | business | local | Private app token (`pat-na1-…`) | [HubSpot MCP](https://developers.hubspot.com/mcp) |
| PayPal | business | remote | OAuth token from client credentials | [PayPal MCP](https://developer.paypal.com/tools/mcp-server/) |
| Square | business | remote | **OAuth-only** (documented flow) | [Square MCP](https://developer.squareup.com/docs/mcp) |
| Plaid | business | remote | OAuth client-credentials token (~15 min expiry) | [Plaid MCP](https://plaid.com/docs/resources/mcp/) |

**Type** legend — *remote*: the session talks to a vendor-hosted HTTPS endpoint;
*local*: a pinned stdio server (npx / uvx / binary) runs inside the session container.

## Enabling a connector

1. Open **Connectors** in the Forge UI.
2. Fill in the connector's auth fields (if any) and save — secrets show as `••••••` once
   stored; leaving the mask untouched keeps the stored value, clearing a field deletes it.
3. Flip the toggle on. New sessions pick the connector up immediately; sessions already
   running keep their original config until restarted.
4. Toggling a connector off removes its tools *and* its secrets from the next session —
   the switch actually cuts access, it does not just hide the card.

## Getting a token, per service

**Plain API keys / PATs (paste and go)** — these vendors accept a long-lived key directly:

- **GitHub** — Settings → Developer settings → tokens. Also enables `git push` in sessions.
- **Linear** — Settings → Security & access → API keys (`lin_api_…`).
- **monday.com** — profile → Developers → My access tokens.
- **Todoist** — Settings → Integrations → Developer → API token.
- **Notion** — [notion.so/profile/integrations](https://www.notion.so/profile/integrations):
  create an internal integration, copy the secret (`ntn_…`), and *share the target pages
  with the integration* — an unshared page is invisible to it.
- **Slack** — create a Slack app, install it to your workspace, and copy the bot token
  (`xoxb-…`) or a user token (`xoxp-…`). User tokens can search; bots must be invited to
  each channel. Posting is disabled by default — set the third field to `true` (anywhere)
  or a comma-separated list of channel IDs to allow it.
- **Airtable** — [airtable.com/create/tokens](https://airtable.com/create/tokens), scope it
  to the bases you need.
- **Netlify** — User settings → OAuth → New access token (`nfp_…`).
- **Supabase** — [Account → Access tokens](https://supabase.com/dashboard/account/tokens)
  (`sbp_…`); Supabase documents this bearer path for CI clients.
- **HubSpot** — Settings → Integrations → Private apps → create app → copy `pat-na1-…`.
- **Stripe** — create a **restricted** API key (`rk_…`) with only the permissions the agent
  needs; Stripe explicitly recommends this for agents. Never paste your full secret key.
- **Intercom** — Developer Hub → your app → Authentication → access token.
- **Hugging Face** — optional; only needed for private/gated repos.
- **Discord** — [Developer Portal](https://discord.com/developers/applications) → Bot →
  token, then invite the bot to your server.
- **Zapier** — [mcp.zapier.com](https://mcp.zapier.com) issues you a personal endpoint URL
  with credentials embedded. Treat the URL itself as a secret.

**OAuth-only services (honest limitations)** — Asana, ClickUp, Box, Sentry, Vercel, Figma,
Canva, Higgsfield, and Square only authenticate their hosted MCP endpoints through an
interactive OAuth browser flow (some additionally restrict which MCP clients may connect).
Forge sends whatever you paste as `Authorization: Bearer …`, so these connectors only work
if you can obtain an OAuth *access token* out of band (e.g. from a vendor app you
registered, or a token minted by another MCP client). There is no plain API-key path; the
connector card says so. PayPal and Plaid sit in between: you mint an OAuth access token
yourself from your app's client ID + secret (Plaid tokens expire after ~15 minutes, which
makes that connector impractical for long sessions). Atlassian is OAuth-first but documents
an API-token option. Higgsfield's MCP (`mcp.higgsfield.ai/mcp`) signs in with your
Higgsfield *account* — generations spend your plan credits, and its API keys
(cloud.higgsfield.ai, a separate developer product) do **not** work against the hosted MCP.
If you need headless key-based auth instead, the community stdio server
[`higgsfield-mcp`](https://github.com/Storyvord/higgsfield-mcp) takes Cloud API keys via
env vars and can be added as a custom connector.

**Gmail (experimental)** — Google offers no public remote MCP, so Forge ships a pinned
community server. One-time setup on your own machine: create a Google Cloud project,
enable the Gmail API, create an OAuth *Desktop* client, then run
`npx @shinzolabs/gmail-mcp auth` to complete the browser consent and mint a refresh token.
Paste the client ID, client secret, and refresh token into the connector card. Rotating or
revoking the Google credential kills access instantly. Google Calendar / Drive are not in
the catalog yet: the leading open-source servers need an interactive credentials file
inside the container, which does not survive Forge's headless env-var model.

## Custom connectors

**Connectors → Add custom** registers any other MCP server:

- **Remote**: name + `https://` URL, plus optional headers (e.g.
  `Authorization: Bearer <token>`).
- **Local**: name + command array (e.g. `["npx", "-y", "some-mcp-server@1.2.3"]`), plus
  optional environment variables. The command runs inside the session container, which
  provides `node` 22 (`npx`), `python3`, and `uvx` — pin exact versions.

> **Warning:** unlike catalog connectors, custom headers and environment values are stored
> and rendered **verbatim** into the session's `opencode.json` — they do not go through the
> `FORGE_CONN_*` env-var indirection. Anything that can read a session's config (or the
> agent itself, if it inspects its own container) can see them in plain text. Prefer a
> catalog entry when one exists, and give custom connectors least-privilege tokens.

Custom connectors can be deleted from their card; catalog connectors can only be disabled.

## Security notes

Per Forge's v1 LAN threat model (PLAN §7):

- **Storage**: connector secrets live in Forge's SQLite database on the orchestrator
  volume, alongside the GitHub PAT and HF token. They are never baked into images and
  never logged, but anyone with filesystem access to the DB can read them — protect the
  host.
- **Delivery**: for catalog connectors, secrets travel to session containers as
  `FORGE_CONN_<ID>_<FIELD>` environment variables and are referenced from the rendered
  `opencode.json` via `{env:…}` indirection — the config file itself contains no secret
  values. Custom-connector headers/env are the exception (see warning above).
- **Exposure to the agent**: the coding agent runs inside the session container, so any
  enabled connector's token is *by design* usable by the agent — and readable by code the
  agent runs. Scope tokens minimally (restricted Stripe keys, read-only PATs, single-base
  Airtable scopes) and enable write-capable connectors (Slack posting, payments) only when
  a task needs them.
- **Network**: session containers have outbound internet; remote connectors are reached
  directly over HTTPS from inside the session. The API is gated by Forge's single-password
  bearer auth — fine for a single-user LAN, not for hostile multi-tenant networks.
- **Kill switch**: disabling a connector removes both its MCP block and its env vars from
  subsequently spawned sessions.
