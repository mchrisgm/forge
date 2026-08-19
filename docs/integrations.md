# Ecosystem integrations (wave 5)

Forge folds in several open-source projects rather than reinventing them. Each
was inspected before adoption; the choices below record *what* was integrated,
*how*, and *what was deliberately left out* so the trade-offs stay legible.

All of it respects Forge's constraints: LAN-only threat model, a single-GPU
budget, SQLite, docker-compose deployment, and everything self-hosted (no
external LLM providers, no paid APIs on the default path).

## Scrapling — stealth web fetching

[D4Vinci/Scrapling](https://github.com/D4Vinci/Scrapling) (BSD-3) ships a
first-party MCP server with TLS-fingerprint HTTP fetching, a Cloudflare-capable
stealth browser, and markdown extraction.

- **Service:** `mcp-scrapling` (official `ghcr.io/d4vinci/scrapling` image),
  streamable-HTTP MCP on the internal network — same pattern as
  `mcp-playwright`.
- **Connector:** a core catalog entry (`scrapling`), enabled by default. The
  older `mcp-server-fetch` connector (`fetch`) is kept but demoted to
  default-off, since Scrapling supersedes it on JS-heavy and protected pages.
- **Chat "read a page":** `POST /api/chat/read_page` fetches a URL server-side
  (fast `get`, auto-escalating to the stealth browser) and saves the result as
  a markdown attachment. Content is capped at 150 KB.
- **Agent skill:** the upstream Scrapling agent skill is vendored under
  `skills-bundled/scrapling/` (provenance + BSD-3 license retained) and seeded
  into the skills volume on first boot.
- **Kept mcp-playwright** for interactive automation (click/type/forms) —
  Scrapling reads pages, Playwright drives them.

## ECC — curated skills

[affaan-m/ECC](https://github.com/affaan-m/ECC) (MIT) is a large Claude
Code-format skill pack. Forge's installer already handles its repo+subdir
layout, so the integration is curation, not plumbing.

- **Suggested skills:** ~40 vetted entries (`orchestrator/app/skill_catalog.py`)
  as one-click installs, grouped by category. Skills that need paid external
  APIs (Exa, fal.ai, X) or the absent ECC runtime are excluded.
- **Skill-pack importer:** a generic scan/batch-install for *any* git repo of
  skills. Bulk-imported skills default to disabled so they don't flood the
  session tool listing.
- **Not adopted:** ECC's memory MCP, dashboards, llm-abstraction, and OpenCode
  subagent fan-out — they duplicate Forge's memory engine / model router /
  System page, or are too token-hungry on a single 12 GB GPU.

## Headroom — context compression

[headroomlabs-ai/headroom](https://github.com/headroomlabs-ai/headroom)
(Apache-2.0) compresses tool outputs, JSON, logs, and code before they reach
the model. On a 12 GB card, context is KV-cache VRAM, so this attacks the
scarcest resource.

- **Chained:** all chat-completion traffic flows `/v1` → `headroom` →
  `/v1-direct` → engines. `/v1-direct` is the loop-free upstream Headroom
  targets (a plain `/v1` upstream would recurse).
- **Softeners:** a runtime toggle (`Setting headroom_enabled`, on the Settings
  page) and an automatic health fallback — when the proxy doesn't answer, a
  cached probe routes callers straight to the engine, so a stopped Headroom
  container degrades to plain Forge instead of breaking every chat.
- **Offline:** `HEADROOM_OFFLINE=1` — the ML prose compressor stays off (no
  surprise model downloads); the JSON/log/code compressors run fully offline.
- **Not adopted:** Headroom's memory / `learn` / Qdrant+Neo4j stack — duplicates
  Forge's memory engine and breaks the SQLite/single-box ethos.
- **Caveat:** Headroom's accuracy benchmarks come from frontier models; on
  small quantized local models, validate before trusting it, hence the toggle.

## smolvm — microVM sandbox

[smol-machines/smolvm](https://github.com/smol-machines/smolvm) (Apache-2.0)
boots OCI images as hardware-isolated microVMs (KVM). It gives Forge a "run
this code safely" primitive it lacked.

- **Service:** built under `sandbox/smolvm/` from a pinned smolvm release,
  behind the opt-in `sandbox` compose profile (`make sandbox`); needs
  `/dev/kvm` on the host. Its control API has **no auth** and stays strictly on
  the internal network.
- **Exec surface:** `services/sandbox.py` runs code in ephemeral,
  network-**off** microVMs with a hard timeout and capped output. Code and
  stdin travel as env vars into a fixed argv bootstrap — never interpolated
  into a shell string. `POST /api/sandbox/run` (authed) powers the Chat **run**
  button on code blocks; `GET /api/sandbox/status` gates it.
- **Not adopted:** smolvm's GPU/CUDA remoting (contends with the one-engine-
  per-GPU lease and isn't a hardened boundary) and full VM-isolated coding
  sessions (deferred — docker sessions remain the default lane).

## MemStack — evaluated, not integrated

[cwinvestments/memstack](https://github.com/cwinvestments/memstack) was
reviewed and **not** integrated: its memory backend (LIKE-query SQLite, no
ranking/decay/extraction) is weaker than Forge's existing engine, its MCP
server and half its skills are closed-source/vendor-hosted, and its skill
licensing is contradictory. Its one good idea — cross-session *project* memory
for coding sessions — is noted for a future native build on Forge's own
SQLite+FTS5, not a dependency.

## Langflow / FastMCP — considered, deferred

[Langflow](https://github.com/langflow-ai/langflow) (visual flow builder) and
[FastMCP](https://github.com/PrefectHQ/fastmcp) (Forge-as-an-MCP-server) were
both researched and deliberately deferred this wave — Langflow duplicates
chat/auth as a heavy appliance, and the FastMCP "expose Forge over MCP" idea,
while attractive, was scoped out. Both remain clean future options.
