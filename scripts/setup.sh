#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Forge first-run setup (Linux / macOS).
#
# One command from a fresh clone to a running stack — the same steps `make up`
# runs, for hosts without `make`:
#   1. preflight the host (docker + compose v2 are required; GPU/disk/KVM warn)
#   2. create .env from .env.example and generate its secrets (idempotent)
#   3. auto-include the GPU overlay when an NVIDIA GPU is present
#   4. build + start the stack, build the session/engine images, prefetch the
#      big engine images
#
# Re-runnable: an existing .env is left alone and compose reconciles in place.
#
# Usage: scripts/setup.sh [--sandbox] [--skip-pull] [--no-gpu] [--help]
#   --sandbox    also build + start the smolvm sandbox lane (needs /dev/kvm)
#   --skip-pull  skip prefetching the llama.cpp/vLLM engine images
#   --no-gpu     force CPU-only bring-up even if an NVIDIA GPU is detected
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
cd "$REPO_ROOT"

WANT_SANDBOX=0
SKIP_PULL=0
FORCE_NO_GPU=0
for arg in "$@"; do
  case "$arg" in
    --sandbox)   WANT_SANDBOX=1 ;;
    --skip-pull) SKIP_PULL=1 ;;
    --no-gpu)    FORCE_NO_GPU=1 ;;
    -h|--help)
      sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

bold() { printf '\n\033[1m%s\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }
die()  { printf '\n\033[31mSetup aborted:\033[0m %s\n' "$*" >&2; exit 1; }

# ── 1. preflight ────────────────────────────────────────────────────────────
bold "[1/5] Checking host prerequisites"
if ! command -v docker >/dev/null 2>&1; then
  die "docker not found — install Docker Engine (Linux) or Docker Desktop (macOS): https://docs.docker.com/get-docker/"
fi
if ! docker info >/dev/null 2>&1; then
  die "the docker daemon is unreachable — start Docker, and on Linux add yourself to the docker group: sudo usermod -aG docker \$USER (then log out and back in)"
fi
if ! docker compose version >/dev/null 2>&1; then
  die "docker compose v2 not found — install the compose plugin: https://docs.docker.com/compose/install/"
fi
info "docker + compose v2 OK"
# The canonical checker prints GPU/disk/KVM advisories; it never hard-fails on
# those, so a nonzero exit here means a real docker/compose problem.
if [ -x scripts/preflight.sh ]; then
  scripts/preflight.sh || die "preflight reported a blocking problem (see above)"
fi

# ── 2. .env + secrets ───────────────────────────────────────────────────────
bold "[2/5] Preparing .env"
if [ ! -f .env ]; then
  cp .env.example .env
  info "created .env from .env.example"
else
  info ".env already exists — leaving it untouched"
fi

gen_secret() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  elif [ -r /dev/urandom ]; then
    LC_ALL=C tr -dc 'a-f0-9' < /dev/urandom | head -c 64
  else
    date +%s%N | shasum -a 256 2>/dev/null | head -c 64
  fi
}

# Set KEY to a fresh secret only when it is missing or blank in .env.
ensure_secret() {
  local key="$1"
  if grep -qE "^${key}=[^[:space:]#]" .env; then
    return
  fi
  local value
  value=$(gen_secret)
  # Drop any existing blank line for this key, then append the filled one.
  # (.bak + rm keeps this portable across GNU and BSD/macOS sed.)
  sed -i.bak "/^${key}=/d" .env && rm -f .env.bak
  printf '%s=%s\n' "$key" "$value" >> .env
  info "generated $key"
}
ensure_secret SEARXNG_SECRET
ensure_secret FORGE_SECRET_KEY

# ── 3. GPU overlay detection ────────────────────────────────────────────────
bold "[3/5] Selecting compose files"
COMPOSE=(docker compose -f docker-compose.yml)
if [ "$FORCE_NO_GPU" = 0 ] && command -v nvidia-smi >/dev/null 2>&1; then
  COMPOSE+=(-f docker-compose.gpu.yml)
  info "NVIDIA GPU detected — including docker-compose.gpu.yml (GPU stats + leases)"
else
  info "CPU-only bring-up (no GPU overlay) — the stack runs, engine loads need a GPU"
fi

# ── 4. build + start ────────────────────────────────────────────────────────
bold "[4/5] Building and starting the stack"
"${COMPOSE[@]}" up -d --build
# Profile-gated images are invisible to a plain `up` — build them so the
# orchestrator can spawn sessions and the local engine lanes.
"${COMPOSE[@]}" --profile build-only build session-runner
"${COMPOSE[@]}" --profile engines build airllm imagegen
if [ "$SKIP_PULL" = 0 ]; then
  info "prefetching the llama.cpp/vLLM engine images (skip next time with --skip-pull)..."
  "${COMPOSE[@]}" --profile engines pull llamacpp vllm || \
    info "engine image prefetch skipped/failed — they'll pull on first model load"
fi

# ── 5. optional sandbox lane ────────────────────────────────────────────────
bold "[5/5] Optional sandbox lane"
if [ "$WANT_SANDBOX" = 1 ]; then
  if [ ! -e /dev/kvm ]; then
    info "WARNING: /dev/kvm not found — smolvm microVMs will not start on this host"
  fi
  "${COMPOSE[@]}" --profile sandbox up -d --build smolvm
  info "sandbox lane (smolvm) built and started"
else
  info "skipped — run 'scripts/setup.sh --sandbox' (needs /dev/kvm) to enable the 'run code' lane"
fi

host_ip=$(hostname -I 2>/dev/null | awk '{print $1}')
[ -n "${host_ip:-}" ] || host_ip="<host-ip>"
bold "Forge is up."
cat <<EOF
  Open  http://${host_ip}:8080  from any device on your LAN.
  The first visitor creates the admin profile; everyone else registers there.
  On first boot the model catalog is seeded automatically — open the Models
  page to download weights, then load an engine.

  Handy commands (all via docker compose, no make needed):
    docker compose logs -f --tail=200      # tail logs
    docker compose down                    # stop (volumes kept)
    scripts/setup.sh --sandbox             # add the code-run sandbox lane
EOF
