"""The shared host GPU detector (scripts/gpu-detect.sh) — the single source of
truth for the Makefile/setup.sh/verify.sh/preflight overlay decision. Driven
through fake `docker`/`rocm-smi` binaries on PATH and the FORGE_* overrides so
no real GPU is required."""

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "gpu-detect.sh"


def run(args, env=None, path_prepend=None):
    child_env = dict(os.environ)
    # Neutralize any inherited forcing from the outer session.
    child_env.pop("FORGE_NO_GPU", None)
    child_env.pop("FORGE_GPU_VENDOR", None)
    if env:
        child_env.update(env)
    if path_prepend:
        child_env["PATH"] = path_prepend + os.pathsep + child_env["PATH"]
    return subprocess.run(
        ["sh", str(SCRIPT), *args], capture_output=True, text=True, env=child_env
    )


def _fake_bin(tmp_path, name, body):
    d = tmp_path / "bin"
    d.mkdir(exist_ok=True)
    p = d / name
    p.write_text("#!/bin/sh\n" + body)
    p.chmod(0o755)
    return str(d)


def test_vendor_override():
    assert run(["vendor"], {"FORGE_GPU_VENDOR": "amd"}).stdout.strip() == "amd"
    assert run(["vendor"], {"FORGE_GPU_VENDOR": "nvidia"}).stdout.strip() == "nvidia"
    assert run(["vendor"], {"FORGE_GPU_VENDOR": "cpu"}).stdout.strip() == "cpu"


def test_no_gpu_forces_cpu():
    r = run(["compose-args"], {"FORGE_NO_GPU": "1", "FORGE_GPU_VENDOR": "nvidia"})
    assert r.stdout.strip() == ""
    assert run(["explain"], {"FORGE_NO_GPU": "1"}).stdout.startswith("CPU_FORCED")


def test_nvidia_overlay_when_runtime_present(tmp_path):
    binp = _fake_bin(
        tmp_path, "docker", '[ "$1" = info ] && echo " Runtimes: runc nvidia"\nexit 0\n'
    )
    r = run(["compose-args"], {"FORGE_GPU_VENDOR": "nvidia"}, path_prepend=binp)
    assert r.stdout.strip() == "-f docker-compose.gpu.yml"
    assert run(["explain"], {"FORGE_GPU_VENDOR": "nvidia"}, path_prepend=binp).stdout.startswith(
        "GPU_READY"
    )


def test_nvidia_no_runtime_no_overlay(tmp_path):
    binp = _fake_bin(tmp_path, "docker", "exit 0\n")  # info mentions no nvidia
    r = run(["compose-args"], {"FORGE_GPU_VENDOR": "nvidia"}, path_prepend=binp)
    assert r.stdout.strip() == ""
    assert run(["explain"], {"FORGE_GPU_VENDOR": "nvidia"}, path_prepend=binp).stdout.startswith(
        "RUNTIME_MISSING"
    )


def test_amd_hardware_via_rocm_smi(tmp_path):
    binp = _fake_bin(tmp_path, "rocm-smi", "exit 0\n")
    assert run(["vendor"], path_prepend=binp).stdout.strip() == "amd"


def test_amd_without_kfd_reports_runtime_missing():
    # Forced AMD with no /dev/kfd present -> the driver/ROCm advice, no overlay.
    assert run(["explain"], {"FORGE_GPU_VENDOR": "amd"}).stdout.startswith(
        "RUNTIME_MISSING"
    )
    assert run(["compose-args"], {"FORGE_GPU_VENDOR": "amd"}).stdout.strip() == ""


def test_usage_error_on_unknown_command():
    r = run(["bogus"])
    assert r.returncode == 2
