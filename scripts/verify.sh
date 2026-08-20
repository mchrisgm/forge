#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Forge post-bring-up reconcile + verification (Linux / macOS).
#
# The bash peer of setup.ps1's sweep + verify steps, shared by `make up` and
# scripts/setup.sh (re-runnable standalone any time):
#   1. sweep orchestrator-spawned containers left on outdated images —
#      compose never recreates them (they're started via the docker socket,
#      not compose), so after a rebuild they'd keep serving old code
#   2. remove engine-lane containers started by a stray
#      `compose --profile engines up` (lanes belong to the orchestrator)
#   3. verify every always-on service is running, wait for the
#      orchestrator's healthcheck, probe the gateway on :8080, and check the
#      locally-built images (sessions + local engine lanes) exist
#
# Exits non-zero with a list of everything that failed. GPU overlay is
# auto-detected like the Makefile/setup.sh; force CPU-only with
# FORGE_NO_GPU=1.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$SCRIPT_DIR/.."

info() { printf '  %s\n' "$*"; }
bold() { printf '\n\033[1m%s\033[0m\n' "$*"; }

COMPOSE=(docker compose -f docker-compose.yml)
if [ "${FORGE_NO_GPU:-0}" = 0 ] && command -v nvidia-smi >/dev/null 2>&1; then
  COMPOSE+=(-f docker-compose.gpu.yml)
fi

# ── 1+2. sweep stale / stray lane containers ────────────────────────────────
bold "Sweeping orchestrator-spawned containers left on outdated images"
stale_removed=()
for label in forge.engine forge.router forge.session; do
  for id in $(docker ps -q --filter "label=$label"); do
    name=$(docker inspect -f '{{.Name}}' "$id" 2>/dev/null | sed 's|^/||') || continue
    ref=$(docker inspect -f '{{.Config.Image}}' "$id" 2>/dev/null || true)
    running_id=$(docker inspect -f '{{.Image}}' "$id" 2>/dev/null || true)
    current_id=""
    if [ -n "$ref" ]; then
      current_id=$(docker image inspect -f '{{.Id}}' "$ref" 2>/dev/null || true)
    fi
    if [ -z "$current_id" ] || [ "$running_id" != "$current_id" ]; then
      docker rm -f "$id" >/dev/null 2>&1 || true
      stale_removed+=("$name")
      info "removed $name (its image was rebuilt; the container ran the old build)"
    fi
  done
done
# compose ps only matches compose-created containers (runtime labels), never
# the orchestrator's forge-engine-* ones, so this cannot touch a healthy lane.
for id in $("${COMPOSE[@]}" --profile engines ps -q llamacpp vllm sglang tabby airllm imagegen 2>/dev/null); do
  [ -n "$id" ] || continue
  name=$(docker inspect -f '{{.Name}}' "$id" 2>/dev/null | sed 's|^/||') || continue
  docker rm -f "$id" >/dev/null 2>&1 || true
  stale_removed+=("$name")
  info "removed $name (engine lanes belong to the orchestrator, not compose up)"
done
if [ ${#stale_removed[@]} -gt 0 ]; then
  # The orchestrator re-adopts lanes blindly on boot and never health-checks
  # them on its own — restart it so it drops the now-dead leases.
  "${COMPOSE[@]}" restart orchestrator
  info "orchestrator restarted to drop stale leases — re-load models from the Models page"
else
  info "none found — all lanes current"
fi

# ── 3. verify ───────────────────────────────────────────────────────────────
bold "Verifying the stack"
failed=()
# ui is a one-shot bundle builder (exits 0 after copying the PWA into the
# ui-dist volume); gateway's depends_on gates on that, so the gateway
# answering below also proves ui completed.
for svc in gateway orchestrator searxng mcp-playwright mcp-scrapling headroom; do
  cid=$("${COMPOSE[@]}" ps -q "$svc" 2>/dev/null || true)
  state=""
  if [ -n "$cid" ]; then
    state=$(docker inspect -f '{{.State.Status}}' "$cid" 2>/dev/null || true)
  fi
  if [ "$state" = "running" ]; then
    info "$svc running"
  else
    failed+=("$svc is ${state:-not created}")
  fi
done

# The orchestrator carries the stack's only healthcheck — wait for it.
orch_id=$("${COMPOSE[@]}" ps -q orchestrator 2>/dev/null || true)
health="not created"
if [ -n "$orch_id" ]; then
  for _ in $(seq 1 30); do
    health=$(docker inspect -f '{{.State.Health.Status}}' "$orch_id" 2>/dev/null || echo unknown)
    [ "$health" = "healthy" ] && break
    sleep 3
  done
fi
if [ "$health" = "healthy" ]; then
  info "orchestrator healthcheck: healthy"
else
  failed+=("orchestrator healthcheck: $health")
fi

# End to end: the gateway must answer on :8080.
probe() {
  if command -v curl >/dev/null 2>&1; then
    curl -fsS -o /dev/null -m 5 http://localhost:8080/
  elif command -v wget >/dev/null 2>&1; then
    wget -q -O /dev/null -T 5 http://localhost:8080/
  else
    return 2  # no probe tool — treated as a soft skip below
  fi
}
gateway_up=0
probe_missing=0
for _ in $(seq 1 10); do
  rc=0
  probe || rc=$?
  if [ "$rc" = 0 ]; then
    gateway_up=1
    break
  fi
  if [ "$rc" = 2 ]; then
    probe_missing=1
    break
  fi
  sleep 2
done
if [ "$gateway_up" = 1 ]; then
  info "gateway answers on http://localhost:8080"
elif [ "$probe_missing" = 1 ]; then
  info "no curl/wget on this host — skipped the gateway HTTP probe"
else
  failed+=("gateway did not answer HTTP 200 on :8080")
fi

# Images the orchestrator spawns on demand (sessions + local engine lanes)
# must exist locally or the first session / model load dies at spawn time.
for img in forge-session-runner forge-airllm forge-imagegen; do
  if docker image inspect -f '{{.Id}}' "$img" >/dev/null 2>&1; then
    info "image $img built"
  else
    failed+=("image $img is missing")
  fi
done

if [ ${#failed[@]} -gt 0 ]; then
  "${COMPOSE[@]}" ps || true
  printf '\n\033[31mStack verification failed:\033[0m\n' >&2
  printf '    %s\n' "${failed[@]}" >&2
  exit 1
fi
bold "Stack verified — all services up, orchestrator healthy, images built."
