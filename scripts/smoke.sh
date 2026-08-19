#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Forge end-to-end smoke test (PLAN §12, M6 acceptance gate).
#
# Flow: compose up → gateway health → login → pick a ready model → load engine
#       → create session → run a task → assert the file the agent created
#       → cleanup (delete session, unload engine).
#
# Requirements: docker (compose v2), curl, jq, a .env at the repo root, and a
# model already downloaded (status=ready) — run `make seed`, then download one
# from the Models page. Set SMOKE_SKIP_MODEL=1 to pass with infra-only checks
# when no model is downloaded yet (the engine/session/task steps are skipped).
#
# Re-runnable: every run uses a fresh session; the engine is (re)loaded only
# when the picked model isn't already serving. On any failure the tail of the
# compose logs (and the engine container log, if any) is dumped.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

cd "$(dirname "$0")/.."

BASE_URL="${FORGE_SMOKE_URL:-http://localhost:8080}"
API="$BASE_URL/api"

HEALTH_TIMEOUT_S=180     # gateway + orchestrator boot
ENGINE_TIMEOUT_S=900     # 15 min — big GGUFs take a while to load
SESSION_TIMEOUT_S=300    # container spawn + opencode serve boot
TASK_TIMEOUT_S=1200      # 20 min — local models are not fast
POLL_S=5

TOKEN=""
SESSION_ID=""
MODEL_ID=""
CLEANUP_DONE=0
STACK_UP=0

# ── Helpers ──────────────────────────────────────────────────────────────────

banner() { printf '\n━━━ %s ━━━\n' "$*"; }
info()   { printf '    %s\n' "$*"; }
fail()   { printf 'SMOKE FAIL: %s\n' "$*" >&2; exit 1; }

api_get() { # <path>
  curl -fsS --max-time 30 -H "Authorization: Bearer ${TOKEN}" "${API}$1"
}

api_post() { # <path> <json-body>
  curl -fsS --max-time 60 -X POST \
    -H "Authorization: Bearer ${TOKEN}" -H 'Content-Type: application/json' \
    -d "$2" "${API}$1"
}

api_delete() { # <path>
  curl -fsS --max-time 60 -X DELETE -H "Authorization: Bearer ${TOKEN}" "${API}$1"
}

dump_logs() {
  banner "Failure diagnostics"
  echo "--- docker ps (forge containers) ---"
  docker ps --format '{{.Names}}\t{{.Status}}' | grep -i forge || true
  echo "--- docker compose logs (tail) ---"
  docker compose logs --no-color --tail=120 || true
  # Engine containers are started by the orchestrator, not compose:
  for c in $(docker ps -a --format '{{.Names}}' | grep '^forge-engine-' || true); do
    echo "--- docker logs ${c} (tail) ---"
    docker logs --tail 60 "$c" 2>&1 || true
  done
}

cleanup() {
  [ "$CLEANUP_DONE" = 1 ] && return 0
  CLEANUP_DONE=1
  banner "Cleanup"
  if [ -n "$SESSION_ID" ] && [ -n "$TOKEN" ]; then
    info "deleting session ${SESSION_ID}"
    api_delete "/sessions/${SESSION_ID}" >/dev/null || true
  fi
  if [ -n "$TOKEN" ]; then
    info "unloading engine"
    api_post "/engines/unload" '{}' >/dev/null || true
  fi
}

# ERR reports where; EXIT (below) dumps diagnostics and cleans up so that
# both `set -e` aborts and explicit `fail` exits are handled the same way.
trap 'echo "SMOKE FAIL: command failed at line $LINENO" >&2' ERR

on_exit() {
  local code=$?
  if [ "$code" -ne 0 ] && [ "$STACK_UP" = 1 ]; then
    dump_logs
    cleanup
  fi
}
trap on_exit EXIT

# ── Step 1: prerequisites ───────────────────────────────────────────────────

banner "1/9 Prerequisites"
for cmd in docker curl jq; do
  command -v "$cmd" >/dev/null 2>&1 || fail "'$cmd' is required but not installed"
done
docker compose version >/dev/null 2>&1 || fail "docker compose v2 is required"
[ -f .env ] || fail "no .env at repo root — run: cp .env.example .env"

# Forge is multi-user: the smoke test registers (or reuses) its own profile.
SMOKE_USER="${SMOKE_USER:-smoke}"
SMOKE_PASSWORD="${SMOKE_PASSWORD:-smoke-ci-password}"
info "docker, curl, jq present; .env found"

# ── Step 2: bring the stack up ──────────────────────────────────────────────

banner "2/9 docker compose up"
docker compose up -d --build
STACK_UP=1
info "building session-runner image (used for per-session containers)"
docker compose --profile build-only build session-runner

# ── Step 3: gateway health ──────────────────────────────────────────────────

banner "3/9 Waiting for gateway health (${BASE_URL}/api/health)"
deadline=$(( $(date +%s) + HEALTH_TIMEOUT_S ))
until curl -fsS --max-time 5 "${API}/health" 2>/dev/null | jq -e '.status == "ok"' >/dev/null 2>&1; do
  [ "$(date +%s)" -lt "$deadline" ] || fail "gateway not healthy after ${HEALTH_TIMEOUT_S}s"
  sleep 3
done
info "gateway is healthy"

# ── Step 4: login ───────────────────────────────────────────────────────────

banner "4/9 Register/login the smoke profile"
register_body=$(jq -n --arg u "$SMOKE_USER" --arg p "$SMOKE_PASSWORD" \
  '{username: $u, password: $p, display_name: "Smoke Test"}')
login_body=$(jq -n --arg u "$SMOKE_USER" --arg p "$SMOKE_PASSWORD" \
  '{username: $u, password: $p}')
# Try register first (fresh install / first run); fall back to login (re-run).
TOKEN=$(curl -fsS --max-time 15 -X POST -H 'Content-Type: application/json' \
  -d "$register_body" "${API}/auth/register" 2>/dev/null | jq -r '.token // empty' || true)
if [ -z "$TOKEN" ]; then
  TOKEN=$(curl -fsS --max-time 15 -X POST -H 'Content-Type: application/json' \
    -d "$login_body" "${API}/auth/login" | jq -r '.token // empty')
fi
[ -n "$TOKEN" ] || fail "could not register or log in the smoke profile"
info "authenticated as profile '$SMOKE_USER'"

# ── Step 5: pick a ready model ──────────────────────────────────────────────

banner "5/9 Picking a ready model"
models_json=$(api_get "/models")
# AirLLM is chat-only (PLAN §6.2) and cannot power a session — exclude it.
MODEL_ID=$(jq -r '[.[] | select(.status == "ready" and .engine != "airllm")] | sort_by(.size_gb) | .[0].id // empty' <<<"$models_json")
if [ -z "$MODEL_ID" ]; then
  echo
  echo "No downloaded model available (need status=ready, engine llamacpp/vllm)."
  echo "Fix: run 'make seed', then download a model:"
  echo "  - UI:  open ${BASE_URL} -> Models -> Download"
  echo "  - API: curl -X POST -H \"Authorization: Bearer \$TOKEN\" ${API}/models/<id>/download"
  if [ "${SMOKE_SKIP_MODEL:-0}" = 1 ]; then
    echo "SMOKE_SKIP_MODEL=1 set — infra smoke PASSED, model/session steps skipped."
    exit 0
  fi
  fail "no ready model (set SMOKE_SKIP_MODEL=1 to accept an infra-only pass)"
fi
MODEL_NAME=$(jq -r --argjson id "$MODEL_ID" '.[] | select(.id == $id) | .display_name' <<<"$models_json")
info "model #${MODEL_ID}: ${MODEL_NAME} (smallest ready model, for speed)"

# ── Step 6: load engine and wait for the lease to be ready ─────────────────

banner "6/9 Loading engine (timeout $((ENGINE_TIMEOUT_S / 60))m)"
lease_state=$(api_get "/engines" | jq -r '.lease.state // "none"')
lease_model=$(api_get "/engines" | jq -r '.lease.model_id // empty')
if [ "$lease_state" = "ready" ] && [ "$lease_model" = "$MODEL_ID" ]; then
  info "engine already serving model #${MODEL_ID} — reusing"
else
  if [ "$lease_state" != "none" ]; then
    info "releasing current lease (state=${lease_state}, model=${lease_model:-?})"
    api_post "/engines/unload" '{}' >/dev/null
  fi
  api_post "/engines/load" "{\"model_id\": ${MODEL_ID}}" >/dev/null
  deadline=$(( $(date +%s) + ENGINE_TIMEOUT_S ))
  while :; do
    engines_json=$(api_get "/engines" || true)
    state=$(jq -r '.lease.state // "none"' <<<"$engines_json" 2>/dev/null || echo "unknown")
    case "$state" in
      ready)  break ;;
      failed) echo "$engines_json" | jq -r '.lease.error' >&2
              fail "engine failed to load (error above — often VRAM OOM)" ;;
      *)      info "lease state: ${state} ..." ;;
    esac
    [ "$(date +%s)" -lt "$deadline" ] || fail "engine not ready after ${ENGINE_TIMEOUT_S}s"
    sleep "$POLL_S"
  done
  info "engine ready"
fi

# ── Step 7: create a session and wait for it to run ────────────────────────

banner "7/9 Creating session"
SESSION_NAME="smoke-$(date +%Y%m%d-%H%M%S)"
SESSION_ID=$(api_post "/sessions" \
  "$(jq -n --arg n "$SESSION_NAME" --argjson m "$MODEL_ID" '{name: $n, model_id: $m}')" \
  | jq -r '.id')
[ -n "$SESSION_ID" ] && [ "$SESSION_ID" != null ] || fail "session creation returned no id"
info "session ${SESSION_ID} (${SESSION_NAME})"

deadline=$(( $(date +%s) + SESSION_TIMEOUT_S ))
while :; do
  session_json=$(api_get "/sessions/${SESSION_ID}" || true)
  state=$(jq -r '.state // "unknown"' <<<"$session_json" 2>/dev/null || echo "unknown")
  case "$state" in
    running) break ;;
    error)   jq -r '.last_error' <<<"$session_json" >&2
             fail "session entered error state" ;;
    *)       info "session state: ${state} ..." ;;
  esac
  [ "$(date +%s)" -lt "$deadline" ] || fail "session not running after ${SESSION_TIMEOUT_S}s"
  sleep "$POLL_S"
done
info "session running"

# ── Step 8: run the task and wait for completion ───────────────────────────

banner "8/9 Running agent task (timeout $((TASK_TIMEOUT_S / 60))m)"
TASK_PROMPT="Create a file named smoke.txt containing exactly FORGE_SMOKE_OK"
TASK_ID=$(api_post "/sessions/${SESSION_ID}/tasks" \
  "$(jq -n --arg p "$TASK_PROMPT" '{prompt: $p}')" | jq -r '.id')
[ -n "$TASK_ID" ] && [ "$TASK_ID" != null ] || fail "task creation returned no id"
info "task #${TASK_ID}: \"${TASK_PROMPT}\""

deadline=$(( $(date +%s) + TASK_TIMEOUT_S ))
while :; do
  task_json=$(api_get "/tasks" | jq --argjson id "$TASK_ID" '.[] | select(.id == $id)' || true)
  state=$(jq -r '.state // "unknown"' <<<"$task_json" 2>/dev/null || echo "unknown")
  case "$state" in
    done)   break ;;
    failed) jq -r '.result' <<<"$task_json" >&2
            fail "task failed (agent output above)" ;;
    *)      info "task state: ${state} ..." ;;
  esac
  [ "$(date +%s)" -lt "$deadline" ] || fail "task not done after ${TASK_TIMEOUT_S}s"
  sleep "$POLL_S"
done
info "task done"

# ── Step 9: assert the file the agent created ──────────────────────────────

banner "9/9 Asserting smoke.txt in the session workspace"
content=""
for attempt in 1 2 3 4 5; do
  content=$(api_get "/sessions/${SESSION_ID}/file?path=smoke.txt" | jq -r '.content // empty' || true)
  [ -n "$content" ] && break
  info "file not readable yet (attempt ${attempt}/5), retrying..."
  sleep 3
done
if ! grep -q "FORGE_SMOKE_OK" <<<"$content"; then
  echo "smoke.txt content was: '${content}'" >&2
  fail "smoke.txt does not contain FORGE_SMOKE_OK"
fi
info "smoke.txt contains FORGE_SMOKE_OK"

cleanup

banner "SMOKE PASSED"
echo "Full loop verified: compose up → health → login → engine load → session → task → file."
