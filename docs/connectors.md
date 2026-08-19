# Connectors

Forge wires MCP (Model Context Protocol) servers into every coding session. Two tiers share
one framework: **core** connectors ship with Forge (web fetch, search, browser, skills,
GitHub), and **integrations** cover every officially hosted, publicly reachable remote MCP
server we could verify — plus a handful of official local servers where a vendor offers no
key-friendly public endpoint. Everything is managed from the **Connectors** page; the agent
sees an enabled connector as a set of extra tools.

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
| Make | productivity | remote | Per-account MCP URL (embeds MCP token) | [Make MCP](https://developers.make.com/mcp-server) |
| Fireflies | productivity | remote | API key as bearer (or OAuth) | [Fireflies MCP](https://docs.fireflies.ai/getting-started/mcp-configuration) |
| Dart | productivity | remote | Auth token (`dsa_…`) as bearer | [Dart MCP](https://help.dartai.com/en/articles/10733406-dart-mcp) |
| Miro | productivity | remote | **OAuth-only** (team picked at consent) | [Miro MCP](https://developers.miro.com/docs/miro-mcp) |
| Sentry | developer | remote | **OAuth-only** | [Sentry MCP](https://mcp.sentry.dev/) |
| Vercel | developer | remote | **OAuth-only** (vetted clients) | [Vercel MCP](https://vercel.com/docs/agent-resources/vercel-mcp) |
| Netlify | developer | local | Personal access token (`nfp_…`) | [Netlify MCP](https://docs.netlify.com/build/build-with-ai/netlify-mcp-server/) |
| Supabase | developer | remote | Personal access token (`sbp_…`) or OAuth | [Supabase MCP](https://supabase.com/docs/guides/getting-started/mcp) |
| Cloudflare docs | developer | remote | none | [mcp-server-cloudflare](https://github.com/cloudflare/mcp-server-cloudflare) |
| Hugging Face | developer | remote | optional HF token (`hf_…`) | [HF MCP](https://huggingface.co/settings/mcp) |
| GitHub (remote) | developer | remote | PAT as bearer (or OAuth) | [github-mcp-server](https://github.com/github/github-mcp-server) |
| GitLab | developer | remote | **OAuth-only** (PAT support still open) | [GitLab MCP](https://docs.gitlab.com/user/model_context_protocol/mcp_server/) |
| DeepWiki | developer | remote | none | [DeepWiki MCP](https://docs.devin.ai/work-with-devin/deepwiki-mcp) |
| Context7 | developer | remote | optional key via `CONTEXT7_API_KEY` header | [context7](https://github.com/upstash/context7) |
| Microsoft Learn | developer | remote | none | [MicrosoftDocs/mcp](https://github.com/MicrosoftDocs/mcp) |
| AWS Knowledge | developer | remote | none (rate-limited) | [AWS Knowledge MCP](https://awslabs.github.io/mcp/servers/aws-knowledge-mcp-server/) |
| Astro docs | developer | remote | none | [withastro/docs-mcp](https://github.com/withastro/docs-mcp) |
| Twilio docs | developer | remote | none (docs search only, beta) | [Twilio MCP](https://www.twilio.com/docs/ai/mcp) |
| Cloudflare Workers | developer | remote | **OAuth-only** | [mcp-server-cloudflare](https://github.com/cloudflare/mcp-server-cloudflare) |
| Cloudflare observability | developer | remote | **OAuth-only** | [mcp-server-cloudflare](https://github.com/cloudflare/mcp-server-cloudflare) |
| Cloudflare Radar | developer | remote | **OAuth-only** (free account works) | [mcp-server-cloudflare](https://github.com/cloudflare/mcp-server-cloudflare) |
| Cloudflare browser rendering | developer | remote | **OAuth-only** | [mcp-server-cloudflare](https://github.com/cloudflare/mcp-server-cloudflare) |
| Neon | developer | remote | API key (`napi_…`) as bearer (or OAuth) | [Neon MCP](https://neon.com/docs/ai/neon-mcp-server) |
| Prisma Postgres | developer | remote | **OAuth-only** (Prisma Console) | [Prisma MCP](https://www.prisma.io/docs/postgres/integrations/mcp-server) |
| Render | developer | remote | API key (`rnd_…`) as bearer (or OAuth) | [Render MCP](https://render.com/docs/mcp-server) |
| Heroku | developer | remote | **OAuth-only** | [Heroku remote MCP](https://devcenter.heroku.com/articles/heroku-remote-mcp-server) |
| Buildkite | developer | remote | API access token (`bkua_…`) as bearer (`/direct`) | [Buildkite MCP](https://buildkite.com/docs/apis/mcp-server) |
| CircleCI | developer | remote | **OAuth-only** | [CircleCI MCP](https://circleci.com/docs/guides/toolkit/circleci-mcp-overview/) |
| Semgrep | developer | remote | **OAuth-only** (sign-in newly required) | [semgrep/mcp](https://github.com/semgrep/mcp) |
| Exa | developer | remote | optional API key as bearer | [Exa MCP](https://docs.exa.ai/reference/exa-mcp) |
| Tavily | developer | remote | API key (`tvly-…`) as bearer (or OAuth) | [Tavily MCP](https://docs.tavily.com/documentation/mcp) |
| Firecrawl | developer | remote | Per-account MCP URL (key in path) | [Firecrawl MCP](https://docs.firecrawl.dev/mcp-server) |
| Apify | developer | remote | API token (`apify_api_…`) as bearer | [Apify MCP](https://docs.apify.com/platform/integrations/mcp) |
| Browserbase | developer | remote | Per-account MCP URL (key in query) | [Browserbase MCP](https://docs.browserbase.com/integrations/mcp/setup) |
| Globalping | developer | remote | API token as bearer (or OAuth) | [globalping-mcp-server](https://github.com/jsdelivr/globalping-mcp-server) |
| Grafana Cloud | developer | remote | **OAuth-only** (hosted) | [Grafana Cloud MCP](https://grafana.com/docs/grafana-cloud/ai-tools/mcp-servers/cloud-mcp/) |
| Honeycomb | developer | remote | Management API key as bearer (Enterprise; or OAuth) | [Honeycomb MCP](https://docs.honeycomb.io/integrations/mcp/configuration-guide) |
| Postman | developer | remote | API key (`PMAK-…`) as bearer (or OAuth) | [Postman MCP](https://learning.postman.com/docs/reference/postman-api/postman-mcp-server/postman-mcp-remote-server) |
| Sanity | developer | remote | API token as bearer (or OAuth) | [Sanity MCP](https://www.sanity.io/docs/ai/mcp-server) |
| Replicate | developer | remote | **OAuth-ish** (web flow stores your `r8_…` token) | [Replicate MCP](https://replicate.com/docs/reference/mcp) |
| Vapi | developer | remote | API key as bearer | [Vapi MCP](https://docs.vapi.ai/sdk/mcp-server) |
| Bright Data | developer | remote | Per-account MCP URL (token in query) | [brightdata-mcp](https://github.com/brightdata/brightdata-mcp) |
| Pinecone Assistant | developer | remote | Per-assistant URL + API key as bearer | [Pinecone Assistant MCP](https://docs.pinecone.io/guides/assistant/mcp-server) |
| Algolia | developer | remote | **OAuth-only** (read-only tools) | [Algolia MCP](https://www.algolia.com/doc/guides/model-context-protocol/productivity-mcp) |
| Stytch | developer | remote | **OAuth-only** | [Stytch MCP](https://stytch.com/docs/workspace-management/stytch-mcp) |
| Jam | developer | remote | **OAuth-only** | [Jam MCP](https://jam.dev/docs/jam-mcp) |
| MongoDB | developer | local | Connection string (+ optional read-only flag) | [mongodb-mcp-server](https://github.com/mongodb-js/mongodb-mcp-server) |
| Redis | developer | local | Host / port / username / password | [mcp-redis](https://github.com/redis/mcp-redis) |
| Shopify dev docs | developer | local | none | [Shopify/dev-mcp](https://github.com/Shopify/dev-mcp) |
| Figma | design | remote | **OAuth-only** | [Figma MCP](https://developers.figma.com/docs/figma-mcp-server/) |
| Canva | design | remote | **OAuth-only** | [Canva MCP setup](https://www.canva.com/help/mcp-agent-setup/) |
| Higgsfield | design | remote | **OAuth-only** (account sign-in, uses plan credits) | [Higgsfield MCP](https://higgsfield.ai/mcp) |
| Webflow | design | remote | **OAuth-only** (site owners/admins) | [Webflow MCP](https://developers.webflow.com/mcp/reference/how-it-works) |
| Wix | design | remote | **OAuth** (API-key path needs extra header) | [Wix MCP](https://dev.wix.com/docs/sdk/articles/use-the-wix-mcp/about-the-wix-mcp) |
| HeyGen | design | remote | **OAuth-only** (uses plan credits) | [HeyGen MCP](https://developers.heygen.com/mcp/overview) |
| Cloudinary | design | remote | **OAuth** (API-key path needs 3 headers) | [cloudinary/mcp-servers](https://github.com/cloudinary/mcp-servers) |
| ElevenLabs | design | local | API key (`sk_…`) | [elevenlabs-mcp](https://github.com/elevenlabs/elevenlabs-mcp) |
| Stripe | business | remote | Restricted API key (`rk_…`) as bearer | [Stripe MCP](https://docs.stripe.com/mcp) |
| Intercom | business | remote | Access token or OAuth (EU: `mcp.eu.intercom.com`) | [Intercom MCP](https://developers.intercom.com/docs/guides/mcp) |
| HubSpot | business | local | Private app token (`pat-na1-…`) | [HubSpot MCP](https://developers.hubspot.com/mcp) |
| PayPal | business | remote | OAuth token from client credentials | [PayPal MCP](https://developer.paypal.com/tools/mcp-server/) |
| Square | business | remote | **OAuth-only** (documented flow) | [Square MCP](https://developer.squareup.com/docs/mcp) |
| Plaid | business | remote | OAuth client-credentials token (~15 min expiry) | [Plaid MCP](https://plaid.com/docs/resources/mcp/) |
| Attio | business | remote | **OAuth-only** | [Attio MCP](https://docs.attio.com/mcp/overview) |
| Close | business | remote | API key via `Close-API-Key` header (scope pinned read-only) | [Close MCP](https://developer.close.com/mcp) |
| Paddle | business | remote | Live API key (`pdl_live_…`) as bearer (or OAuth) | [Paddle MCP](https://developer.paddle.com/sdks/ai/paddle-mcp) |
| PagerDuty | business | remote | OAuth token as bearer (`Token token=…` needs custom) | [PagerDuty MCP](https://support.pagerduty.com/main/docs/pagerduty-mcp-server) |
| CoinGecko | business | remote | none (keyless public beta) | [CoinGecko MCP](https://docs.coingecko.com/docs/mcp-server) |

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

**No auth needed (flip on and go)** — public servers with nothing to configure:
Cloudflare docs, DeepWiki, Microsoft Learn, AWS Knowledge, Astro docs, Twilio docs,
Shopify dev docs (local), CoinGecko (keyless public beta; the Pro host
`mcp.pro-api.coingecko.com` takes a paid key via a custom connector), and Hugging Face /
Exa / Context7, which work anonymously with rate limits and accept an optional key.

**Plain API keys / PATs (paste and go)** — these vendors accept a long-lived key directly:

- **GitHub** — Settings → Developer settings → tokens. Also enables `git push` in sessions.
  The same PAT works as a bearer on **GitHub (remote)** (`api.githubcopilot.com/mcp/`),
  where tool visibility follows the token's scopes.
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
- **Fireflies** — app.fireflies.ai → Integrations → Fireflies API → API key.
- **Dart** — app.dartai.com → Settings → Account → Authentication token (`dsa_…`).
- **Neon** — console.neon.tech → Account settings → API keys (`napi_…`).
- **Render** — Account settings → API keys (`rnd_…`).
- **Buildkite** — [buildkite.com/user/api-access-tokens](https://buildkite.com/user/api-access-tokens)
  (`bkua_…`); Forge targets the token pass-through `/direct` endpoint, so token scopes gate
  the tools.
- **Tavily** — app.tavily.com → API keys (`tvly-…`).
- **Exa** — optional; dashboard.exa.ai → API keys for higher limits and all tools.
- **Context7** — optional; a free key from context7.com/dashboard raises rate limits (sent
  as a `CONTEXT7_API_KEY` header, not a bearer).
- **Apify** — console.apify.com → Settings → API & integrations (`apify_api_…`). Actor runs
  bill your Apify account.
- **Globalping** — dash.globalping.io → Tokens (the hosted endpoint rejects anonymous
  sessions).
- **Honeycomb** — Enterprise feature: a Management API key pair (`<id>:<secret>`) with the
  *Model Context Protocol* and *Environments* scopes.
- **Postman** — Settings → API keys (`PMAK-…`). The `/minimal` and `/code` variants and the
  EU host (`mcp.eu.postman.com`) are available as custom connectors.
- **Sanity** — a robot token from sanity.io/manage (or `sanity debug --secrets`); tool
  access follows the token's role.
- **Vapi** — dashboard.vapi.ai → Org → API keys.
- **Paddle** — Paddle → Developer tools → Authentication (`pdl_live_…`). Sandbox keys only
  work against Paddle's sandbox MCP host — add it as a custom connector.
- **Close** — Settings → Developer → API keys, sent in a `Close-API-Key` header. Forge pins
  the mandatory `Close-Scope` header to `mcp.read` (read-only); for writes add a custom
  connector with `Close-Scope: mcp.write_safe` or `mcp.write_destructive`.
- **MongoDB** (local) — a connection string (`mongodb+srv://…`); set the read-only field to
  `true` to block writes.
- **Redis** (local) — host/port/username/password of any reachable Redis; TLS/Cluster need
  extra env vars (see the server's docs).
- **ElevenLabs** (local) — elevenlabs.io → Developers → API keys (`sk_…`); generation
  spends your plan credits.

**Per-account MCP URLs (the URL is the credential)** — these vendors embed the key in a
personal endpoint URL; paste the full URL and treat it as a secret:

- **Zapier** — [mcp.zapier.com](https://mcp.zapier.com) issues your endpoint.
- **Make** — Profile → API access → Add token (scope `mcp:use`), then
  `https://<zone>.make.com/mcp/u/<token>/sse`. (`mcp.make.com` is the OAuth alternative
  for interactive clients.)
- **Firecrawl** — `https://mcp.firecrawl.dev/<api-key>/v2/mcp` (key from
  firecrawl.dev/app → API keys).
- **Browserbase** — `https://mcp.browserbase.com/mcp?browserbaseApiKey=<key>`.
- **Bright Data** — `https://mcp.brightdata.com/mcp?token=<api-token>`; append `&pro=1`
  for the paid full tool set.
- **Pinecone Assistant** — the URL itself is not secret
  (`https://<assistant-host>/mcp/assistants/<assistant>`, host shown on the assistant's
  console page) and pairs with a Pinecone API key sent as bearer.

**OAuth-only services (honest limitations)** — Asana, ClickUp, Box, Sentry, Vercel, Figma,
Canva, Higgsfield, Square, Miro, GitLab, the four account-scoped Cloudflare servers
(Workers/bindings, observability, Radar, browser rendering), Prisma Postgres, Heroku,
CircleCI, Semgrep, Grafana Cloud, Algolia, Stytch, Jam, Webflow, HeyGen, Attio, and
Replicate only authenticate their hosted MCP endpoints through an interactive OAuth
browser flow (some additionally restrict which MCP clients may connect). Forge sends
whatever you paste as `Authorization: Bearer …`, so these connectors only work if you can
obtain an OAuth *access token* out of band (e.g. from a vendor app you registered, or a
token minted by another MCP client). There is no plain API-key path; the connector card
says so. PayPal and Plaid sit in between: you mint an OAuth access token yourself from
your app's client ID + secret (Plaid tokens expire after ~15 minutes, which makes that
connector impractical for long sessions). PagerDuty accepts OAuth bearer tokens (App OAuth
via client credentials works headlessly), but its *User API token* scheme
(`Authorization: Token token=…`) needs a custom connector. Atlassian is OAuth-first but
documents an API-token option. Wix and Cloudinary do have API-key paths, but they require
extra vendor-specific headers (`wix-account-id`; three `CLOUDINARY_*` headers) — use a
custom connector for those. Higgsfield's MCP (`mcp.higgsfield.ai/mcp`) signs in with your
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
