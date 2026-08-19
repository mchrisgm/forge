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
if command -v nvidia-smi >/dev/null 2>&1; then
  if [ "$docker_usable" = 1 ] && ! docker info 2>/dev/null | grep -qi nvidia; then
    warn "nvidia-smi found, but docker does not list the NVIDIA runtime — engine containers will not see the GPU. Install the NVIDIA Container Toolkit, then: sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker"
  else
    ok "NVIDIA GPU detected (make up will include the GPU overlay)"
  fi
else
  warn "no NVIDIA GPU detected (nvidia-smi missing) — continuing in CPU-only mode: the stack comes up and GPU stats show unavailable, but loading models onto an engine needs a GPU"
fi

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
