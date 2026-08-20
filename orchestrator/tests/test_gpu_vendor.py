"""Vendor-aware GPU support: detection, device wiring (NVIDIA device requests
vs AMD/ROCm /dev/kfd + /dev/dri mounts), AMD sysfs counting/stats, and the
AMD-only engine guard. No real GPU is needed — sysfs and glob are faked."""

import glob as glob_mod

import pytest

from app import config
from app import db as db_module
from app.config import get_settings
from app.models import EngineKind, ModelEntry
from app.routers import system
from app.services import engine_manager as em

from .conftest import add_model


def _reload_settings(monkeypatch, **env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    config.get_settings.cache_clear()


# ── vendor detection ─────────────────────────────────────────────────────────


class TestDetectVendor:
    def test_override_wins(self, monkeypatch):
        for want in ("nvidia", "amd", "cpu"):
            _reload_settings(monkeypatch, FORGE_GPU_VENDOR=want)
            assert em.detect_gpu_vendor() == want

    def test_none_maps_to_cpu(self, monkeypatch):
        _reload_settings(monkeypatch, FORGE_GPU_VENDOR="none")
        assert em.detect_gpu_vendor() == "cpu"

    def test_amd_from_kfd_device(self, monkeypatch):
        _reload_settings(monkeypatch, FORGE_GPU_VENDOR="auto")
        monkeypatch.setattr(em.os.path, "exists", lambda p: p == "/dev/kfd")
        monkeypatch.setattr(em.os.path, "isdir", lambda p: False)
        assert em.detect_gpu_vendor() == "amd"

    def test_unclassifiable_defaults_to_nvidia(self, monkeypatch):
        # No AMD/NVIDIA signals and NVML unavailable — legacy-safe default.
        _reload_settings(monkeypatch, FORGE_GPU_VENDOR="auto")
        monkeypatch.setattr(em.os.path, "exists", lambda p: False)
        monkeypatch.setattr(em.os.path, "isdir", lambda p: False)
        assert em.detect_gpu_vendor() == "nvidia"


class TestAmdGpuCount:
    def test_counts_only_amd_render_nodes(self, tmp_path, monkeypatch):
        amd = tmp_path / "renderD128" / "device"
        amd.mkdir(parents=True)
        (amd / "vendor").write_text("0x1002\n")  # AMD/ATI
        other = tmp_path / "renderD129" / "device"
        other.mkdir(parents=True)
        (other / "vendor").write_text("0x10de\n")  # NVIDIA — ignored
        monkeypatch.setattr(
            em.glob, "glob", lambda _p: [str(amd / "vendor"), str(other / "vendor")]
        )
        assert em._amd_gpu_count() == 1

    def test_detect_count_uses_amd_path(self, monkeypatch):
        _reload_settings(monkeypatch, FORGE_GPU_VENDOR="amd")
        monkeypatch.setattr(em, "_amd_gpu_count", lambda: 2)
        assert em.detect_gpu_count() == 2


# ── device wiring ────────────────────────────────────────────────────────────


class TestGpuRunKwargs:
    def test_nvidia_uses_device_requests(self):
        kwargs, env = em.gpu_run_kwargs("nvidia", [0, 1])
        (req,) = kwargs["device_requests"]
        assert req["DeviceIDs"] == ["0", "1"]
        assert env == {}
        assert "devices" not in kwargs

    def test_amd_mounts_kfd_and_dri(self, monkeypatch):
        _reload_settings(monkeypatch, FORGE_GPU_VENDOR="amd")
        kwargs, env = em.gpu_run_kwargs("amd", [1])
        assert kwargs["devices"] == ["/dev/kfd", "/dev/dri"]
        assert kwargs["group_add"] == ["video", "render"]
        assert kwargs["ipc_mode"] == "host"
        assert "device_requests" not in kwargs
        assert env["HIP_VISIBLE_DEVICES"] == "1"
        assert env["ROCR_VISIBLE_DEVICES"] == "1"
        assert "HSA_OVERRIDE_GFX_VERSION" not in env

    def test_amd_sets_hsa_override_when_configured(self, monkeypatch):
        _reload_settings(
            monkeypatch, FORGE_GPU_VENDOR="amd", HSA_OVERRIDE_GFX_VERSION="9.0.0"
        )
        _kwargs, env = em.gpu_run_kwargs("amd", [0])
        assert env["HSA_OVERRIDE_GFX_VERSION"] == "9.0.0"


# ── engine spawn: AMD uses the ROCm image + devices, refuses CUDA-only lanes ──


def _lease_for(model, engine=EngineKind.llamacpp):
    return em.Lease(
        model_id=model.id,
        model_name=model.display_name,
        model_slug="test-slug",
        engine=engine,
        gpu_ids=[0],
    )


class TestAmdEngineSpawn:
    def test_llamacpp_spawns_rocm_image_with_devices(
        self, db_ready, fake_docker, monkeypatch
    ):
        em.engine_manager._gpu_vendor = "amd"
        em.engine_manager._gpu_count = 1
        model_id = add_model()  # llama.cpp GGUF
        with db_module.read_session() as db:
            model = db.get(ModelEntry, model_id)
        container = em.engine_manager._create_container(
            model, _lease_for(model), snapshot={}
        )
        kw = container.run_kwargs
        assert container.image == get_settings().llamacpp_rocm_image
        assert kw["devices"] == ["/dev/kfd", "/dev/dri"]
        assert "device_requests" not in kw
        assert kw["environment"]["HIP_VISIBLE_DEVICES"] == "0"

    def test_non_llamacpp_lane_refused_on_amd(
        self, db_ready, fake_docker, monkeypatch
    ):
        em.engine_manager._gpu_vendor = "amd"
        em.engine_manager._gpu_count = 1
        model_id = add_model(engine=EngineKind.vllm, file_path="")
        with db_module.read_session() as db:
            model = db.get(ModelEntry, model_id)
        with pytest.raises(RuntimeError, match="needs an NVIDIA GPU"):
            em.engine_manager._create_container(
                model, _lease_for(model, EngineKind.vllm), snapshot={}
            )

    def test_nvidia_still_uses_device_requests(
        self, db_ready, fake_docker, monkeypatch
    ):
        em.engine_manager._gpu_vendor = "nvidia"
        em.engine_manager._gpu_count = 1
        model_id = add_model()
        with db_module.read_session() as db:
            model = db.get(ModelEntry, model_id)
        container = em.engine_manager._create_container(
            model, _lease_for(model), snapshot={}
        )
        kw = container.run_kwargs
        assert container.image == get_settings().llamacpp_image
        assert kw["device_requests"], "NVIDIA lane must request the GPU"
        assert "devices" not in kw


# ── AMD stats from sysfs ─────────────────────────────────────────────────────


class TestAmdStats:
    def test_reads_amdgpu_sysfs(self, tmp_path, monkeypatch):
        dev = tmp_path / "card0" / "device"
        dev.mkdir(parents=True)
        (dev / "vendor").write_text("0x1002\n")
        (dev / "mem_info_vram_total").write_text(str(16 * 1024**3))
        (dev / "mem_info_vram_used").write_text(str(2 * 1024**3))
        (dev / "gpu_busy_percent").write_text("42")
        (dev / "product_name").write_text("Instinct MI25\n")
        monkeypatch.setattr(glob_mod, "glob", lambda _p: [str(dev)])
        assert system._amd_gpu_stats() == [
            {
                "index": 0,
                "name": "Instinct MI25",
                "vram_total_gb": 16.0,
                "vram_used_gb": 2.0,
                "utilization_pct": 42,
            }
        ]

    def test_skips_non_amd_cards(self, tmp_path, monkeypatch):
        dev = tmp_path / "card0" / "device"
        dev.mkdir(parents=True)
        (dev / "vendor").write_text("0x10de\n")  # NVIDIA
        monkeypatch.setattr(glob_mod, "glob", lambda _p: [str(dev)])
        assert system._amd_gpu_stats() is None

    def test_gpu_stats_dispatches_on_vendor(self, monkeypatch):
        em.engine_manager._gpu_vendor = "amd"
        monkeypatch.setattr(system, "_amd_gpu_stats", lambda: [{"index": 0}])
        monkeypatch.setattr(
            system, "_nvidia_gpu_stats", lambda: pytest.fail("used NVML on AMD")
        )
        assert system._gpu_stats() == [{"index": 0}]
