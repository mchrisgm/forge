#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Forge preflight — host sanity checks before `make up`.
#
# Exit non-zero ONLY for docker/compose problems (the stack cannot come up
# without them). Everything else — no GPU, low disk, no KVM — is a warning or
# note with an actionable fix, and the stack still starts:
#   - no GPU / no nvidia runtime  -> CPU-only mode (engine loads need a GPU)
#   - low disk on the docker root -> warn (images + model weights need ~30GB+)
#   - no /dev/kvm                 -> only the optional sandbox profile is off
# ─────────────────────────────────────────────────────────────────────────────
set -u

fail=0
ok()   { printf '  OK    %s\n' "$*"; }
warn() { printf '  WARN  %s\n' "$*"; }
note() { printf '  NOTE  %s\n' "$*"; }
err()  { printf '  FAIL  %s\n' "$*"; fail=1; }

echo "Forge preflight:"

# ── docker + compose v2 (hard requirements) ─────────────────────────────────
docker_usable=0
if ! command -v docker >/dev/null 2>&1; then
  err "docker not found — install Docker Engine: https://docs.docker.com/engine/install/"
else
  if ! docker info >/dev/null 2>&1; then
    err "docker is installed but the daemon is unreachable — start it (sudo systemctl start docker) and make sure your user is in the docker group (sudo usermod -aG docker \$USER, then log out and back in)"
  else
    ok "docker daemon reachable"
    docker_usable=1
  fi
  if ! docker compose version >/dev/null 2>&1; then
    err "docker compose v2 not found — install the compose plugin: https://docs.docker.com/compose/install/linux/"
  else
    ok "docker compose v2 present ($(docker compose version --short 2>/dev/null || echo '?'))"
  fi
fi

# ── GPU (optional — absence means CPU-only mode, never a hard fail) ─────────
# Vendor-aware: identifies NVIDIA *and* AMD/ROCm through kernel devices, not
# just `nvidia-smi` on PATH (a GPU box can lack the host CLI yet run GPU
# containers fine). The shared detector returns KEY\tmessage; map KEY to a line.
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
gpu_line=$("$SCRIPT_DIR/gpu-detect.sh" explain 2>/dev/null)
gpu_key=$(printf '%s' "$gpu_line" | cut -f1)
gpu_msg=$(printf '%s' "$gpu_line" | cut -f2-)
case "$gpu_key" in
  GPU_READY)              ok "$gpu_msg" ;;
  RUNTIME_MISSING|NO_GPU) warn "$gpu_msg" ;;
  CPU_FORCED)             note "$gpu_msg" ;;
  *)                      warn "GPU detection unavailable — continuing in CPU-only mode" ;;
esac

# ── disk space on the docker root (warn under ~30GB) ────────────────────────
if [ "$docker_usable" = 1 ]; then
  docker_root=$(docker info --format '{{ .DockerRootDir }}' 2>/dev/null)
  [ -n "$docker_root" ] || docker_root=/var/lib/docker
  free_gb=$(df -Pk "$docker_root" 2>/dev/null | awk 'NR==2 { printf "%d", $4 / 1024 / 1024 }')
  if [ -n "${free_gb:-}" ]; then
    if [ "$free_gb" -lt 30 ]; then
      warn "only ${free_gb}GB free on ${docker_root} — engine images plus model weights need ~30GB+; free up space or move the docker data-root"
    else
      ok "${free_gb}GB free on ${docker_root}"
    fi
  fi
fi

# ── /dev/kvm (info only — needed by the opt-in sandbox profile) ─────────────
if [ -e /dev/kvm ]; then
  note "/dev/kvm present — the optional sandbox lane is available (make sandbox)"
else
  note "/dev/kvm not present — the optional sandbox lane (smolvm microVMs, make sandbox) needs KVM; everything else is unaffected"
fi

if [ "$fail" -ne 0 ]; then
  echo "Preflight failed — fix the FAIL line(s) above and re-run 'make up'."
fi
exit "$fail"
