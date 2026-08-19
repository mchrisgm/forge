# Forge — Self-Hosted Agentic Coding Platform: Implementation Plan

> **For agentic workers:** Implement this plan milestone-by-milestone, task-by-task. Tasks use checkbox (`- [ ]`) syntax for tracking. Every milestone ends with acceptance criteria — do not proceed to the next milestone until they pass. Commit frequently with conventional-commit messages.

**Goal:** A fully dockerized, provider-free platform that gives the owner Claude Code-style agentic coding sessions — accessible from a phone on the LAN — powered entirely by open-weight models running on local hardware, with a self-updating model registry, MCP connectors, parallel sandboxed coding containers, and installable Claude Code-format skills.

**Architecture:** A FastAPI orchestrator is the brain: it manages inference-engine lifecycle on a single shared GPU, spawns per-session OpenCode containers over the host Docker socket, runs the model-suggestion scheduler, and exposes one API consumed by a custom React PWA. OpenCode (open source) is the agent engine inside each session container; llama.cpp, vLLM, and AirLLM are the three inference lanes behind an OpenAI-compatible interface.

**Tech stack:** Python 3.12 + FastAPI + SQLModel/SQLite + APScheduler + docker-py + huggingface_hub · React 18 + TypeScript + Vite (PWA) · Caddy gateway · OpenCode · llama.cpp (llama-server) · vLLM · AirLLM · SearXNG · MCP (GitHub, fetch, Playwright, SearXNG, skills).

**Target hardware (dev box):** RTX 4070 Ti Super (12 GB VRAM), 48 GB DDR4 RAM, NVMe storage. Linux host with Docker Engine + NVIDIA Container Toolkit installed. Single user, LAN-only.

**Working name:** `forge` (rename freely; used in container names, labels, and env prefixes).

---

## 1. Locked decisions (do not revisit during implementation)

1. **Agent engine:** reuse OpenCode as the per-session agent; wrap it, don't fork it.
2. **UI:** custom React PWA (no claudecodeui).
3. **Inference:** all three engines — llama.cpp (default daily driver), vLLM (fast lane for ≤14B AWQ), AirLLM (slow lane for over-VRAM models).
4. **Access:** LAN only in v1. Compose is structured so a VPN sidecar can be added later without touching other services.
5. **Container spawning:** host Docker socket mounted into the orchestrator ONLY. Session containers are siblings, never get the socket.
6. **Model registry:** suggest-only. A scheduled job ranks new models against this hardware; downloads happen only after approval in the UI.
7. **Backend language:** Python (FastAPI).
8. **Connectors v1:** GitHub MCP, fetch MCP, SearXNG search MCP, Playwright browser-automation MCP, plus a custom skills MCP server.
9. **No external LLM providers anywhere.** No Anthropic/OpenAI/OpenRouter keys. The only outbound calls are: Hugging Face (model metadata + weight downloads), git hosts (skill installs, repo clones), and whatever the user's session tools fetch.

## 2. Hard hardware constraints (encode these as config, not comments)

- **Single GPU lease.** Only ONE GPU-resident engine process may run at a time (llama.cpp with GPU layers, vLLM, or AirLLM during generation). The orchestrator's EngineManager enforces this by stopping the current engine container before starting another. Attempting to load a second GPU model returns HTTP 409 with the current lease holder.
- **VRAM budget:** 12 GB total; reserve ~0.8 GB for display/driver → plan against **11 GB usable**.
- **RAM budget for offload:** cap llama.cpp CPU-offloaded weights + KV at **32 GB** so the host keeps ~16 GB for the orchestrator, sessions, and OS.
- **Fit rules** (used by both the registry scorer and the load endpoint):
  - vLLM lane: model weights (AWQ/GPTQ 4-bit) + KV cache must fit in 11 GB → roughly ≤ 14–15B dense params at 16k context.
  - llama.cpp lane: GGUF file size ≤ (11 GB VRAM-resident layers + 32 GB RAM) with `--n-gpu-layers` computed to fill VRAM; MoE models (e.g. 30B-A3B class) preferred because active params are small.
  - AirLLM lane: anything that fits on disk; warn in UI that throughput is seconds-per-token and block session use (chat-only lane).

## 3. Repository layout

```
forge/
├── PLAN.md                          # this file
├── README.md
├── .env.example
├── docker-compose.yml
├── docker-compose.dev.yml           # dev overrides (UI hot reload, orchestrator --reload)
├── Makefile                         # make up / down / logs / smoke / test
├── gateway/
│   └── Caddyfile
├── orchestrator/
│   ├── Dockerfile
│   ├── pyproject.toml
│   ├── app/
│   │   ├── main.py                  # FastAPI app factory, routers, startup
│   │   ├── config.py                # pydantic-settings, all env vars
│   │   ├── db.py                    # SQLModel engine + session, migrations via create_all
│   │   ├── models.py                # SQLModel tables (see §5)
│   │   ├── auth.py                  # single-password login -> bearer token middleware
│   │   ├── routers/
│   │   │   ├── health.py
│   │   │   ├── auth.py
│   │   │   ├── sessions.py          # CRUD + WS/SSE proxy to OpenCode
│   │   │   ├── models_api.py        # catalog, suggestions, downloads, load/unload
│   │   │   ├── engines.py           # engine status, lease info
│   │   │   ├── skills.py            # list/install/remove skills
│   │   │   ├── connectors.py        # MCP connector config (tokens, enable/disable)
│   │   │   └── system.py            # GPU/RAM/disk stats via pynvml + psutil
│   │   ├── services/
│   │   │   ├── engine_manager.py    # GPU lease, start/stop engine containers, healthwait
│   │   │   ├── session_manager.py   # spawn/stop/reap OpenCode containers (docker-py)
│   │   │   ├── opencode_client.py   # thin async client for OpenCode server API
│   │   │   ├── registry.py          # HF scan, scoring, suggestions (APScheduler job)
│   │   │   ├── downloader.py        # hf_hub download with progress events
│   │   │   ├── skills_service.py    # git-based skill install, SKILL.md parsing
│   │   │   └── events.py            # in-process pub/sub -> SSE for UI (downloads, engine state)
│   │   └── opencode_config.py       # renders per-session opencode.json (provider + mcp blocks)
│   └── tests/
│       ├── test_fit_rules.py
│       ├── test_registry_scoring.py
│       ├── test_opencode_config.py
│       └── test_session_lifecycle.py  # uses docker-py against a dind fixture or mocks
├── engines/
│   ├── llamacpp/                    # thin wrapper: image ref + entrypoint template (see §6.2)
│   ├── vllm/
│   └── airllm/
│       ├── Dockerfile
│       └── server.py                # OpenAI-compatible /v1/chat/completions over AirLLM
├── session-runner/
│   ├── Dockerfile                   # OpenCode + toolchain image for coding containers
│   └── entrypoint.sh
├── mcp/
│   └── skills-server/
│       ├── Dockerfile               # or run via stdio inside session containers
│       └── server.py                # MCP server exposing list_skills / load_skill
├── ui/
│   ├── Dockerfile                   # multi-stage: build -> static, served by Caddy
│   ├── package.json
│   ├── vite.config.ts
│   ├── public/manifest.webmanifest
│   └── src/
│       ├── main.tsx, App.tsx, api/client.ts, api/sse.ts
│       └── pages/  Sessions.tsx  Chat.tsx  Files.tsx  Git.tsx
│                   Models.tsx  Skills.tsx  Connectors.tsx  System.tsx  Settings.tsx
├── skills/                          # mounted volume; installed skills live here
│   └── .gitkeep
└── scripts/
    ├── smoke.sh                     # end-to-end smoke test (see §12)
    └── seed_models.py               # inserts §10 seed catalog rows
```

## 4. Docker Compose topology

Networks: `forge-edge` (gateway ↔ orchestrator ↔ ui assets), `forge-internal` (orchestrator ↔ engines ↔ MCP services ↔ session containers). Session containers are attached to `forge-internal` only. Nothing publishes ports to the host except the gateway (`:8080`) — bound to `0.0.0.0` for LAN access.

Services in `docker-compose.yml`:

| Service | Image | GPU | Purpose | Managed by |
|---|---|---|---|---|
| `gateway` | caddy:2 | – | TLS-optional reverse proxy; serves built PWA; routes `/api/*` → orchestrator | compose |
| `orchestrator` | build ./orchestrator | – | Core API, schedulers, docker control (socket mount) | compose |
| `searxng` | searxng/searxng | – | Self-hosted web search backend | compose |
| `mcp-playwright` | mcr.microsoft.com/playwright-mcp (or equivalent) | – | Browser automation MCP over SSE | compose |
| `llamacpp` | ghcr.io/ggml-org/llama.cpp:server-cuda | yes | GGUF engine, OpenAI-compatible on :8081 | orchestrator (start/stop) |
| `vllm` | vllm/vllm-openai | yes | AWQ engine, OpenAI-compatible on :8082 | orchestrator (start/stop) |
| `airllm` | build ./engines/airllm | yes | Slow lane, OpenAI-compatible on :8083 | orchestrator (start/stop) |
| `session-<id>` | build ./session-runner | – | One per coding session, `opencode serve` on :4096 | orchestrator (docker-py) |

Engine services are defined in compose with `profiles: ["engines"]` so `docker compose up` does NOT start them; the orchestrator starts exactly one via docker-py with the chosen model's flags. GPU services use `deploy.resources.reservations.devices` with `driver: nvidia`.

Volumes: `models` (large — GGUF/AWQ/HF snapshots), `db` (SQLite file), `skills`, `workspaces` (one subdir per session), `searxng-config`, `caddy-data`.

## 5. Data model (SQLModel tables, SQLite at /data/db/forge.db)

- `ModelEntry`: id, hf_repo, display_name, family, params_b, quant (gguf-q4_k_m | awq | fp16-airllm), file_path, size_gb, engine (llamacpp|vllm|airllm), ctx_max, tool_call_format (hermes|qwen|llama3|none), status (suggested|approved|downloading|ready|failed), score, added_at.
- `Suggestion`: id, hf_repo, reason (json: trend, recency, coding_signal, fit), created_at, dismissed (bool).
- `Session`: id (uuid), name, container_id, state (creating|running|idle|stopped|error), workspace_path, model_id (FK), created_at, last_active_at, repo_url (nullable).
- `Task`: id, session_id (FK), prompt, state (queued|running|done|failed), created_at, finished_at — powers the parallel-runs view.
- `Skill`: id, name, description, source_url, path, installed_at, enabled.
- `Connector`: id, kind (github|searxng|fetch|playwright|skills), enabled, config_json (e.g. GitHub PAT — stored in SQLite, LAN-only threat model, documented in §13).
- `Setting`: key, value (auth password hash, GPU budgets, reaper timeout).

## 6. Component specifications

### 6.1 Orchestrator API (all under `/api`, bearer-token auth except `/health` and `/auth/login`)

```
POST /auth/login {password} -> {token}
GET  /health
GET  /system/stats                      # pynvml VRAM, psutil RAM/CPU/disk, engine lease
GET  /engines                           # states + current lease holder
POST /engines/load {model_id}           # enforces single GPU lease; 409 if busy with details
POST /engines/unload
GET  /models  /models/suggestions
POST /models/suggestions/{id}/approve   # -> creates ModelEntry(status=approved) + starts download
POST /models/{id}/download  DELETE /models/{id}
GET  /models/downloads/stream           # SSE progress events
GET  /sessions  POST /sessions {name, model_id, repo_url?}
POST /sessions/{id}/stop  /start  DELETE /sessions/{id}
ANY  /sessions/{id}/opencode/{path}     # authenticated reverse proxy to that session's OpenCode API
GET  /sessions/{id}/events              # SSE: proxied OpenCode message/tool-call stream
GET  /sessions/{id}/files?path=  GET /sessions/{id}/file?path=   PUT /sessions/{id}/file
GET  /sessions/{id}/git/{status|log|diff}   POST /sessions/{id}/git/{commit|push}
GET  /skills  POST /skills/install {git_url, subdir?}  DELETE /skills/{id}  PATCH /skills/{id}
GET  /connectors  PATCH /connectors/{kind}
GET  /events/stream                     # global SSE: engine state, downloads, session states
```

The session proxy is the key trick: the PWA never talks to session containers directly; the orchestrator resolves `session_id -> container IP:4096` on `forge-internal` and streams through. File and git endpoints execute inside the session container via `docker exec` (docker-py `exec_run`) so the orchestrator needs no volume access to workspaces beyond creation/cleanup.

### 6.2 Engines

**llama.cpp (default lane).** Started by EngineManager with per-model flags:

```
llama-server -m /data/models/<file>.gguf --host 0.0.0.0 --port 8081 \
  -c ${ctx} --n-gpu-layers ${ngl} --parallel ${slots} --jinja --flash-attn
```

`--jinja` enables the model's chat template so OpenAI-style tool calling works. `ngl` is computed at load time: binary-search the largest layer count whose estimated VRAM (layer_size ≈ file_size/n_layers, + KV estimate for ctx) ≤ 11 GB; remainder goes to RAM. `slots` default 2 (parallel sessions share the server; each slot splits the context budget — document this in the UI model detail view).

**vLLM (fast lane, ≤14B AWQ).**

```
--model <hf_repo> --quantization awq --max-model-len 16384 \
  --gpu-memory-utilization 0.90 --enable-auto-tool-choice \
  --tool-call-parser ${parser}   # hermes for Qwen-family, llama3_json for Llama-family
```

**AirLLM (slow lane).** `engines/airllm/server.py`: FastAPI implementing `/v1/models` and `/v1/chat/completions` (streaming + non-streaming) over `airllm.AutoModel.from_pretrained(repo, compression='4bit')`. Apply the model's chat template via tokenizer; no tool calling; hard-cap max_tokens (default 512) and expose a queue of depth 1. UI labels this lane "slow — minutes to hours per reply" and hides it from the session-creation model picker (chat page only).

All three expose the same OpenAI surface, so OpenCode and the PWA chat page use one client with different base URLs. EngineManager healthcheck: poll `GET /v1/models` until 200 (llama.cpp and vLLM may take minutes to load; stream state via `/events/stream`).

### 6.3 Session containers (`session-runner` image)

Contents: node 22 + OpenCode (pinned version), git, openssh-client, python3 + uv, build-essential, ripgrep, curl, jq. Entrypoint: clone `repo_url` if provided into `/workspace`, write the rendered `/root/.config/opencode/opencode.json`, then `opencode serve --hostname 0.0.0.0 --port 4096`.

Rendered `opencode.json` per session (template in `opencode_config.py`):

```json
{
  "provider": {
    "forge-local": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Forge local",
      "options": {"baseURL": "http://<engine-host>:<port>/v1"},
      "models": {"<model_id>": {"name": "<display_name>", "tools": true}}
    }
  },
  "model": "forge-local/<model_id>",
  "mcp": {
    "github":     {"type": "local", "command": ["github-mcp-server", "stdio"], "environment": {"GITHUB_PERSONAL_ACCESS_TOKEN": "{env:GITHUB_PAT}"}, "enabled": true},
    "fetch":      {"type": "local", "command": ["uvx", "mcp-server-fetch"], "enabled": true},
    "searxng":    {"type": "local", "command": ["uvx", "mcp-searxng"], "environment": {"SEARXNG_URL": "http://searxng:8080"}, "enabled": true},
    "playwright": {"type": "remote", "url": "http://mcp-playwright:8931/sse", "enabled": true},
    "skills":     {"type": "local", "command": ["python", "/opt/forge/skills_mcp.py"], "enabled": true}
  }
}
```

(Exact MCP package names/flags to be verified against current releases during M5 — pin whatever works and record versions in README.)

Spawn parameters (session_manager.py): image `forge-session-runner`, network `forge-internal`, labels `{"forge.session": id}`, mounts `workspaces/<id>:/workspace` and `skills:/skills:ro`, limits `mem_limit=4g, nano_cpus=4e9, pids_limit=512`, **no docker socket, no GPU**. Reaper job (APScheduler, every 5 min): stop containers idle > `FORGE_SESSION_IDLE_MIN` (default 120; resume = restart container, OpenCode reloads its session state from the workspace volume). Parallel tasks = multiple running session containers; the shared llama-server slots handle concurrent inference.

### 6.4 Model registry (suggest-only)

APScheduler cron (weekly, configurable): query Hugging Face Hub API for `pipeline_tag=text-generation` sorted by trending and by downloads (last-30-days), take top 100, then:

1. Filter: has GGUF or AWQ artifacts (search sibling repos/files), license allows use, params estimable.
2. `hardware_fit(model) -> lane|None` per §2 rules; drop misfits (keep over-VRAM ones only as AirLLM-lane suggestions if ≤ 70B).
3. Score = 0.35·trend_rank_norm + 0.25·recency_decay(created_at, half_life=60d) + 0.25·coding_signal (name/tags/readme regex: coder|code|instruct benchmarks) + 0.15·fit_quality (vLLM lane > llamacpp full-GPU > offload > airllm).
4. Upsert `Suggestion` rows for anything scoring above threshold and not already in catalog/dismissed. Emit event → UI badge on Models page.

Approval flow: approve → pick quant artifact (default Q4_K_M for GGUF) → `downloader.py` streams via huggingface_hub with progress events → status `ready` → appears in session model picker.

### 6.5 Skills system (Claude Code-compatible)

Skill = directory containing `SKILL.md` with YAML frontmatter `name:` and `description:` (Claude Code / Agent Skills format), plus any support files. Installer: `POST /skills/install {git_url, subdir}` shallow-clones into `/data/skills/<name>` and parses frontmatter; compatible with community skill repos that follow the anthropics/skills layout.

`mcp/skills-server/server.py` (MCP over stdio, mounted read-only into session containers): tools `list_skills()` → `[{name, description}]` and `load_skill(name)` → full SKILL.md body plus a file listing of the skill dir. This mirrors Claude Code's progressive-disclosure behavior: models see only names/descriptions until a skill is loaded. Session containers mount `/skills:ro` so skill support files are directly readable once a skill tells the agent where they live.

### 6.6 PWA (ui/)

React 18 + TS + Vite; installable PWA (manifest + service worker via vite-plugin-pwa, cache-first for static assets only). Mobile-first layout, bottom tab bar. Pages: **Sessions** (list, states, new-session sheet with model picker), **Chat** (SSE stream of OpenCode messages, tool-call cards with allow/deny where OpenCode requests permission, markdown + code rendering), **Files** (tree, viewer, small edits), **Git** (status/diff/commit/push), **Models** (catalog, suggestion inbox with score breakdown, download progress, load/unload with VRAM gauge), **Skills** (installed list, install-from-URL), **Connectors** (toggles + GitHub PAT field), **System** (VRAM/RAM/disk gauges, engine lease, running containers), **Settings** (password, reaper timeout, registry schedule). Single shared SSE connection to `/api/events/stream` for global state; per-chat SSE for message streams.

### 6.7 Gateway (Caddyfile)

```
:8080 {
  handle /api/* { reverse_proxy orchestrator:8000 }
  handle { root * /srv/ui  try_files {path} /index.html  file_server }
}
```

No TLS in v1 (LAN). Leave a commented block for the future VPN/TLS setup.

## 7. Security model (v1, LAN threat model — document in README)

- Single password → bearer token (argon2 hash in Settings). All `/api` routes gated.
- Docker socket only in orchestrator; session containers get no socket, no GPU, resource limits, and run as non-root.
- Socket-mount caveat: orchestrator compromise = host root. Acceptable for single-user LAN v1; note sysbox/rootless as future hardening.
- Secrets (GitHub PAT, HF token) in SQLite + `.env`; never baked into images; never logged.
- Session containers have outbound internet (git/pip/npm need it) — document that agent-run code is sandboxed only by container limits.

## 8. Environment variables (.env.example — complete list)

```
FORGE_PASSWORD=changeme            # first-run login password
FORGE_SECRET_KEY=                  # token signing, generate: openssl rand -hex 32
FORGE_DB_PATH=/data/db/forge.db
FORGE_MODELS_DIR=/data/models
FORGE_SKILLS_DIR=/data/skills
FORGE_WORKSPACES_DIR=/data/workspaces
FORGE_VRAM_BUDGET_GB=11
FORGE_RAM_OFFLOAD_BUDGET_GB=32
FORGE_SESSION_IDLE_MIN=120
FORGE_MAX_PARALLEL_SESSIONS=4
FORGE_REGISTRY_CRON=0 6 * * 1      # weekly Mon 06:00
FORGE_LLAMACPP_SLOTS=2
HF_TOKEN=                          # optional, gated models
GITHUB_PAT=                        # optional, GitHub MCP
```

## 9. GPU/memory budget reference (encode in `fit_rules`, unit-tested)

| Lane | What fits (approx) | Example |
|---|---|---|
| vLLM (11 GB VRAM) | ≤ 15B dense @ 4-bit AWQ, 16k ctx | Qwen coder 14B AWQ |
| llama.cpp full-GPU | GGUF ≤ ~10 GB file | 14B Q4_K_M |
| llama.cpp offload | GGUF ≤ ~40 GB file (VRAM+32 GB RAM), MoE strongly preferred | 30B-A3B class Q4/Q5 |
| AirLLM | ≤ 70B fp16-from-disk, chat-only | 70B instruct |

## 10. Seed model catalog (insert via `scripts/seed_models.py`; registry supersedes over time)

Pick current best-in-class at implementation time; as of this plan's writing the shortlist is: a Qwen coder-family ~14B (AWQ → vLLM lane; GGUF Q4_K_M → llama.cpp full-GPU), a Qwen 30B-class MoE coder (GGUF → llama.cpp offload lane, expected daily driver), gpt-oss-20b (GGUF, offload), and one small utility model ~7B for quick tasks. Verify each entry's tool-calling works through OpenCode before marking `ready` in the seed script. Record `tool_call_format` per model.

## 11. Milestones

### M0 — Scaffold & skeleton

- [ ] Repo layout from §3; `.env.example`; Makefile (`up`, `down`, `logs`, `test`, `smoke`)
- [ ] Orchestrator FastAPI app with `/api/health`, config, SQLModel tables from §5, auth login/token
- [ ] Compose with gateway + orchestrator + searxng + mcp-playwright; UI placeholder built and served
- [ ] **Accept:** `docker compose up -d` → `curl -s localhost:8080/api/health` returns `{"status":"ok"}`; login returns a token; all containers healthy.

### M1 — Inference lane 1 (llama.cpp) + manual catalog

- [ ] `engine_manager.py`: start/stop llamacpp container via docker-py with computed `--n-gpu-layers`; healthwait; lease state
- [ ] `POST /models` manual add (hf_repo + gguf filename) + `downloader.py` with SSE progress
- [ ] `/engines/load`, `/engines/unload`, `/system/stats` (pynvml)
- [ ] Unit tests: `test_fit_rules.py` (ngl computation, lane assignment) pass
- [ ] **Accept:** download a small GGUF via API, load it, `curl` an OpenAI chat completion through `http://llamacpp:8081/v1/chat/completions` from inside the network and get a coherent reply; loading a second model while loaded returns 409.

### M2 — Sessions + PWA core

- [ ] `session-runner` image; `session_manager.py` spawn/stop/delete with limits + labels; reaper job
- [ ] `opencode_config.py` renders provider block (MCP block empty for now); OpenCode proxy routes + SSE event stream; file/git endpoints via exec
- [ ] PWA: Sessions, Chat (streaming), Files, Git, System pages; PWA installable on phone over LAN
- [ ] **Accept:** from a phone browser on LAN — create session, ask the agent to create and run a Python script in the workspace, watch streamed tool calls, see the file in Files, commit in Git. Two sessions run in parallel against llama-server slots.

### M3 — Engine lanes 2 & 3 + GPU lease UX

- [ ] vLLM lifecycle with per-model parser flags; AirLLM `server.py` + image; lease arbitration across all three
- [ ] Models page: load/unload with VRAM gauge, lane badges, AirLLM chat-only restriction
- [ ] **Accept:** switch llamacpp→vLLM from UI (old stops, new healthy); AirLLM answers one prompt (however slowly) via chat page; session picker never offers AirLLM models.

### M4 — Model registry

- [ ] `registry.py` scan + scoring per §6.4 with tests (`test_registry_scoring.py` uses fixture JSON of HF API responses); APScheduler wiring
- [ ] Suggestions inbox UI (score breakdown, approve/dismiss), approval → download → ready
- [ ] **Accept:** trigger scan manually via API; ≥1 plausible suggestion appears with reasons; approving downloads it and it becomes loadable.

### M5 — Connectors & skills

- [ ] MCP block in generated opencode.json per §6.3 (pin working package versions); Connectors page with GitHub PAT
- [ ] `skills-server` MCP + installer + Skills page; mount `/skills:ro` into sessions
- [ ] **Accept:** in a session — agent uses SearXNG search and fetch to answer a current-events question; opens a page via Playwright MCP; lists a GitHub repo's issues with PAT; `list_skills` shows an installed skill from a public skills repo and `load_skill` injects it, observably changing behavior.

### M6 — Parallel tasks, polish, hardening

- [ ] Task queue (fire prompt at session, track state) + parallel runs view; global events wiring everywhere
- [ ] `scripts/smoke.sh`: compose up → login → load seed model → create session → run task → assert file exists in workspace → teardown
- [ ] README: install (Docker + NVIDIA toolkit), first-run, adding models, adding skills, future VPN slot
- [ ] **Accept:** `make smoke` passes clean on a fresh checkout; two tasks in two sessions complete in parallel; idle session gets reaped and resumes correctly.

## 12. Testing strategy

Pytest for orchestrator services with docker-py mocked (except one optional integration marker that requires the real socket). Pure-logic modules (fit rules, scoring, config rendering) get exhaustive unit tests written before implementation. UI: keep to typecheck + build in CI for v1. `scripts/smoke.sh` is the release gate for every milestone from M2 on. GitHub Actions: lint (ruff), pytest, UI build — no GPU steps in CI; GPU paths covered by the local smoke script.

## 13. Non-goals for v1

Remote/WAN access (structure ready, not implemented) · multi-user/RBAC · fine-tuning or training · autoscaling/multi-GPU · Kubernetes · Windows/macOS hosts · billing/quotas · voice.

## 14. Known risks & mitigations

- **Open-weight tool-calling flakiness:** mitigated by `tool_call_format` per model, `--jinja`/parser flags, and seed-list verification; keep a "tool reliability" note field on ModelEntry.
- **OpenCode API drift:** pin OpenCode version in session-runner; `opencode_client.py` is the single integration point; record the pinned version's API shape in a fixture test.
- **MCP package churn:** pin exact versions at M5; record in README.
- **VRAM OOM on load:** healthwait treats engine container exit as failure, surfaces logs to UI, auto-releases lease.
- **SQLite contention:** single-writer usage is fine for one user; wrap writes in a lock; Postgres is the documented upgrade path.
