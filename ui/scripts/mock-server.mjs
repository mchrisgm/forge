// Mock orchestrator + static PWA server for README screenshots.
//
// Zero-dependency: serves the built PWA from ui/dist (SPA fallback) and
// implements the /api endpoints the app calls with rich, static demo data so
// screenshots look like a live two-GPU Forge box. Never used in production.
//
//   node scripts/mock-server.mjs [port]     (default 4173)

import { createServer } from "node:http";
import { readFileSync, existsSync, statSync } from "node:fs";
import { join, extname, dirname, resolve, normalize } from "node:path";
import { fileURLToPath } from "node:url";
import { deflateSync } from "node:zlib";

const __dirname = dirname(fileURLToPath(import.meta.url));
const DIST = resolve(__dirname, "..", "dist");
const PORT = Number(process.argv[2] || 4173);
// SETUP_MODE=1 → /api/auth/status reports a fresh install (setup_required),
// which routes the app to the /setup wizard. Run a second instance with this
// set to capture the first-run screens.
const SETUP_MODE = process.env.SETUP_MODE === "1";

const now = Date.now();
const iso = (msAgo) => new Date(now - msAgo).toISOString();
const MIN = 60_000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

// ── Demo data ───────────────────────────────────────────────────────────────

const models = [
  {
    id: 9,
    hf_repo: "Qwen/Qwen3-4B-Instruct-2507",
    display_name: "Qwen3 4B Instruct",
    family: "qwen",
    params_b: 4.0,
    quant: "safetensors",
    file_path: "hf/Qwen__Qwen3-4B-Instruct-2507",
    size_gb: 8.1,
    engine: "sglang",
    ctx_max: 16384,
    n_layers: 0,
    is_moe: false,
    tool_call_format: "hermes",
    vision: false,
    status: "ready",
    score: 0.9,
    note: "Engine detected automatically: bf16 checkpoint fits in VRAM; served natively by SGLang",
    added_at: "2026-08-20T10:00:00Z",
  },
  {
    id: 1,
    hf_repo: "unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF",
    display_name: "Qwen3 Coder 30B A3B",
    family: "qwen3",
    params_b: 30.5,
    quant: "gguf-q4_k_m",
    file_path: "/models/qwen3-coder-30b-a3b-instruct-q4_k_m.gguf",
    size_gb: 18.6,
    engine: "llamacpp",
    ctx_max: 65536,
    n_layers: 48,
    is_moe: true,
    tool_call_format: "qwen",
    status: "ready",
    score: 0.91,
    note: "",
    added_at: iso(12 * DAY),
  },
  {
    id: 2,
    hf_repo: "Qwen/Qwen2.5-Coder-14B-Instruct-AWQ",
    display_name: "Qwen2.5 Coder 14B AWQ",
    family: "qwen2.5",
    params_b: 14.7,
    quant: "awq",
    file_path: "/models/qwen2.5-coder-14b-instruct-awq",
    size_gb: 9.9,
    engine: "vllm",
    ctx_max: 32768,
    n_layers: 48,
    is_moe: false,
    tool_call_format: "qwen",
    status: "ready",
    score: 0.84,
    note: "",
    added_at: iso(30 * DAY),
  },
  {
    id: 3,
    hf_repo: "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF",
    display_name: "Qwen2.5 Coder 7B",
    family: "qwen2.5",
    params_b: 7.6,
    quant: "gguf-q4_k_m",
    file_path: "/models/qwen2.5-coder-7b-instruct-q4_k_m.gguf",
    size_gb: 4.7,
    engine: "llamacpp",
    ctx_max: 32768,
    n_layers: 28,
    is_moe: false,
    tool_call_format: "qwen",
    status: "ready",
    score: 0.78,
    note: "",
    added_at: iso(41 * DAY),
  },
  {
    id: 6,
    hf_repo: "Qwen/Qwen2.5-Coder-32B-Instruct-AWQ",
    display_name: "Qwen2.5 Coder 32B AWQ",
    family: "qwen2.5",
    params_b: 32.8,
    quant: "awq",
    file_path: "/models/qwen2.5-coder-32b-instruct-awq",
    size_gb: 19.3,
    engine: "vllm",
    ctx_max: 32768,
    n_layers: 64,
    is_moe: false,
    tool_call_format: "qwen",
    status: "ready",
    score: 0.88,
    note: "",
    added_at: iso(8 * DAY),
  },
  {
    id: 4,
    hf_repo: "lmstudio-community/DeepSeek-Coder-V2-Lite-Instruct-GGUF",
    display_name: "DeepSeek Coder V2 Lite",
    family: "deepseek",
    params_b: 15.7,
    quant: "gguf-q4_k_m",
    file_path: "/models/deepseek-coder-v2-lite-instruct-q4_k_m.gguf",
    size_gb: 9.4,
    engine: "llamacpp",
    ctx_max: 131072,
    n_layers: 27,
    is_moe: true,
    tool_call_format: "none",
    status: "downloading",
    score: 0.8,
    note: "",
    added_at: iso(2 * HOUR),
  },
  {
    id: 5,
    hf_repo: "meta-llama/Llama-3.3-70B-Instruct",
    display_name: "Llama 3.3 70B",
    family: "llama3",
    params_b: 70.6,
    quant: "fp16-airllm",
    file_path: "/models/llama-3.3-70b-instruct",
    size_gb: 131.4,
    engine: "airllm",
    ctx_max: 8192,
    n_layers: 80,
    is_moe: false,
    tool_call_format: "llama3",
    status: "ready",
    score: 0.62,
    note: "",
    added_at: iso(20 * DAY),
  },
  {
    // Slug "tinyllama-1-1b-chat" — the auto-routing (router) fixture model.
    id: 8,
    hf_repo: "TinyLlama/TinyLlama-1.1B-Chat-v1.0-GGUF",
    display_name: "TinyLlama 1.1B Chat",
    family: "llama",
    params_b: 1.1,
    quant: "gguf-q4_k_m",
    file_path: "/models/tinyllama-1.1b-chat-v1.0-q4_k_m.gguf",
    size_gb: 0.7,
    engine: "llamacpp",
    ctx_max: 2048,
    n_layers: 22,
    is_moe: false,
    tool_call_format: "none",
    status: "ready",
    score: 0.4,
    note: "Auto-routing model — reads each prompt in Auto chats and picks the answering model.",
    added_at: iso(15 * DAY),
  },
  {
    id: 7,
    hf_repo: "black-forest-labs/FLUX.1-schnell",
    display_name: "FLUX.1 schnell",
    family: "flux",
    params_b: 12,
    quant: "fp16-diffusers",
    file_path: "/models/hf/black-forest-labs/FLUX.1-schnell",
    size_gb: 23.8,
    engine: "imagegen",
    ctx_max: 0,
    n_layers: 0,
    is_moe: false,
    tool_call_format: "none",
    status: "ready",
    score: 0,
    note: "Added from Hub search — text-to-image (diffusers snapshot).",
    added_at: iso(3 * DAY),
  },
];

// ── Hub search fixtures (GET /api/models/search) ────────────────────────────

const hubSearchResults = {
  text: [
    {
      hf_repo: "Qwen/Qwen2.5-Coder-32B-Instruct",
      downloads: 1_284_301,
      likes: 1893,
      tags: ["text-generation", "qwen2", "code"],
      gated: false,
      created_at: iso(290 * DAY),
      params_b: 32.8,
      in_catalog: false,
    },
    {
      hf_repo: "Qwen/Qwen2.5-Coder-14B-Instruct-AWQ",
      downloads: 402_118,
      likes: 264,
      tags: ["text-generation", "qwen2", "awq"],
      gated: false,
      created_at: iso(285 * DAY),
      params_b: 14.7,
      in_catalog: true,
    },
    {
      hf_repo: "mistralai/Codestral-22B-v0.1",
      downloads: 96_412,
      likes: 1247,
      tags: ["text-generation", "mistral", "code"],
      gated: true,
      created_at: iso(440 * DAY),
      params_b: 22.2,
      in_catalog: false,
    },
  ],
  image: [
    {
      hf_repo: "black-forest-labs/FLUX.1-schnell",
      downloads: 3_412_887,
      likes: 3891,
      tags: ["text-to-image", "diffusers", "flux"],
      gated: false,
      created_at: iso(380 * DAY),
      params_b: 0,
      in_catalog: true,
    },
    {
      hf_repo: "stabilityai/stable-diffusion-xl-base-1.0",
      downloads: 2_107_554,
      likes: 6712,
      tags: ["text-to-image", "diffusers", "sdxl"],
      gated: false,
      created_at: iso(750 * DAY),
      params_b: 0,
      in_catalog: false,
    },
    {
      hf_repo: "black-forest-labs/FLUX.1-dev",
      downloads: 1_893_020,
      likes: 9204,
      tags: ["text-to-image", "diffusers", "flux"],
      gated: true,
      created_at: iso(380 * DAY),
      params_b: 0,
      in_catalog: false,
    },
  ],
};

const suggestions = [
  {
    id: 101,
    hf_repo: "mistralai/Devstral-Small-2508",
    reason: {
      trend: 0.92,
      recency: 0.88,
      coding_signal: 0.95,
      fit: 0.71,
      lane: "llamacpp",
      score: 0.89,
      params_b: 24,
      is_moe: false,
      gguf_repo: "mistralai/Devstral-Small-2508_gguf",
      gguf_file: "Devstral-Small-2508-Q4_K_M.gguf",
      gguf_size_gb: 14.3,
      has_awq: false,
    },
    created_at: iso(6 * HOUR),
    dismissed: false,
  },
  {
    id: 102,
    hf_repo: "ByteDance-Seed/Seed-Coder-8B-Instruct",
    reason: {
      trend: 0.81,
      recency: 0.64,
      coding_signal: 0.9,
      fit: 0.94,
      lane: "vllm",
      score: 0.81,
      params_b: 8.2,
      is_moe: false,
      gguf_repo: null,
      gguf_file: null,
      gguf_size_gb: 0,
      has_awq: true,
    },
    created_at: iso(3 * DAY),
    dismissed: false,
  },
];

const lease0 = {
  model_id: 1,
  model_name: "Qwen3 Coder 30B A3B",
  model_slug: "qwen3-coder-30b-a3b",
  engine: "llamacpp",
  gpu_ids: [0],
  gpu_index: 0,
  state: "ready",
  container_id: "forge-llamacpp",
  base_url: "http://forge-llamacpp:8081/v1",
  error: "",
  acquired_at: iso(52 * MIN),
};

const lease1 = {
  model_id: 2,
  model_name: "Qwen2.5 Coder 14B AWQ",
  model_slug: "qwen25-coder-14b-awq",
  engine: "vllm",
  gpu_ids: [1],
  gpu_index: 1,
  state: "ready",
  container_id: "forge-vllm",
  base_url: "http://forge-vllm:8082/v1",
  error: "",
  acquired_at: iso(18 * MIN),
};

const enginesStatus = {
  gpu_count: 2,
  lease: lease0,
  leases: [lease0, lease1],
  gpus: [
    { index: 0, lease: lease0 },
    { index: 1, lease: lease1 },
  ],
  engines: {
    llamacpp: { port: 8081, active_on: [0] },
    vllm: { port: 8082, active_on: [1] },
    airllm: { port: 8083, active_on: [] },
  },
};

const gpuStats = [
  {
    index: 0,
    name: "NVIDIA GeForce RTX 4070 Ti SUPER",
    vram_total_gb: 16.0,
    vram_used_gb: 13.2,
    utilization_pct: 87,
  },
  {
    index: 1,
    name: "NVIDIA GeForce RTX 4070 Ti SUPER",
    vram_total_gb: 16.0,
    vram_used_gb: 10.4,
    utilization_pct: 54,
  },
];

const systemStats = {
  gpu: gpuStats[0],
  gpus: gpuStats,
  ram: { total_gb: 48.0, used_gb: 29.3, pct: 61 },
  cpu_pct: 34,
  disk: { total_gb: 931.5, used_gb: 412.7, free_gb: 518.8 },
  engine: enginesStatus,
  session_containers: [
    { name: "forge-session-a1b2c3", status: "running", session_id: "sess-1" },
    { name: "forge-session-d4e5f6", status: "running", session_id: "sess-2" },
    { name: "forge-session-g7h8i9", status: "exited", session_id: "sess-3" },
  ],
  services: [
    { service: "gateway", status: "running", running: true, optional: false },
    { service: "orchestrator", status: "running", running: true, optional: false },
    { service: "searxng", status: "running", running: true, optional: false },
    { service: "mcp-playwright", status: "running", running: true, optional: false },
    { service: "mcp-scrapling", status: "running", running: true, optional: false },
    { service: "headroom", status: "running", running: true, optional: false },
    { service: "smolvm", status: "running", running: true, optional: true },
  ],
  docker_ok: true,
  missing_images: [], // all built — no "run make up" banner in screenshots
  budgets: { vram_gb: 15, ram_offload_gb: 32 },
};

const sessions = [
  {
    id: "sess-1",
    name: "fix flaky auth tests",
    container_id: "forge-session-a1b2c3",
    state: "running",
    workspace_path: "/workspace",
    model_id: 1,
    created_at: iso(3 * HOUR),
    last_active_at: iso(2 * MIN),
    repo_url: "https://github.com/acme/api-server",
    last_error: "",
  },
  {
    id: "sess-2",
    name: "add dark mode",
    container_id: "forge-session-d4e5f6",
    state: "idle",
    workspace_path: "/workspace",
    model_id: 3,
    created_at: iso(26 * HOUR),
    last_active_at: iso(38 * MIN),
    repo_url: "https://github.com/acme/webapp",
    last_error: "",
  },
  {
    id: "sess-3",
    name: "profile slow ingest job",
    container_id: "forge-session-g7h8i9",
    state: "stopped",
    workspace_path: "/workspace",
    model_id: 2,
    created_at: iso(4 * DAY),
    last_active_at: iso(26 * HOUR),
    repo_url: "https://github.com/acme/data-pipeline",
    last_error: "",
  },
];

// ── Curated OpenCode conversation for sess-1 ────────────────────────────────

const OC_ID = "oc-1";

const ocSessions = [
  {
    id: OC_ID,
    title: "fix flaky auth tests",
    time: { created: now - 3 * HOUR, updated: now - 2 * MIN },
  },
];

const analysisText = [
  "I reproduced it by running the test in a loop — it fails whenever the refresh lands in the **same second** the token was issued.",
  "",
  "The bug is in `services/auth/token.py`: `is_expired()` compares with `<`, so a token that expires exactly at `now()` is still considered valid and never rotated:",
  "",
  "```python",
  "def is_expired(self, now: datetime) -> bool:",
  "    return self.expires_at < now  # boundary: equal timestamps pass",
  "```",
  "",
  "CI runners are fast enough to hit that boundary regularly, which is why it only flakes there. I'll make expiry inclusive and freeze time in the test so the assertion stops depending on runner speed.",
].join("\n");

const summaryText = [
  "**Fixed.** The flake was a boundary condition, not test infrastructure:",
  "",
  "- `is_expired()` now treats a token expiring exactly at `now` as expired (`<=` instead of `<`)",
  "- `test_token_refresh` freezes time with `freezegun`, so same-second refreshes are deterministic",
  "- 20 consecutive runs of the auth suite pass cleanly",
  "",
  "Want me to commit this on `fix/flaky-token-refresh` and open a PR?",
].join("\n");

const ocMessages = [
  {
    info: {
      id: "msg-1",
      role: "user",
      sessionID: OC_ID,
      time: { created: now - 21 * MIN },
    },
    parts: [
      {
        id: "prt-u1",
        messageID: "msg-1",
        sessionID: OC_ID,
        type: "text",
        text: "The auth suite keeps flaking in CI — test_token_refresh fails roughly 1 in 5 runs with `AssertionError: token was not refreshed`. Can you track down the root cause and fix it?",
      },
    ],
  },
  {
    info: {
      id: "msg-2",
      role: "assistant",
      sessionID: OC_ID,
      time: { created: now - 20 * MIN, completed: now - 14 * MIN },
    },
    parts: [
      {
        id: "prt-a1",
        messageID: "msg-2",
        sessionID: OC_ID,
        type: "text",
        text: analysisText,
      },
      {
        id: "prt-t1",
        messageID: "msg-2",
        sessionID: OC_ID,
        type: "tool",
        tool: "read",
        callID: "call-1",
        state: {
          status: "completed",
          title: "tests/auth/test_token_refresh.py",
          input: { filePath: "tests/auth/test_token_refresh.py" },
          output:
            "def test_token_refresh(client):\n    token = issue_token(ttl=1)\n    time.sleep(1)\n    refreshed = client.post(\"/auth/refresh\", token=token)\n    assert refreshed.token != token, \"token was not refreshed\"",
        },
      },
      {
        id: "prt-t2",
        messageID: "msg-2",
        sessionID: OC_ID,
        type: "tool",
        tool: "edit",
        callID: "call-2",
        state: {
          status: "completed",
          title: "services/auth/token.py",
          input: {
            filePath: "services/auth/token.py",
            oldString: "return self.expires_at < now",
            newString: "return self.expires_at <= now",
          },
          output:
            "Edited services/auth/token.py (1 replacement).\n\nis_expired() now treats a token expiring exactly at `now` as expired, so a refresh that lands in the same second still rotates the token.",
        },
      },
      {
        id: "prt-t3",
        messageID: "msg-2",
        sessionID: OC_ID,
        type: "tool",
        tool: "bash",
        callID: "call-3",
        state: {
          status: "completed",
          title: "pytest tests/auth -q ×20",
          input: { command: "for i in $(seq 20); do pytest tests/auth -q || break; done" },
          output:
            "run 20/20 — 84 passed in 6.21s\nno failures across 20 consecutive runs\nslowest: test_token_refresh 0.41s",
        },
      },
    ],
  },
  {
    info: {
      id: "msg-3",
      role: "assistant",
      sessionID: OC_ID,
      time: { created: now - 14 * MIN, completed: now - 13 * MIN },
    },
    parts: [
      {
        id: "prt-a2",
        messageID: "msg-3",
        sessionID: OC_ID,
        type: "text",
        text: summaryText,
      },
    ],
  },
];

// ── Files / git / tasks for sess-1 ──────────────────────────────────────────

const fileTree = {
  "": [
    { name: "src", type: "dir", size: 0 },
    { name: "tests", type: "dir", size: 0 },
    { name: "README.md", type: "file", size: 2481 },
    { name: "pyproject.toml", type: "file", size: 914 },
  ],
  src: [
    { name: "auth", type: "dir", size: 0 },
    { name: "main.py", type: "file", size: 3720 },
  ],
  "src/auth": [
    { name: "token.py", type: "file", size: 1980 },
    { name: "routes.py", type: "file", size: 4102 },
  ],
  tests: [
    { name: "auth", type: "dir", size: 0 },
    { name: "conftest.py", type: "file", size: 1204 },
  ],
  "tests/auth": [
    { name: "test_token_refresh.py", type: "file", size: 1480 },
  ],
};

const fileContents = {
  "README.md":
    "# acme/api-server\n\nFastAPI backend for the Acme platform.\n\n## Development\n\n```bash\nuv sync\nuv run pytest\nuv run uvicorn src.main:app --reload\n```\n",
  "src/auth/token.py":
    "from datetime import datetime, timedelta\n\n\nclass Token:\n    def __init__(self, value: str, expires_at: datetime) -> None:\n        self.value = value\n        self.expires_at = expires_at\n\n    def is_expired(self, now: datetime) -> bool:\n        return self.expires_at <= now\n",
};

const gitStatus = {
  branch: "fix/flaky-token-refresh",
  changes: [
    { status: "M", path: "services/auth/token.py" },
    { status: "M", path: "tests/auth/test_token_refresh.py" },
  ],
};

const gitLog = [
  {
    hash: "9f21c4a",
    author: "forge-agent",
    date: iso(15 * MIN),
    subject: "fix(auth): make token expiry inclusive at the boundary",
  },
  {
    hash: "b8305de",
    author: "chris",
    date: iso(2 * DAY),
    subject: "ci: run auth suite on 3.11 and 3.12",
  },
  {
    hash: "51adca9",
    author: "chris",
    date: iso(3 * DAY),
    subject: "feat(auth): sliding-window refresh tokens",
  },
];

const gitDiff = {
  diff: [
    "diff --git a/services/auth/token.py b/services/auth/token.py",
    "index 4c7f2aa..9d114be 100644",
    "--- a/services/auth/token.py",
    "+++ b/services/auth/token.py",
    "@@ -18,7 +18,7 @@ class Token:",
    "     def is_expired(self, now: datetime) -> bool:",
    '-        return self.expires_at < now',
    '+        return self.expires_at <= now',
    "",
    "diff --git a/tests/auth/test_token_refresh.py b/tests/auth/test_token_refresh.py",
    "index 77b01c2..f00d9e1 100644",
    "--- a/tests/auth/test_token_refresh.py",
    "+++ b/tests/auth/test_token_refresh.py",
    "@@ -1,9 +1,10 @@",
    "+from freezegun import freeze_time",
    " import time",
    "",
    "-def test_token_refresh(client):",
    "-    token = issue_token(ttl=1)",
    "-    time.sleep(1)",
    "+@freeze_time(\"2026-08-19 12:00:00\", tick=False)",
    "+def test_token_refresh(client):",
    "+    token = issue_token(ttl=0)",
    "     refreshed = client.post(\"/auth/refresh\", token=token)",
    '     assert refreshed.token != token, "token was not refreshed"',
  ].join("\n"),
};

const tasks = [
  {
    id: 1,
    session_id: "sess-1",
    prompt: "Run the full test suite and summarize any failures",
    state: "done",
    opencode_session_id: "task-1",
    result: "84 passed, 0 failed — the flaky auth test is fixed.",
    created_at: iso(50 * MIN),
    finished_at: iso(44 * MIN),
    thinking: "auto",
  },
  {
    id: 2,
    session_id: "sess-1",
    prompt: "Draft a changelog entry for the token refresh fix",
    state: "running",
    opencode_session_id: "task-2",
    result: "",
    created_at: iso(4 * MIN),
    finished_at: null,
    thinking: "low",
  },
];

const skills = [
  {
    id: 1,
    name: "conventional-commits",
    description: "Commit message conventions and changelog discipline for Acme repos.",
    source_url: "https://github.com/acme/agent-skills",
    path: "/skills/conventional-commits",
    installed_at: iso(9 * DAY),
    enabled: true,
  },
  {
    id: 2,
    name: "fastapi-review",
    description: "Review checklist for FastAPI endpoints: auth, pagination, error shapes.",
    source_url: "https://github.com/acme/agent-skills",
    path: "/skills/fastapi-review",
    installed_at: iso(6 * DAY),
    enabled: true,
  },
];

// ── Suggested-skills catalog + pack importer (routers/skills.py) ────────────

const ECC_REPO = "https://github.com/affaan-m/ECC";
const cat = (name, description, category, installed = false) => ({
  name,
  description,
  category,
  repo: ECC_REPO,
  subdir: `skills/${name}`,
  installed,
});
const skillCatalog = [
  cat("tdd-workflow", "Test-driven development loop: failing test first, then implement, with coverage goals.", "workflow", true),
  cat("verification-loop", "Verify a coding session's work (build, tests, lint, claims) before declaring it done.", "workflow"),
  cat("git-workflow", "Branching strategies, commit conventions, merge vs rebase, and conflict resolution.", "workflow"),
  cat("blueprint", "Turn a one-line objective into a stepwise plan with self-contained context briefs.", "workflow"),
  cat("python-patterns", "Pythonic idioms, PEP 8, type hints, and structure for maintainable Python.", "languages"),
  cat("fastapi-patterns", "FastAPI structure, Pydantic v2 schemas, dependency injection, and async handlers.", "languages"),
  cat("react-patterns", "React 18/19: hooks discipline, server/client boundaries, Suspense, and state.", "languages"),
  cat("rust-patterns", "Idiomatic Rust: ownership, error handling, traits, and concurrency.", "languages"),
  cat("security-review", "Security checklist for auth, user input, secrets, API endpoints, and sensitive features.", "quality"),
  cat("api-design", "REST API design: resource naming, status codes, pagination, and versioning.", "quality"),
  cat("accessibility", "Build and audit UI against WCAG 2.2 AA: keyboard, contrast, and screen-reader support.", "quality"),
  cat("market-research", "Market sizing, competitive analysis, and industry intelligence with source attribution.", "research"),
  cat("article-writing", "Long-form articles, guides, and tutorials in a consistent voice with solid structure.", "research"),
  cat("mcp-server-patterns", "Build MCP servers with the Node/TypeScript SDK: tools, resources, validation, transports.", "other"),
  cat("context-budget", "Audit what is eating the context window (agents, skills, MCP servers) and trim it.", "other"),
];

// A believable multi-skill monorepo for the pack-importer scan.
const packScan = [
  { name: "commit-craft", description: "Conventional-commit messages and changelog discipline.", subdir: "skills/commit-craft" },
  { name: "pr-review", description: "Structured pull-request review checklist.", subdir: "skills/pr-review" },
  { name: "sql-tuning", description: "Diagnose and fix slow SQL queries with EXPLAIN.", subdir: "skills/sql-tuning" },
  { name: "terraform-modules", description: "Reusable Terraform module patterns and state hygiene.", subdir: "skills/terraform-modules" },
  { name: "incident-response", description: "On-call runbook: triage, mitigate, and write the postmortem.", subdir: "skills/incident-response" },
  { name: "legacy-notes", description: "", subdir: "skills/legacy-notes", note: "SKILL.md frontmatter missing or malformed" },
];

const settings = {
  session_idle_min: 30,
  registry_cron: "0 6 * * 1",
  max_parallel_sessions: 3,
  vram_budget_gb: 15,
  ram_offload_budget_gb: 32,
  llamacpp_slots: 2,
  headroom: {
    enabled: true,
    healthy: true,
    url: "http://forge-headroom:8088",
  },
};

// Chat system prompt (admin-editable) — mirrors chat_service.py's default.
// The override lives in memory so save/restore round-trips in the mock.
const DEFAULT_CHAT_SYSTEM_PROMPT = [
  "You are Forge, an AI assistant running privately on your user's own hardware.",
  "",
  "Give direct answers. Lead with the answer itself, then add context only when",
  "it genuinely helps. Skip preamble and never restate the question back. If",
  "you don't know something, say so plainly instead of guessing.",
  "",
  "Match the format to the question: short questions get short answers in plain",
  "prose. Use headings, lists, or tables only when structure makes the answer",
  "clearer, and code blocks for code. Answer in the language the user writes in.",
  "",
  "You may be given extra context: the user's standing instructions, long-term",
  "memories about them, attached files or images, and a summary of the earlier",
  "conversation. Use it naturally — do not recite it back.",
].join("\n");

let chatSystemPromptOverride = ""; // "" = default in effect

const chatSystemPromptView = () => ({
  chat_system_prompt: chatSystemPromptOverride || DEFAULT_CHAT_SYSTEM_PROMPT,
  chat_system_prompt_customized: Boolean(chatSystemPromptOverride),
  chat_system_prompt_default: DEFAULT_CHAT_SYSTEM_PROMPT,
});

// Auto-routing model (admin-editable via PATCH /api/settings). In-memory like
// the chat system prompt; ready only while it names the TinyLlama fixture
// ("" = LLM routing disabled — Auto falls back to deterministic picks).
const ROUTER_FIXTURE_SLUG = "tinyllama-1-1b-chat";
let routerModelSlug = ROUTER_FIXTURE_SLUG;

const routerSettingsView = () => ({
  router_model_slug: routerModelSlug,
  router_model_ready: routerModelSlug === ROUTER_FIXTURE_SLUG,
});

const connectors = JSON.parse(
  readFileSync(join(__dirname, "mock-connectors.json"), "utf8"),
);

// ── Per-user OAuth sign-in (routers/connectors.py + services/oauth_flows.py) ─

// Admin-configured OAuth apps (PATCH /api/settings flat keys). Both client
// IDs are set so the "Sign in with …" buttons render out of the box.
const oauthSettings = {
  github_oauth_client_id: "Iv1.mock0demo0client",
  hf_oauth_client_id: "hf-mock-oauth-app",
  hf_oauth_client_secret: "",
};

const OAUTH_PROVIDER_META = {
  github: {
    label: "GitHub",
    method: "device",
    clientIdKey: "github_oauth_client_id",
    setup_note:
      "Create a GitHub OAuth App (any callback URL) with device flow " +
      "enabled, then paste its client ID here. No client secret needed.",
    setup_url: "https://github.com/settings/developers",
  },
  "hugging-face": {
    label: "Hugging Face",
    method: "code",
    clientIdKey: "hf_oauth_client_id",
    secretKey: "hf_oauth_client_secret",
    setup_note:
      "Create a Hugging Face OAuth app with redirect URL http(s)://<your " +
      "forge host>/oauth/callback and paste its client ID (and secret, if " +
      "issued) here.",
    setup_url: "https://huggingface.co/settings/applications",
  },
};

// Demo per-user state: GitHub starts connected (connected chip + repo picker
// work immediately); Hugging Face shows the sign-in button. Disconnect via
// the UI to exercise the not-connected paths.
const oauthConnections = {
  github: { account: "chris-dev", connected_at: (now - 2 * DAY) / 1000 },
  "hugging-face": null,
};

const oauthProviderStatus = (kind) => {
  const meta = OAUTH_PROVIDER_META[kind];
  if (!meta) return { supported: false };
  return {
    supported: true,
    method: meta.method,
    ready: Boolean(oauthSettings[meta.clientIdKey]),
    setup_note: meta.setup_note,
    setup_url: meta.setup_url,
  };
};

const connectorOauthView = (kind) => {
  const conn = oauthConnections[kind] ?? null;
  return {
    ...oauthProviderStatus(kind),
    connected: Boolean(conn),
    account: conn?.account ?? "",
    connected_at: conn?.connected_at ?? null,
  };
};

const settingsOauthView = () =>
  Object.fromEntries(
    Object.entries(OAUTH_PROVIDER_META).map(([kind, meta]) => [
      kind,
      {
        label: meta.label,
        method: meta.method,
        client_id: oauthSettings[meta.clientIdKey],
        needs_secret: Boolean(meta.secretKey),
        has_secret: Boolean(meta.secretKey && oauthSettings[meta.secretKey]),
        setup_note: meta.setup_note,
        setup_url: meta.setup_url,
      },
    ]),
  );

// Pending flows: device polls report pending once, then connected.
let oauthFlowSeq = 0;
const oauthFlows = new Map(); // flow_id -> { kind, polls }

const githubRepos = [
  {
    full_name: "acme/api-server",
    private: true,
    default_branch: "main",
    description: "FastAPI backend for the Acme platform.",
    pushed_at: iso(2 * HOUR),
    html_url: "https://github.com/acme/api-server",
    clone_url: "https://github.com/acme/api-server.git",
  },
  {
    full_name: "acme/webapp",
    private: false,
    default_branch: "main",
    description: "Customer-facing React app.",
    pushed_at: iso(7 * HOUR),
    html_url: "https://github.com/acme/webapp",
    clone_url: "https://github.com/acme/webapp.git",
  },
  {
    full_name: "chris-dev/dotfiles",
    private: false,
    default_branch: "main",
    description: "",
    pushed_at: iso(3 * DAY),
    html_url: "https://github.com/chris-dev/dotfiles",
    clone_url: "https://github.com/chris-dev/dotfiles.git",
  },
  {
    full_name: "acme/data-pipeline",
    private: true,
    default_branch: "develop",
    description: "Nightly ingest + dbt models.",
    pushed_at: iso(6 * DAY),
    html_url: "https://github.com/acme/data-pipeline",
    clone_url: "https://github.com/acme/data-pipeline.git",
  },
  {
    full_name: "chris-dev/irrigation-controller",
    private: false,
    default_branch: "main",
    description: "ESP32 firmware for the garden drip system.",
    pushed_at: iso(21 * DAY),
    html_url: "https://github.com/chris-dev/irrigation-controller",
    clone_url: "https://github.com/chris-dev/irrigation-controller.git",
  },
];

// ── Multi-user profiles (routers/users.py) ──────────────────────────────────

const currentUser = {
  id: 1,
  username: "chris",
  display_name: "Chris",
  is_admin: true,
  avatar_color: "#f59e0b",
  memory_enabled: true,
  personal_instructions:
    "Be concise. Metric units and 24-hour times. When code is involved, lead with a runnable snippet.",
  created_at: iso(90 * DAY),
};

const publicUsers = [
  {
    id: 1,
    username: "chris",
    display_name: "Chris",
    is_admin: true,
    avatar_color: "#f59e0b",
  },
  {
    id: 2,
    username: "maya",
    display_name: "Maya",
    is_admin: false,
    avatar_color: "#8b5cf6",
  },
  {
    id: 3,
    username: "sam",
    display_name: "Sam",
    is_admin: false,
    avatar_color: "#10b981",
  },
];

// ── Chat conversations (routers/chat.py) ────────────────────────────────────

// The imagegen lane serving FLUX — lights up the chat image affordance.
const imageLease = {
  model_id: 7,
  model_name: "FLUX.1 schnell",
  model_slug: "flux-1-schnell",
  engine: "imagegen",
  gpu_ids: [1],
  gpu_index: 1,
  state: "ready",
  container_id: "forge-imagegen",
  base_url: "http://forge-imagegen:8084/v1",
  error: "",
  acquired_at: iso(9 * MIN),
};

const chatStatus = { serving: [lease0], image: imageLease };

const conv = (id, title, model_slug, updatedAgo, createdAgo, extra = {}) => ({
  id,
  user_id: 1,
  title,
  model_slug,
  thinking: "auto",
  memory_enabled: true,
  archived: false,
  summarized_until: 0,
  created_at: iso(createdAgo),
  updated_at: iso(updatedAgo),
  ...extra,
});

const conversations = [
  conv("conv-1", "Plan the garden irrigation", "qwen3-coder-30b-a3b", 8 * MIN, 2 * HOUR),
  conv("conv-2", "Debug docker compose networking", "qwen3-coder-30b-a3b", 3 * HOUR, 5 * HOUR),
  conv("conv-3", "Trip to Lisbon in October", "qwen25-coder-14b-awq", 26 * HOUR, 28 * HOUR),
  conv("conv-4", "Sourdough starter rescue", "qwen3-coder-30b-a3b", 2 * DAY, 2 * DAY + 2 * HOUR),
  conv("conv-5", "Birthday gift ideas for Maya", "qwen3-coder-30b-a3b", 4 * DAY, 4 * DAY + HOUR),
  conv("conv-6", "Regex for log timestamps", "qwen25-coder-14b-awq", 6 * DAY, 6 * DAY + HOUR),
];

const archivedConversations = [
  conv("conv-7", "Compare NAS drive options", "qwen25-coder-14b-awq", 21 * DAY, 22 * DAY, {
    archived: true,
  }),
];

// The curated conversation the chat screenshots open: an everyday question
// with an image attachment and markdown-rich replies (table, lists, code).
const gardenAttachment = {
  id: "up-1",
  filename: "raised-beds.png",
  kind: "image",
  mime: "image/png",
  size_bytes: 0, // patched after the PNG is generated below
  generated: false,
  prompt: "",
};

// A Forge-generated image attached to an assistant turn (POST /api/chat/image).
const generatedPrompt =
  "Top-down illustration of three raised garden beds connected by drip " +
  "irrigation lines running from a rain barrel";

const generatedAttachment = {
  id: "up-gen-1",
  filename: "top-down-illustration-of-three-raised-garden-beds.png",
  kind: "image",
  mime: "image/png",
  size_bytes: 0, // patched after the PNG is generated below
  generated: true,
  prompt: generatedPrompt,
};

const gardenReply = [
  "Nice setup — three beds off one rain barrel is very doable with a gravity-fed drip line.",
  "",
  "**Split the beds into two zones**",
  "",
  "| Zone | Beds | Emitters | Daily water |",
  "|---|---|---|---|",
  "| A | Tomatoes | 6 × 2 L/h | ~8 L |",
  "| B | Herbs + greens | 8 × 1 L/h | ~5 L |",
  "",
  "Tomatoes want deep, infrequent watering; herbs and greens prefer little and often — separate timer valves keep both happy. Add a cheap inline mesh filter right at the barrel: rain water clogs emitters fast.",
  "",
  "**Starting schedule** — adjust to the weather after a week:",
  "",
  "```",
  "Zone A  06:30  20 min  daily",
  "Zone B  07:00   8 min  twice daily",
  "```",
].join("\n");

// Persisted reasoning: stored assistant turns can carry literal <think>
// blocks — the UI extracts them into the collapsed "Thinking" expander and
// keeps them out of the rendered answer.
const gardenFollowUp = [
  "<think>80 cm of head ≈ 0.08 bar (h·ρ·g ≈ 0.8 m × 1000 kg/m³ × 9.81 m/s² " +
    "≈ 7.8 kPa). Regular pressure-compensating drippers need 1+ bar, so the " +
    "advice must steer to gravity-rated emitters and short runs.</think>",
  "80 cm of head is only **0.08 bar**, so skip anything rated 1–4 bar:",
  "",
  "- Choose *gravity-rated* (unregulated) drippers — pressure-compensating ones barely open",
  "- Keep each zone under ~10 m of line so friction losses stay negligible",
  "- Raise the barrel on cinder blocks if you can; every extra 30 cm helps",
  "",
  "Fully open you'll get roughly 1–2 L/h per outlet — exactly the trickle a drip bed wants.",
].join("\n");

const chatMsg = (id, role, content, createdAgo, attachments = []) => ({
  id,
  conversation_id: "conv-1",
  role,
  content,
  token_estimate: Math.ceil(content.length / 4),
  created_at: iso(createdAgo),
  attachments,
});

const conversationDetail = {
  ...conversations[0],
  summary: "",
  messages: [
    chatMsg(
      101,
      "user",
      "Here's our back garden — we just built three raised beds (tomatoes, herbs, salad greens). I want drip irrigation running off the rain barrel before it gets properly hot. Where do I start?",
      24 * MIN,
      [gardenAttachment],
    ),
    chatMsg(102, "assistant", gardenReply, 22 * MIN),
    chatMsg(
      103,
      "user",
      "The barrel only sits about 80 cm above the beds — is that enough pressure for drippers?",
      10 * MIN,
    ),
    chatMsg(104, "assistant", gardenFollowUp, 8 * MIN),
    chatMsg(105, "user", generatedPrompt, 5 * MIN),
    chatMsg(
      106,
      "assistant",
      `[Generated image: ${generatedPrompt}]`,
      4 * MIN,
      [generatedAttachment],
    ),
  ],
};

/** Small generic detail so every listed conversation opens without errors. */
const genericDetail = (c) => ({
  ...c,
  summary: "",
  messages: [
    chatMsg(1, "user", "…", 2 * HOUR),
    chatMsg(2, "assistant", "…", 2 * HOUR),
  ].map((m) => ({ ...m, conversation_id: c.id })),
});

// ── Memory (routers/memory_api.py) ──────────────────────────────────────────

let memId = 0;
const mem = (kind, content, importance, pinned, useCount, agoDays, src = "") => ({
  id: ++memId,
  user_id: 1,
  kind,
  content,
  importance,
  pinned,
  source_conversation_id: src,
  use_count: useCount,
  created_at: iso(agoDays * DAY),
  updated_at: iso(Math.max(0.2, agoDays / 2) * DAY),
  last_used_at: iso(Math.max(0.1, agoDays / 3) * DAY),
});

const memories = [
  mem("fact", "Lives in Rotterdam; the garden faces south-west.", 1.6, true, 12, 60),
  mem("fact", "Household: Chris, Maya and two kids (8 and 11).", 1.1, false, 4, 45),
  mem(
    "fact",
    "Runs Forge on a two-GPU box in the attic (2× RTX 4070 Ti Super).",
    0.9,
    false,
    3,
    30,
  ),
  mem(
    "preference",
    "Prefers concise answers with metric units and 24-hour times.",
    1.8,
    true,
    31,
    75,
  ),
  mem("preference", "Docker Compose over Kubernetes for home projects.", 1.2, false, 7, 40),
  mem("preference", "Vegetarian household; spicy food is welcome.", 0.8, false, 2, 20),
  mem(
    "project",
    "Building drip irrigation for three raised beds, fed from a rain barrel.",
    1.4,
    false,
    6,
    5,
    "conv-1",
  ),
  mem(
    "project",
    "Migrating the family photo archive to Immich on the NAS.",
    1.0,
    false,
    3,
    14,
  ),
  mem(
    "episode",
    "2026-08-12: fixed the compose networking flake by renaming the conflicting `web` service.",
    0.7,
    false,
    1,
    7,
    "conv-2",
  ),
  mem(
    "episode",
    "Planned a week in Lisbon for October — flights not booked yet.",
    0.6,
    false,
    1,
    1,
    "conv-3",
  ),
];

// ── Attachment image: a small pleasant PNG rendered at startup ──────────────
// Zero-dependency PNG encoder (deflate via node:zlib, hand-rolled CRC32) so
// the attachment thumbnail in the chat screenshots is a real image.

const CRC_TABLE = new Int32Array(256).map((_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c;
});

function crc32(buf) {
  let c = 0xffffffff;
  for (const byte of buf) c = CRC_TABLE[(c ^ byte) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

function pngChunk(type, data) {
  const chunk = Buffer.concat([Buffer.from(type, "ascii"), data]);
  const out = Buffer.alloc(chunk.length + 8);
  out.writeUInt32BE(data.length, 0);
  chunk.copy(out, 4);
  out.writeUInt32BE(crc32(chunk), chunk.length + 4);
  return out;
}

function encodePng(width, height, rgb) {
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 2; // color type: truecolor
  const raw = Buffer.alloc(height * (1 + width * 3));
  for (let y = 0; y < height; y++) {
    raw[y * (1 + width * 3)] = 0; // filter: none
    rgb.copy(raw, y * (1 + width * 3) + 1, y * width * 3, (y + 1) * width * 3);
  }
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    pngChunk("IHDR", ihdr),
    pngChunk("IDAT", deflateSync(raw, { level: 9 })),
    pngChunk("IEND", Buffer.alloc(0)),
  ]);
}

function renderGardenPng() {
  const W = 480;
  const H = 360;
  const px = Buffer.alloc(W * H * 3);
  const set = (x, y, r, g, b) => {
    if (x < 0 || y < 0 || x >= W || y >= H) return;
    const i = (y * W + x) * 3;
    px[i] = r;
    px[i + 1] = g;
    px[i + 2] = b;
  };
  const rect = (x0, y0, x1, y1, r, g, b) => {
    for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) set(x, y, r, g, b);
  };
  const HORIZON = 210;
  // Sky: soft blue fading to warm near the horizon.
  for (let y = 0; y < HORIZON; y++) {
    const t = y / HORIZON;
    const r = Math.round(140 + 90 * t);
    const g = Math.round(196 + 40 * t);
    const b = Math.round(235 - 30 * t);
    for (let x = 0; x < W; x++) set(x, y, r, g, b);
  }
  // Sun with a soft rim.
  for (let y = 30; y < 130; y++) {
    for (let x = 340; x < 440; x++) {
      const d = Math.hypot(x - 390, y - 80);
      if (d < 34) set(x, y, 255, 209, 102);
      else if (d < 42) set(x, y, 250, 220, 150);
    }
  }
  // Grass: gentle vertical gradient.
  for (let y = HORIZON; y < H; y++) {
    const t = (y - HORIZON) / (H - HORIZON);
    const r = Math.round(116 - 30 * t);
    const g = Math.round(176 - 40 * t);
    const b = Math.round(101 - 30 * t);
    for (let x = 0; x < W; x++) set(x, y, r, g, b);
  }
  // Three raised beds: timber frame, dark soil, rows of plants.
  const beds = [
    { x: 28, w: 128 },
    { x: 176, w: 128 },
    { x: 324, w: 128 },
  ];
  for (const bed of beds) {
    const y0 = 248;
    const y1 = 332;
    rect(bed.x, y0, bed.x + bed.w, y1, 146, 104, 66); // timber
    rect(bed.x + 8, y0 + 8, bed.x + bed.w - 8, y1 - 8, 74, 52, 38); // soil
    for (let row = 0; row < 3; row++) {
      for (let col = 0; col < 5; col++) {
        const cx = bed.x + 22 + col * ((bed.w - 44) / 4);
        const cy = y0 + 22 + row * 24;
        for (let dy = -6; dy <= 6; dy++) {
          for (let dx = -6; dx <= 6; dx++) {
            if (dx * dx + dy * dy <= 30) {
              set(Math.round(cx + dx), cy + dy, 96, 168, 82);
            }
          }
        }
        set(Math.round(cx), cy - 1, 132, 200, 112);
      }
    }
  }
  // Rain barrel on the right edge.
  rect(452, 196, 478, 250, 92, 112, 128);
  rect(452, 196, 478, 202, 70, 88, 102);
  return encodePng(W, H, px);
}

const gardenPngBuffer = renderGardenPng();
gardenAttachment.size_bytes = gardenPngBuffer.length;
generatedAttachment.size_bytes = gardenPngBuffer.length;

// ── HTTP plumbing ───────────────────────────────────────────────────────────

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".ico": "image/x-icon",
  ".webmanifest": "application/manifest+json",
  ".woff2": "font/woff2",
  ".txt": "text/plain; charset=utf-8",
};

function sendJson(res, body, status = 200) {
  const data = JSON.stringify(body);
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
  });
  res.end(data);
}

/** Collect a JSON request body, then invoke cb (best-effort parse). */
function readBody(req, cb) {
  const chunks = [];
  req.on("data", (c) => chunks.push(c));
  req.on("end", () => {
    let body = {};
    try {
      body = JSON.parse(Buffer.concat(chunks).toString() || "{}");
    } catch {
      /* body optional in the mock */
    }
    cb(body);
  });
}

/** Reply after `ms` (simulating a slow lane); no-op if the client left. */
function sendJsonAfter(_req, res, ms, body) {
  setTimeout(() => {
    if (!res.writableEnded && !res.destroyed) sendJson(res, body);
  }, ms);
}

const hostFromUrl = (u) => {
  try {
    return new URL(u).host || "page";
  } catch {
    return "page";
  }
};

function sendSse(res, { events = [] } = {}) {
  res.writeHead(200, {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-store",
    Connection: "keep-alive",
  });
  res.write(": connected\n\n");
  const timers = [];
  for (const { delayMs, payload } of events) {
    timers.push(
      setTimeout(() => {
        res.write(`data: ${JSON.stringify(payload)}\n\n`);
      }, delayMs),
    );
  }
  const heartbeat = setInterval(() => res.write(": keepalive\n\n"), 15000);
  res.on("close", () => {
    clearInterval(heartbeat);
    for (const t of timers) clearTimeout(t);
  });
}

/** Stream a Forge chat reply the way the orchestrator does: the always-sent
 *  `{"forge":"status"}` frame, a `<think>…</think>` span split across two
 *  content chunks (the `</think>` tag straddles the chunk boundary, to
 *  exercise the UI's boundary-safe think parser), OpenAI-style answer deltas,
 *  then `[DONE]` and the terminal `{"forge":"done"}` frame — the shape
 *  ChatView consumes for both a POST turn and a GET /stream reattach.
 *  With `inflight: true` (reattach fixture) the frames generated "so far" —
 *  status, the whole think span, half the answer — replay in one instant
 *  burst, then the rest continues live. Closes when finished. */
function sendForgeStream(res, conversationId, text, { inflight = false } = {}) {
  res.writeHead(200, {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-store",
    Connection: "keep-alive",
  });
  res.write(": connected\n\n");

  const frame = (payload) => `data: ${JSON.stringify(payload)}\n\n`;
  const delta = (content) => frame({ choices: [{ delta: { content } }] });

  const thinking =
    "The user wants a quick, concrete answer. Sketch the steps, keep it " +
    "short, and close with a check-in question.";
  const words = text.split(" ");
  // Each entry: [sse payload, pause-before-writing ms].
  const queue = [
    [
      frame({
        forge: "status",
        conversation_id: conversationId,
        detail: "prompt sent to qwen3-coder-30b-a3b (llamacpp) — processing",
      }),
      60,
    ],
    // Think span in two chunks with the closing tag split mid-tag.
    [delta(`<think>${thinking}</thi`), 900],
    [delta("nk>"), 90],
    ...words.map((w, i) => [delta((i === 0 ? "" : " ") + w), i === 0 ? 350 : 70]),
  ];
  // Reattach: everything up to mid-answer replays instantly (buffered frames),
  // then a beat, then the remaining tokens stream live.
  const replayCount = inflight ? 3 + Math.ceil(words.length / 2) : 0;

  const timers = [];
  let i = 0;
  const step = () => {
    if (res.writableEnded || res.destroyed) return;
    if (i < queue.length) {
      res.write(queue[i][0]);
      i += 1;
      const next =
        i < replayCount ? 0 : i === replayCount ? 400 : queue[i]?.[1] ?? 70;
      timers.push(setTimeout(step, next));
      return;
    }
    res.write("data: [DONE]\n\n");
    res.write(
      frame({
        forge: "done",
        conversation_id: conversationId,
        assistant_message_id: 999,
      }),
    );
    res.end();
  };
  timers.push(setTimeout(step, inflight ? 0 : queue[0][1]));
  res.on("close", () => {
    for (const t of timers) clearTimeout(t);
  });
}

function handleApi(req, res, url) {
  const p = url.pathname.replace(/\/+$/, "") || "/";
  const q = url.searchParams;
  let m;

  // auth — multi-user: any credentials sign in as the demo profile
  if (p === "/api/auth/status") {
    return sendJson(res, {
      setup_required: SETUP_MODE,
      allow_registration: true,
      user_count: SETUP_MODE ? 0 : publicUsers.length,
    });
  }
  if (p === "/api/auth/login" && req.method === "POST") {
    return sendJson(res, { token: "demo-token", user: currentUser });
  }
  if (p === "/api/auth/register" && req.method === "POST") {
    return sendJson(res, { token: "demo-token", user: currentUser });
  }
  if (p === "/api/auth/check") {
    return sendJson(res, { ok: true, user: currentUser });
  }

  // users
  if (p === "/api/users/me") return sendJson(res, currentUser);
  if (p === "/api/users") return sendJson(res, publicUsers);

  // chat
  if (p === "/api/chat/status") {
    return sendJson(res, {
      ...chatStatus,
      auto: {
        available: true,
        router_model: routerModelSlug,
        router_ready: routerModelSlug === ROUTER_FIXTURE_SLUG,
      },
    });
  }
  // Server-side cancel of an in-flight generation. Mirrors the CHAT_INFLIGHT
  // fixture: with it set a job "exists" to cancel; otherwise 409.
  m = p.match(/^\/api\/chat\/conversations\/([^/]+)\/cancel$/);
  if (m && req.method === "POST") {
    if (process.env.CHAT_INFLIGHT === "1") {
      return sendJson(res, { ok: true, state: "done" });
    }
    return sendJson(
      res,
      { detail: "nothing is generating for this conversation" },
      409,
    );
  }
  // Which of the caller's conversations are generating right now (badges the
  // list). Empty by default so screenshots show a calm list.
  if (p === "/api/chat/active") return sendJson(res, []);
  // Re-attach to an in-flight generation. Idle by default (nothing running),
  // so opening a chat just shows its stored history. Flip CHAT_INFLIGHT=1 to
  // replay a short canned generation for the /stream + reattach path.
  m = p.match(/^\/api\/chat\/conversations\/([^/]+)\/stream$/);
  if (m) {
    if (process.env.CHAT_INFLIGHT === "1") {
      return sendForgeStream(
        res,
        m[1],
        "Here's a reply that was already streaming before you reattached — " +
          "the buffered half arrives instantly and the rest continues live.",
        { inflight: true },
      );
    }
    res.writeHead(200, {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-store",
      Connection: "keep-alive",
    });
    res.write(
      `data: ${JSON.stringify({ forge: "idle", conversation_id: m[1] })}\n\n`,
    );
    return res.end();
  }
  // A saved chat turn — streams a short canned reply then the forge:done frame,
  // mirroring the real server-side job (temporary chat is handled elsewhere).
  m = p.match(/^\/api\/chat\/conversations\/([^/]+)\/messages$/);
  if (m && req.method === "POST") {
    return sendForgeStream(res, m[1], "Sure — here's a quick answer for you");
  }
  if (p === "/api/chat/read_page" && req.method === "POST") {
    // Stealth fetches take 5-30s live; a short pause shows the pending chip.
    return readBody(req, (body) => {
      const url = body.url || "https://example.com/article";
      sendJsonAfter(req, res, 1500, {
        upload: {
          id: "up-page-1",
          filename: `${hostFromUrl(url)}.md`,
          kind: "text",
          mime: "text/markdown",
          size_bytes: 18244,
          generated: true,
          prompt: url,
        },
        url,
        mode_used: "stealth",
        truncated: false,
      });
    });
  }
  if (p === "/api/chat/image" && req.method === "POST") {
    // Happy path after a believable "diffusion" pause; the pending bubble
    // shows meanwhile. Serves the demo garden PNG like every other upload —
    // or inline as a data URI when the body asks for a temporary generation.
    return readBody(req, (body) => {
      const payload =
        body.temporary === true
          ? {
              upload: null,
              image_data_uri: `data:image/png;base64,${gardenPngBuffer.toString("base64")}`,
              conversation_id: null,
              user_message_id: null,
              assistant_message_id: null,
            }
          : {
              upload: generatedAttachment,
              conversation_id: null,
              user_message_id: null,
              assistant_message_id: null,
            };
      sendJsonAfter(req, res, 1500, payload);
    });
  }
  if (p === "/api/chat/conversations") {
    if (req.method === "POST") return sendJson(res, conversations[0]);
    const archived = q.get("archived") === "true";
    return sendJson(res, archived ? archivedConversations : conversations);
  }
  m = p.match(/^\/api\/chat\/conversations\/([^/]+)$/);
  if (m) {
    if (m[1] === "conv-1") return sendJson(res, conversationDetail);
    const c = [...conversations, ...archivedConversations].find(
      (x) => x.id === m[1],
    );
    if (!c) return sendJson(res, { detail: "conversation not found" }, 404);
    return sendJson(res, req.method === "GET" ? genericDetail(c) : c);
  }

  // memory
  if (p === "/api/memory") {
    if (req.method === "POST") return sendJson(res, memories[0]);
    return sendJson(res, memories);
  }

  // files — every upload id serves the demo garden photo
  m = p.match(/^\/api\/files\/([^/]+)$/);
  if (m && req.method === "GET") {
    res.writeHead(200, {
      "Content-Type": "image/png",
      "Cache-Control": "no-store",
    });
    return res.end(gardenPngBuffer);
  }

  // global SSE — one download.progress frame makes the 62% download live
  if (p === "/api/events/stream") {
    return sendSse(res, {
      events: [
        {
          delayMs: 250,
          payload: {
            kind: "download.progress",
            ts: Date.now() / 1000,
            model_id: 4,
            downloaded_gb: 5.8,
            total_gb: 9.4,
            pct: 62,
          },
        },
      ],
    });
  }

  // per-session SSE
  m = p.match(/^\/api\/sessions\/([^/]+)\/events$/);
  if (m) return sendSse(res);

  if (p === "/api/system/stats") return sendJson(res, systemStats);
  if (p === "/api/engines") return sendJson(res, enginesStatus);
  if (p === "/api/engines/load" && req.method === "POST") {
    return sendJson(res, { lease: lease0 });
  }
  if (p === "/api/engines/unload" && req.method === "POST") {
    return sendJson(res, { leases: [lease0, lease1] });
  }

  if (p === "/api/models") return sendJson(res, models);
  if (p === "/api/models/suggestions") return sendJson(res, suggestions);
  if (p === "/api/models/search") {
    const kind = q.get("kind") === "image" ? "image" : "text";
    return sendJson(res, hubSearchResults[kind]);
  }
  if (p === "/api/models/search/add" && req.method === "POST") {
    return sendJson(res, models[0]);
  }
  m = p.match(/^\/api\/models\/(\d+)\/refit$/);
  if (m && req.method === "POST") {
    const model = models.find((x) => x.id === Number(m[1]));
    if (!model) return sendJson(res, { detail: "model not found" }, 404);
    if (model.engine !== "airllm") {
      return sendJson(
        res,
        { detail: `re-resolution keeps the ${model.engine} lane — no alternative artifact exists for this hardware` },
        409,
      );
    }
    return sendJson(res, {
      ...model,
      engine: "llamacpp",
      quant: "gguf-q4_k_m",
      hf_repo: `bartowski/${model.display_name.replaceAll(" ", "-")}-GGUF`,
      file_path: "model-q4_k_m.gguf",
      status: "downloading",
      note: "Refitted from the airllm lane: now llamacpp (gguf-q4_k_m).",
    });
  }
  m = p.match(/^\/api\/models\/(\d+)\/thinking\/(auto|off|low|high)$/);
  if (m) {
    const model = models.find((x) => x.id === Number(m[1]));
    return sendJson(res, {
      family: model?.family ?? "qwen3",
      level: m[2],
      system: "",
      user_suffix: "",
    });
  }

  if (p === "/api/sessions") return sendJson(res, sessions);

  m = p.match(/^\/api\/sessions\/([^/]+)(\/.*)?$/);
  if (m) {
    const session = sessions.find((s) => s.id === m[1]);
    if (!session) return sendJson(res, { detail: "session not found" }, 404);
    const sub = m[2] ?? "";
    if (sub === "") return sendJson(res, session);
    if (sub === "/opencode/session") {
      if (req.method === "POST") return sendJson(res, ocSessions[0]);
      return sendJson(res, ocSessions);
    }
    const oc = sub.match(/^\/opencode\/session\/([^/]+)\/message$/);
    if (oc) {
      if (req.method === "POST") return sendJson(res, { ok: true });
      return sendJson(res, ocMessages);
    }
    if (sub === "/files") {
      const path = (q.get("path") ?? "").replace(/^\.?\/?/, "").replace(/\/+$/, "");
      const entries = fileTree[path] ?? [];
      return sendJson(res, { path: path || ".", entries });
    }
    if (sub === "/file") {
      const path = (q.get("path") ?? "").replace(/^\.?\/?/, "");
      return sendJson(res, {
        path,
        content: fileContents[path] ?? `// ${path}\n// (demo content)\n`,
      });
    }
    if (sub === "/git/status") return sendJson(res, gitStatus);
    if (sub === "/git/log") return sendJson(res, gitLog);
    if (sub === "/git/diff") return sendJson(res, gitDiff);
    if (sub === "/tasks") {
      if (req.method === "POST") return sendJson(res, tasks[1]);
      return sendJson(res, session.id === "sess-1" ? tasks : []);
    }
    // start/stop/delete etc.
    return sendJson(res, session);
  }

  if (p === "/api/tasks") return sendJson(res, tasks);
  if (p === "/api/skills") return sendJson(res, skills);
  if (p === "/api/skills/catalog") return sendJson(res, skillCatalog);
  if (p === "/api/skills/catalog/install" && req.method === "POST") {
    return readBody(req, (body) => {
      const entry = skillCatalog.find((s) => s.name === body.name);
      sendJson(res, {
        id: 900 + Math.floor(Math.random() * 90),
        name: body.name || "installed-skill",
        description: entry?.description ?? "",
        source_url: ECC_REPO,
        path: `/skills/${body.name}`,
        installed_at: new Date().toISOString(),
        enabled: true,
      });
    });
  }
  if (p === "/api/skills/pack/scan" && req.method === "POST") {
    return sendJsonAfter(req, res, 900, packScan); // git clone + scan is slow
  }
  if (p === "/api/skills/pack/install" && req.method === "POST") {
    return readBody(req, (body) => {
      const subdirs = Array.isArray(body.subdirs) ? body.subdirs : [];
      sendJsonAfter(req, res, 1200, {
        installed: subdirs.map((s) => String(s).split("/").pop()),
        skipped: [],
        note: "bulk-imported skills start disabled — enable the ones you want sessions to load",
      });
    });
  }
  if (p === "/api/sandbox/status") {
    return sendJson(res, {
      enabled: true,
      healthy: true,
      detail: "ok",
      url: "http://forge-smolvm:8080",
    });
  }
  if (p === "/api/sandbox/run" && req.method === "POST") {
    return readBody(req, (body) => {
      const lang = String(body.language || "").toLowerCase();
      const result = lang.startsWith("py")
        ? {
            stdout: "Hello from the sandbox!\nsquares: [0, 1, 4, 9, 16]\n",
            stderr: "",
            exit_code: 0,
            timed_out: false,
            duration_ms: 812,
          }
        : {
            stdout: "ok\n",
            stderr: "",
            exit_code: 0,
            timed_out: false,
            duration_ms: 640,
          };
      sendJsonAfter(req, res, 700, result);
    });
  }
  if (p === "/api/connectors") {
    return sendJson(
      res,
      connectors.map((c) => ({ ...c, oauth: connectorOauthView(c.kind) })),
    );
  }

  // per-user OAuth sign-in
  if (p === "/api/connectors/oauth/providers") {
    return sendJson(
      res,
      Object.fromEntries(
        Object.keys(OAUTH_PROVIDER_META).map((k) => [k, oauthProviderStatus(k)]),
      ),
    );
  }
  m = p.match(/^\/api\/connectors\/([^/]+)\/oauth\/start$/);
  if (m && req.method === "POST") {
    const kind = m[1];
    const status = oauthProviderStatus(kind);
    if (!status.supported) {
      return sendJson(res, { detail: `${kind} does not support OAuth sign-in` }, 404);
    }
    if (!status.ready) {
      return sendJson(
        res,
        {
          detail:
            `${OAUTH_PROVIDER_META[kind].label} sign-in isn't configured — ` +
            "an admin must add a client ID on the Settings page. " +
            OAUTH_PROVIDER_META[kind].setup_note,
        },
        409,
      );
    }
    return readBody(req, (body) => {
      const flowId = `flow-${++oauthFlowSeq}`;
      oauthFlows.set(flowId, { kind, polls: 0 });
      if (status.method === "device") {
        return sendJson(res, {
          flow: "device",
          flow_id: flowId,
          user_code: "ABCD-1234",
          verification_uri: "https://github.com/login/device",
          interval: 2,
          expires_in: 900,
        });
      }
      // Code flow: "authorize" straight back into the SPA so the whole
      // redirect → /oauth/callback → exchange path works offline.
      const redirect = body.redirect_uri || "/oauth/callback";
      sendJson(res, {
        flow: "code",
        flow_id: flowId,
        authorize_url: `${redirect}?code=fake&state=${flowId}`,
      });
    });
  }
  m = p.match(/^\/api\/connectors\/([^/]+)\/oauth\/poll$/);
  if (m && req.method === "POST") {
    const kind = m[1];
    return readBody(req, (body) => {
      const flow = oauthFlows.get(body.flow_id);
      if (!flow || flow.kind !== kind) {
        return sendJson(res, { detail: "unknown or expired sign-in flow" }, 404);
      }
      flow.polls += 1;
      if (flow.polls < 2) return sendJson(res, { status: "pending" });
      oauthFlows.delete(body.flow_id);
      oauthConnections[kind] = {
        account: "chris-dev",
        connected_at: Date.now() / 1000,
      };
      sendJson(res, { status: "connected", account: "chris-dev" });
    });
  }
  m = p.match(/^\/api\/connectors\/([^/]+)\/oauth\/exchange$/);
  if (m && req.method === "POST") {
    const kind = m[1];
    return readBody(req, (body) => {
      const flow = oauthFlows.get(body.state);
      if (!flow || flow.kind !== kind || !body.code) {
        return sendJson(res, { detail: "unknown or expired sign-in flow" }, 404);
      }
      oauthFlows.delete(body.state);
      const account = kind === "hugging-face" ? "chris-hf" : "chris-dev";
      oauthConnections[kind] = { account, connected_at: Date.now() / 1000 };
      sendJson(res, { status: "connected", account });
    });
  }
  m = p.match(/^\/api\/connectors\/([^/]+)\/oauth$/);
  if (m && req.method === "DELETE") {
    oauthConnections[m[1]] = null;
    return sendJson(res, { ok: true });
  }
  if (p === "/api/connectors/github/repos") {
    if (!oauthConnections.github) {
      return sendJson(
        res,
        {
          detail:
            "GitHub isn't connected — sign in (or paste a token) on the " +
            "Connectors page to pick from your repositories.",
        },
        409,
      );
    }
    const needle = (q.get("q") ?? "").trim().toLowerCase();
    return sendJson(
      res,
      needle
        ? githubRepos.filter((r) => r.full_name.toLowerCase().includes(needle))
        : githubRepos,
    );
  }

  if (p === "/api/settings") {
    if (req.method === "PATCH") {
      return readBody(req, (body) => {
        if (typeof body.headroom_enabled === "boolean") {
          settings.headroom = {
            ...settings.headroom,
            enabled: body.headroom_enabled,
            healthy: body.headroom_enabled ? true : null,
          };
        }
        if (typeof body.chat_system_prompt === "string") {
          // "" (or the default verbatim) restores the built-in default.
          const value = body.chat_system_prompt.trim();
          chatSystemPromptOverride =
            value === DEFAULT_CHAT_SYSTEM_PROMPT.trim() ? "" : value;
        }
        if (typeof body.router_model_slug === "string") {
          routerModelSlug = body.router_model_slug.trim();
        }
        for (const key of [
          "github_oauth_client_id",
          "hf_oauth_client_id",
          "hf_oauth_client_secret",
        ]) {
          if (typeof body[key] === "string") oauthSettings[key] = body[key];
        }
        sendJson(res, {
          ...settings,
          ...chatSystemPromptView(),
          ...routerSettingsView(),
          oauth: settingsOauthView(),
        });
      });
    }
    return sendJson(res, {
      ...settings,
      ...chatSystemPromptView(),
      ...routerSettingsView(),
      oauth: settingsOauthView(),
    });
  }
  if (p === "/api/health") return sendJson(res, { ok: true });

  // benign default so no page ever shows an error state during capture
  return sendJson(res, req.method === "GET" ? [] : { ok: true });
}

function serveStatic(res, pathname) {
  let rel = normalize(decodeURIComponent(pathname)).replace(/^([/\\])+/, "");
  let file = join(DIST, rel);
  if (!file.startsWith(DIST)) file = join(DIST, "index.html");
  if (!existsSync(file) || statSync(file).isDirectory()) {
    file = extname(file) ? null : join(DIST, "index.html"); // SPA fallback
  }
  if (!file || !existsSync(file)) {
    res.writeHead(404, { "Content-Type": "text/plain" });
    return res.end("not found");
  }
  res.writeHead(200, {
    "Content-Type": MIME[extname(file)] ?? "application/octet-stream",
    "Cache-Control": "no-store",
  });
  res.end(readFileSync(file));
}

const server = createServer((req, res) => {
  const url = new URL(req.url, `http://127.0.0.1:${PORT}`);
  if (url.pathname.startsWith("/api/")) return handleApi(req, res, url);
  return serveStatic(res, url.pathname === "/" ? "/index.html" : url.pathname);
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`mock forge on http://127.0.0.1:${PORT} (serving ${DIST})`);
});

for (const sig of ["SIGINT", "SIGTERM"]) {
  process.on(sig, () => {
    server.closeAllConnections?.();
    server.close(() => process.exit(0));
    setTimeout(() => process.exit(0), 500).unref();
  });
}
