#!/usr/bin/env sh
# ─────────────────────────────────────────────────────────────────────────────
# Forge GPU detection — the single, vendor-aware source of truth shared by the
# Makefile, setup.sh, verify.sh and preflight.sh so they can never disagree.
#
# GPU identification must NOT hinge on `nvidia-smi` being on the host PATH. That
# was the old bug: it is wrong in both directions AND it is blind to AMD.
#   * NVIDIA: a host can run GPU containers through the NVIDIA Container Toolkit
#     with the kernel driver but WITHOUT the host `nvidia-smi` CLI (common on
#     servers). nvidia-smi "missing" there does NOT mean "no GPU".
#   * AMD/ROCm: there is no `nvidia-smi` at all — GPUs are reached by mounting
#     /dev/kfd + /dev/dri, so the old check reported every AMD box as CPU-only.
#
# We identify the VENDOR first, then decide two orthogonal things:
#   vendor    — nvidia | amd | cpu  (what hardware/driver is on this host)
#   runtime   — can Docker actually give a container this GPU?
#                 nvidia -> the nvidia container runtime is registered
#                 amd    -> /dev/kfd and a /dev/dri render node exist to mount
#               THIS decides which compose overlay (if any) is included.
#
# Usage:
#   gpu-detect.sh vendor         print nvidia | amd | cpu
#   gpu-detect.sh compose-args   print the "-f docker-compose.*.yml" overlay(s)
#   gpu-detect.sh runtime        exit 0 iff Docker can serve the detected GPU
#   gpu-detect.sh hardware       exit 0 iff a GPU + driver is present
#   gpu-detect.sh explain        print one KEY\tmessage advisory line
#
# FORGE_NO_GPU=1 forces CPU-only. FORGE_GPU_VENDOR=nvidia|amd|cpu overrides
# auto-detection (e.g. to pin a box that auto-detects ambiguously).
# ─────────────────────────────────────────────────────────────────────────────

_has() { command -v "$1" >/dev/null 2>&1; }

# An NVIDIA GPU + kernel driver is present (through several signals so no single
# missing userspace tool hides it).
forge_nvidia_hardware() {
  _has nvidia-smi && return 0
  [ -e /dev/nvidia0 ] && return 0
  [ -e /dev/nvidiactl ] && return 0
  if [ -d /proc/driver/nvidia/gpus ]; then
    for _g in /proc/driver/nvidia/gpus/*; do [ -e "$_g" ] && return 0; done
  fi
  _has lspci && lspci 2>/dev/null | grep -Eiq 'nvidia.*(vga|3d|display|controller)' && return 0
  return 1
}

# An AMD GPU with the amdgpu/ROCm compute stack is present.
forge_amd_hardware() {
  [ -e /dev/kfd ] && return 0            # the ROCm kernel-fusion device
  _has rocm-smi && return 0
  _has amd-smi && return 0
  [ -d /sys/module/amdgpu ] && return 0
  _has lspci && lspci 2>/dev/null | grep -Eiq '(amd|ati|advanced micro).*(vga|3d|display|controller)' && return 0
  return 1
}

# nvidia | amd | cpu. AMD is checked first because /dev/kfd is unambiguous;
# ties (no signal either way) default to nvidia to preserve legacy behavior.
forge_gpu_vendor() {
  case "${FORGE_GPU_VENDOR:-auto}" in
    nvidia) echo nvidia; return ;;
    amd)    echo amd; return ;;
    cpu|none) echo cpu; return ;;
  esac
  if forge_amd_hardware; then echo amd; return; fi
  if forge_nvidia_hardware; then echo nvidia; return; fi
  echo cpu
}

# Docker can hand a container an NVIDIA GPU (the nvidia runtime is registered,
# or a CDI spec exposes the devices).
_nvidia_runtime_ok() {
  _has docker || return 1
  _info=$(docker info 2>/dev/null) || return 1
  printf '%s' "$_info" | grep -qi 'nvidia' && return 0
  for _cdi in /etc/cdi/nvidia.yaml /etc/cdi/nvidia.json \
              /var/run/cdi/nvidia.yaml /var/run/cdi/nvidia.json; do
    [ -f "$_cdi" ] && return 0
  done
  return 1
}

# ROCm needs no special Docker runtime — just the device nodes to mount.
_amd_runtime_ok() {
  [ -e /dev/kfd ] || return 1
  [ -d /dev/dri ] || return 1
  for _r in /dev/dri/renderD*; do [ -e "$_r" ] && return 0; done
  return 1
}

forge_gpu_runtime_ok() {
  [ "${FORGE_NO_GPU:-0}" = 1 ] && return 1
  case "$(forge_gpu_vendor)" in
    nvidia) _nvidia_runtime_ok ;;
    amd)    _amd_runtime_ok ;;
    *)      return 1 ;;
  esac
}

forge_gpu_hardware() {
  case "$(forge_gpu_vendor)" in
    nvidia) forge_nvidia_hardware ;;
    amd)    forge_amd_hardware ;;
    *)      return 1 ;;
  esac
}

case "${1:-}" in
  vendor)
    forge_gpu_vendor
    ;;
  compose-args)
    [ "${FORGE_NO_GPU:-0}" = 1 ] && exit 0
    case "$(forge_gpu_vendor)" in
      nvidia) _nvidia_runtime_ok && printf -- '-f docker-compose.gpu.yml' ;;
      amd)    _amd_runtime_ok && printf -- '-f docker-compose.rocm.yml' ;;
    esac
    ;;
  runtime)
    forge_gpu_runtime_ok
    ;;
  hardware)
    forge_gpu_hardware
    ;;
  explain)
    _v=$(forge_gpu_vendor)
    if [ "${FORGE_NO_GPU:-0}" = 1 ]; then
      printf 'CPU_FORCED\tGPU disabled by FORGE_NO_GPU — CPU-only bring-up\n'
    elif [ "$_v" = nvidia ] && _nvidia_runtime_ok; then
      printf 'GPU_READY\tNVIDIA GPU ready (Docker exposes the nvidia runtime)\n'
    elif [ "$_v" = nvidia ]; then
      printf 'RUNTIME_MISSING\tNVIDIA GPU present but the Container Toolkit is not configured for Docker — engine containers will not see it. Install the NVIDIA Container Toolkit, then: sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker\n'
    elif [ "$_v" = amd ] && _amd_runtime_ok; then
      printf 'GPU_READY\tAMD GPU ready (ROCm /dev/kfd + /dev/dri present — the ROCm llama.cpp lane is available)\n'
    elif [ "$_v" = amd ]; then
      printf 'RUNTIME_MISSING\tAMD GPU present but /dev/kfd is missing — the amdgpu driver / ROCm stack is not loaded. Install ROCm (or load amdgpu) so the render nodes appear, then re-run.\n'
    else
      printf 'NO_GPU\tno NVIDIA or AMD GPU detected — CPU-only mode: the stack comes up and GPU stats show unavailable, but loading models onto an engine needs a GPU\n'
    fi
    ;;
  *)
    echo "usage: gpu-detect.sh {vendor|compose-args|runtime|hardware|explain}" >&2
    exit 2
    ;;
esac
