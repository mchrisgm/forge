# Forge

**Self-hosted agentic coding, on your own GPU, from any device on your LAN.**

Forge gives you Claude Code-style coding sessions — an agent that reads, writes,
runs, and commits code in a sandboxed workspace — powered entirely by
open-weight models running on local hardware. There are **no external LLM
providers anywhere**: no API keys, no per-token bills, no code leaving your
network. The only outbound traffic is model downloads from Hugging Face, git
operations, and whatever your agent's own tools fetch. A weekly registry job
watches Hugging Face for new models that fit your hardware and files
suggestions; nothing is downloaded without your approval.

Under the hood, a FastAPI **orchestrator** is the brain: it enforces a
single-GPU lease across three inference lanes (llama.cpp, vLLM, AirLLM), spawns
one sandboxed [OpenCode](https://opencode.ai) container per coding session over
the host Docker socket, streams everything to a custom React **PWA** you can
install on your phone, and wires MCP connectors (GitHub, web fetch, SearXNG
search, Playwright browser automation) plus Claude Code-format **skills** into
every session.

```
                            phone / laptop on LAN
                                    │  http://<host>:8080
                            ┌───────▼────────┐
                            │  gateway       │  Caddy: serves PWA,
                            │  (caddy :8080) │  proxies /api/* ↓
                            └───────┬────────┘
                                    │
                     ┌──────────────▼───────────────┐
                     │  orchestrator (FastAPI)      │
                     │  auth · engine lease · tasks │
                     │  registry · downloads · SSE  │
                     └──┬───────────┬────────────┬──┘
              docker sock│           │            │
        starts ONE of:   │           │ spawns     │ MCP / search
   ┌─────────────────────▼──┐   ┌────▼────────┐  ┌▼──────────────┐
   │ llama.cpp :8081  (GPU) │   │ session-<id>│  │ searxng       │
   │ vLLM      :8082  (GPU) │   │ OpenCode    │  │ mcp-playwright│
   │ AirLLM    :8083  (GPU) │◄──┤ :4096       │  └───────────────┘
   └────────────────────────┘   │ /workspace  │
     OpenAI-compatible /v1      │ /skills:ro  │   × N parallel sessions
                                └─────────────┘
```

The PWA never talks to session containers directly — the orchestrator proxies
every OpenCode call and streams events over SSE, so one bearer token guards
everything.

## Hardware target

Built for a single-GPU workstation; the reference box is:

- **GPU:** RTX 4070 Ti Super, 12 GB VRAM (budgets assume ~11 GB usable)
- **RAM:** 48 GB (up to 32 GB used for CPU-offloaded model layers)
- **Disk:** NVMe, tens of GB free for model weights
- **OS:** Linux with Docker Engine + [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)

Different hardware? Adjust `FORGE_VRAM_BUDGET_GB` and
`FORGE_RAM_OFFLOAD_BUDGET_GB` in `.env` — the fit rules, registry scoring, and
`--n-gpu-layers` computation all read those budgets.

## Quick start

```bash
git clone <your-forge-repo> forge && cd forge
cp .env.example .env
# edit .env: set FORGE_PASSWORD, and FORGE_SECRET_KEY (openssl rand -hex 32)
make up
```

Then open `http://<host-ip>:8080` from any device on your LAN and log in with
`FORGE_PASSWORD`. On your phone, use the browser's **"Add to Home Screen" /
"Install app"** — the UI is an installable PWA with a mobile-first layout.

`make help` lists all targets (`up`, `down`, `logs`, `seed`, `smoke`, `test`,
`dev`, `clean`).

## First run

1. **Seed the model catalog** — `make seed` inserts a curated,
   hardware-verified starter catalog (see `scripts/seed_models.py`): Qwen2.5
   Coder 14B (AWQ + GGUF), Qwen3 Coder 30B-A3B MoE (the expected daily
   driver), gpt-oss-20b, and a 7B utility model. Entries arrive as
   `approved` — nothing is downloaded yet.
2. **Download a model** — Models page → pick one → **Download**. Progress
   streams live. Start with the 7B if you want a quick end-to-end check.
3. **Load it** — Models page → **Load**. The orchestrator starts the right
   engine container with computed flags and holds the single GPU lease; the
   VRAM gauge and lease state update live. Big GGUFs can take minutes.
4. **Create a session** — Sessions page → **New session**, pick the loaded
   model, optionally paste a `repo_url` to clone. Then chat: ask it to build
   something and watch the streamed tool calls, edit files in Files, and
   commit from Git.

Fire-and-forget prompts go through **tasks** (`POST
/api/sessions/{id}/tasks`) — run several sessions in parallel and track them
in the parallel-runs view.

## Engine lanes

Three inference lanes sit behind the same OpenAI-compatible surface; the
orchestrator enforces that only **one** GPU engine runs at a time (loading a
second returns HTTP 409 with the current lease holder). From PLAN §9:

| Lane | What fits (approx) | Example |
|---|---|---|
| vLLM (11 GB VRAM) | ≤ 15B dense @ 4-bit AWQ, 16k ctx | Qwen coder 14B AWQ |
| llama.cpp full-GPU | GGUF ≤ ~10 GB file | 14B Q4_K_M |
| llama.cpp offload | GGUF ≤ ~40 GB file (VRAM + 32 GB RAM), MoE strongly preferred | 30B-A3B class Q4/Q5 |
| AirLLM | ≤ 70B fp16-from-disk, **chat-only** | 70B instruct |

Notes:

- **llama.cpp** is the default lane. `--n-gpu-layers` is computed per model to
  fill VRAM; the rest offloads to RAM. `--parallel 2` slots let two sessions
  share one server (each slot splits the context budget).
- **vLLM** is the fast lane for ≤14B AWQ models, with
  `--enable-auto-tool-choice` and a per-model tool-call parser.
- **AirLLM** is the slow lane for over-VRAM models: seconds-to-minutes per
  token, no tool calling, chat only — it never appears in the session model
  picker. Talk to it (or any loaded model) via **Chat with model** on the
  Models page once its lease is ready.

## Adding models

**Manually:** Models page → **Add model** (or `POST /api/models`) with the
Hugging Face repo id, engine lane, and — for llama.cpp — the exact `.gguf`
filename from the repo (e.g. `Qwen2.5-Coder-14B-Instruct-Q4_K_M.gguf`; the
file must live at the repo root, single-file quants only). Set
`tool_call_format` (`hermes` for Qwen-family, `llama3` for Llama-family,
`none` to disable tools).

**Via registry suggestions:** a weekly scheduled scan (cron in
`FORGE_REGISTRY_CRON`, trigger manually with `POST /api/models/registry/scan`)
ranks trending Hugging Face text-generation models by trend, recency, coding
signal, and hardware fit. Anything promising lands in the **suggestion inbox**
on the Models page with a score breakdown. Approve → it downloads and becomes
loadable; dismiss → never suggested again. Suggest-only by design: no weights
are fetched without your click.

## Skills (Claude Code format)

Forge installs and serves skills in the [Claude Code / Agent
Skills](https://github.com/anthropics/skills) layout: a directory with a
`SKILL.md` whose YAML frontmatter has `name:` and `description:`, plus any
support files.

- **Install:** Skills page → install from a git URL (optionally a subdir), or
  `POST /api/skills/install {"git_url": "...", "subdir": "..."}`. Community
  repos following the anthropics/skills layout work as-is.
- **Use:** every session container gets a read-only `/skills` mount and a
  built-in skills MCP server exposing `list_skills` / `load_skill`. Models see
  only names and descriptions until a skill is loaded — the same progressive
  disclosure Claude Code uses.

## Connectors (MCP)

Sessions come wired with MCP servers (toggle per connector on the Connectors
page):

| Connector | What it does | Needs |
|---|---|---|
| `github` | Repo/issue/PR operations via github-mcp-server | A GitHub PAT (Connectors page). Off until one is set. |
| `fetch` | Fetch/convert web pages | — |
| `searxng` | Web search via the bundled self-hosted SearXNG | — |
| `playwright` | Browser automation (headless, shared service) | — |
| `skills` | The skills server described above | — |

The GitHub PAT is stored in SQLite and injected into session containers as an
environment variable — see the security model below before pasting a
broadly-scoped token.

## Security model (v1 — LAN threat model)

Forge v1 assumes a **single trusted user on a trusted LAN**. Concretely:

- **Auth:** one password → bearer token (argon2 hash at rest). Every `/api`
  route is gated except `/api/health` and `/api/auth/login`. No TLS in v1 —
  the gateway listens on plain HTTP :8080.
- **Docker socket:** mounted into the orchestrator **only**. Session
  containers are spawned as siblings and never get the socket, never get the
  GPU, run as non-root with memory/CPU/pid limits.
- **The caveat that matters:** whoever compromises the orchestrator
  effectively has host root via the socket. Accepted for single-user LAN v1;
  sysbox/rootless Docker are the documented hardening path.
- **Secrets:** GitHub PAT and HF token live in SQLite + `.env`. They are never
  baked into images and never logged. LAN-only threat model — treat the box
  accordingly.
- **Agent code execution:** session containers have outbound internet (git,
  pip, npm need it). Code your agent writes and runs is sandboxed only by
  container limits — don't point Forge at repos you wouldn't run locally.

**Do not port-forward :8080 to the internet.**

### Future VPN slot

Remote access is deliberately out of v1, but the compose topology reserves the
slot: add a WireGuard/Tailscale sidecar on the `forge-edge` network and enable
TLS in `gateway/Caddyfile` (a commented block is already there). No other
service needs to change.

## Troubleshooting

- **Engine fails to load / VRAM OOM** — the healthwait treats an engine
  container exit as a failed load: the lease auto-releases and the engine's
  log tail surfaces in the UI (Models/System) and in `GET /api/engines` under
  `lease.error`. Try a smaller quant, a smaller context, or lower
  `FORGE_VRAM_BUDGET_GB`.
- **Session was reaped / "container unreachable"** — idle sessions are stopped
  after `FORGE_SESSION_IDLE_MIN` (default 120) to free resources. Restart it:
  Sessions page → **Start** (or `POST /api/sessions/{id}/start`). The
  workspace volume persists, and OpenCode reloads its session state from it.
- **`make smoke` says "no ready model"** — run `make seed`, download a model
  (UI or `POST /api/models/{id}/download`), and re-run. `SMOKE_SKIP_MODEL=1
  ./scripts/smoke.sh` accepts an infra-only pass.
- **Tool calls are flaky** — open the model's detail view: each catalog entry
  records a `tool_call_format` and a tool-reliability note. Prefer the seeded
  Qwen coder models for agentic work; gpt-oss-20b is seeded chat-only.
- **Everything on fire** — `make logs` tails all compose services; engine and
  session containers are visible via `docker ps` (`forge-engine-*`,
  `forge-session-*`).

## Development

```bash
make dev     # compose dev overrides: orchestrator --reload, Vite HMR on :5173
make test    # orchestrator pytest + UI typecheck
make lint    # ruff + tsc
make smoke   # full end-to-end gate (needs GPU + a downloaded model)
```

Layout: `orchestrator/` (FastAPI + SQLModel + APScheduler + docker-py),
`ui/` (React 18 + TS + Vite PWA), `session-runner/` (OpenCode session image),
`engines/` (llama.cpp/vLLM wrappers + AirLLM server), `mcp/skills-server/`
(skills MCP), `gateway/` (Caddy), `scripts/` (smoke + seed). `PLAN.md` is the
authoritative spec. CI (GitHub Actions) runs ruff, pytest, and the UI
typecheck/build — no GPU steps; GPU paths are covered by `make smoke` on the
real host.

## Pinned versions

Exact versions of OpenCode and the MCP packages inside session containers are
pinned (and checksum-verified where applicable) in
[`session-runner/Dockerfile`](session-runner/Dockerfile) — that file's header
is the single source of truth recording the M5 research: `opencode-ai`,
`github-mcp-server`, `uv`/`uvx`, `mcp-server-fetch`, and `mcp-searxng`. Engine
images (`vllm/vllm-openai`, `mcr.microsoft.com/playwright/mcp`, `searxng`) are
pinned in `docker-compose.yml` / `.env.example`. Bump deliberately: OpenCode
API drift is the main integration risk (PLAN §14), and
`orchestrator/app/services/opencode_client.py` is the single integration
point to re-verify after an upgrade.

## License

See [LICENSE](LICENSE).
