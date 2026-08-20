"""EngineManager tests (PLAN §2, §6.2, extended to multiple GPUs): command
construction for both GPU lanes, per-GPU lease arbitration (single- and
multi-GPU), and the healthwait failure paths — all with docker faked and httpx
routed through a MockTransport."""

import asyncio

import httpx
import pytest

from app.config import Settings, get_settings
from app.models import EngineKind, ModelEntry, ToolCallFormat
from app.services.engine_manager import (
    EngineManager,
    LeaseHeldError,
    build_airllm_env,
    build_llamacpp_command,
    build_vllm_command,
    engine_base_url,
    engine_container_name,
    engine_port,
)
from app.services.fit_rules import compute_ngl, estimate_n_layers


def make_model(**overrides) -> ModelEntry:
    defaults = dict(
        id=1,
        hf_repo="Qwen/Qwen2.5-Coder-14B-Instruct-GGUF",
        display_name="Qwen2.5 Coder 14B Instruct",
        params_b=14.0,
        file_path="qwen2.5-coder-14b-instruct-q4_k_m.gguf",
        size_gb=9.0,
        engine=EngineKind.llamacpp,
        ctx_max=16384,
        n_layers=40,
        tool_call_format=ToolCallFormat.hermes,
    )
    defaults.update(overrides)
    return ModelEntry(**defaults)


def flag_value(cmd: list[str], flag: str) -> str:
    return cmd[cmd.index(flag) + 1]


def make_manager(gpu_count: int) -> EngineManager:
    manager = EngineManager()
    manager._gpu_count = gpu_count  # what detect_gpu_count() would have found
    return manager


async def settle(manager: EngineManager, timeout: float = 10) -> None:
    """Wait for every in-flight load task to finish."""
    tasks = list(manager._load_tasks.values())
    if tasks:
        await asyncio.wait_for(asyncio.gather(*tasks), timeout)


# ── build_airllm_env (per-model shard cache) ────────────────────────────────


class TestBuildAirllmEnv:
    def test_shards_dir_is_namespaced_per_model(self):
        # AirLLM's split dir name ("splitted_model.4bit") is a constant with no
        # model namespacing, so two models MUST get different shard roots or one
        # model's layer shards satisfy the other's by-name completeness check.
        settings = Settings()
        a = build_airllm_env(
            make_model(display_name="Llama 3.3 70B", engine=EngineKind.airllm),
            settings,
        )
        b = build_airllm_env(
            make_model(display_name="Qwen 72B", engine=EngineKind.airllm),
            settings,
        )
        assert a["AIRLLM_SHARDS_DIR"] == "/data/models/airllm-shards/llama-3-3-70b"
        assert b["AIRLLM_SHARDS_DIR"] == "/data/models/airllm-shards/qwen-72b"
        assert a["AIRLLM_SHARDS_DIR"] != b["AIRLLM_SHARDS_DIR"]

    def test_shards_dir_matches_the_served_slug(self):
        model = make_model(display_name="Llama 3.3 70B", engine=EngineKind.airllm)
        env = build_airllm_env(model, Settings())
        # The subdir is exactly the model slug the /v1 router serves.
        assert env["AIRLLM_SHARDS_DIR"].endswith("/" + env["AIRLLM_MODEL_NAME"])


# ── build_llamacpp_command ──────────────────────────────────────────────────


class TestLlamacppCommand:
    def test_flags_and_computed_ngl(self):
        settings = Settings()
        model = make_model()
        cmd = build_llamacpp_command(model, settings)

        assert "--jinja" in cmd
        # llama.cpp made --flash-attn value-taking ([on|off|auto]) in Aug 2025;
        # the command must omit it entirely to stay valid on the rolling tag.
        assert "--flash-attn" not in cmd
        assert flag_value(cmd, "-m") == f"/data/models/{model.file_path}"
        assert flag_value(cmd, "--host") == "0.0.0.0"
        assert flag_value(cmd, "--port") == str(settings.llamacpp_port)
        assert flag_value(cmd, "--parallel") == str(settings.llamacpp_slots)
        from app.opencode_config import opencode_model_id

        assert flag_value(cmd, "--alias") == opencode_model_id(model)

        ngl = int(flag_value(cmd, "--n-gpu-layers"))
        assert 0 < ngl <= model.n_layers
        assert ngl == compute_ngl(9.0, 40, 16384, settings.vram_budget_gb)

    def test_ctx_capped_to_default_ctx(self):
        settings = Settings()
        cmd = build_llamacpp_command(make_model(ctx_max=131072), settings)
        assert flag_value(cmd, "-c") == str(settings.default_ctx)

    def test_smaller_model_ctx_wins(self):
        cmd = build_llamacpp_command(make_model(ctx_max=8192), Settings())
        assert flag_value(cmd, "-c") == "8192"

    def test_zero_ctx_falls_back_to_default(self):
        settings = Settings()
        cmd = build_llamacpp_command(make_model(ctx_max=0), settings)
        assert flag_value(cmd, "-c") == str(settings.default_ctx)

    def test_unknown_layer_count_estimated_from_params(self):
        settings = Settings()
        model = make_model(n_layers=0, params_b=14.0)
        cmd = build_llamacpp_command(model, settings)
        expected_layers = estimate_n_layers(14.0)
        assert int(flag_value(cmd, "--n-gpu-layers")) == compute_ngl(
            9.0, expected_layers, 16384, settings.vram_budget_gb
        )

    def test_big_offload_model_gets_partial_ngl(self):
        model = make_model(size_gb=18.0, n_layers=48, params_b=30.0)
        cmd = build_llamacpp_command(model, Settings())
        ngl = int(flag_value(cmd, "--n-gpu-layers"))
        assert 0 < ngl < 48


# ── build_vllm_command ──────────────────────────────────────────────────────


class TestVllmCommand:
    def _cmd(self, fmt: ToolCallFormat) -> list[str]:
        model = make_model(
            engine=EngineKind.vllm, file_path="", tool_call_format=fmt
        )
        return build_vllm_command(model, Settings())

    @pytest.mark.parametrize(
        ("fmt", "parser"),
        [
            (ToolCallFormat.hermes, "hermes"),
            (ToolCallFormat.qwen, "hermes"),
            (ToolCallFormat.llama3, "llama3_json"),
        ],
    )
    def test_parser_mapping(self, fmt, parser):
        cmd = self._cmd(fmt)
        assert "--enable-auto-tool-choice" in cmd
        assert flag_value(cmd, "--tool-call-parser") == parser

    def test_serves_the_opencode_slug_first(self):
        """OpenCode sends the provider models-map key (the slug) as the
        request's model field; vLLM 404s unserved names — the slug must be a
        served model name or every session on the fast lane fails."""
        from app.opencode_config import opencode_model_id

        model = make_model(engine=EngineKind.vllm, file_path="")
        cmd = build_vllm_command(model, Settings())
        idx = cmd.index("--served-model-name")
        served = [cmd[idx + 1], cmd[idx + 2]]
        assert served[0] == opencode_model_id(model)
        assert model.display_name in served

    def test_none_format_gets_no_tool_flags(self):
        cmd = self._cmd(ToolCallFormat.none)
        assert "--enable-auto-tool-choice" not in cmd
        assert "--tool-call-parser" not in cmd

    def test_awq_quantization_and_ctx_cap(self):
        cmd = self._cmd(ToolCallFormat.hermes)
        assert flag_value(cmd, "--quantization") == "awq"
        assert flag_value(cmd, "--max-model-len") == "16384"
        assert flag_value(cmd, "--gpu-memory-utilization") == "0.90"

    def test_ctx_above_16k_is_capped(self):
        model = make_model(engine=EngineKind.vllm, file_path="", ctx_max=32768)
        cmd = build_vllm_command(model, Settings())
        assert flag_value(cmd, "--max-model-len") == "16384"

    def test_model_path_prefers_local_snapshot(self):
        model = make_model(engine=EngineKind.vllm, file_path="snapshots/qwen-awq")
        cmd = build_vllm_command(model, Settings())
        assert flag_value(cmd, "--model") == "/data/models/snapshots/qwen-awq"

    def test_model_path_falls_back_to_hf_repo(self):
        model = make_model(engine=EngineKind.vllm, file_path="")
        cmd = build_vllm_command(model, Settings())
        assert flag_value(cmd, "--model") == model.hf_repo

    def test_single_gpu_has_no_tensor_parallel_flag(self):
        model = make_model(engine=EngineKind.vllm, file_path="")
        cmd = build_vllm_command(model, Settings(), tensor_parallel=1)
        assert "--tensor-parallel-size" not in cmd

    def test_tensor_parallel_flag_for_multi_gpu(self):
        model = make_model(engine=EngineKind.vllm, file_path="")
        cmd = build_vllm_command(model, Settings(), tensor_parallel=2)
        assert flag_value(cmd, "--tensor-parallel-size") == "2"


# ── engine addressing (per-GPU container names) ─────────────────────────────


class TestEngineAddressing:
    def test_base_urls_per_lane(self):
        settings = Settings()
        assert (
            engine_base_url(EngineKind.llamacpp, settings)
            == "http://forge-engine-llamacpp-gpu0:8081/v1"
        )
        assert (
            engine_base_url(EngineKind.vllm, settings)
            == "http://forge-engine-vllm-gpu0:8082/v1"
        )
        assert (
            engine_base_url(EngineKind.airllm, settings)
            == "http://forge-engine-airllm-gpu0:8083/v1"
        )

    def test_base_url_carries_the_gpu_index(self):
        settings = Settings()
        assert (
            engine_base_url(EngineKind.llamacpp, settings, gpu_index=1)
            == "http://forge-engine-llamacpp-gpu1:8081/v1"
        )

    def test_ports_follow_settings(self):
        settings = Settings(llamacpp_port=9001, vllm_port=9002, airllm_port=9003)
        assert engine_port(EngineKind.llamacpp, settings) == 9001
        assert engine_base_url(EngineKind.vllm, settings).endswith(":9002/v1")
        assert engine_base_url(EngineKind.airllm, settings).endswith(":9003/v1")

    def test_container_names(self):
        assert engine_container_name(EngineKind.llamacpp) == "forge-engine-llamacpp-gpu0"
        assert engine_container_name(EngineKind.vllm, 1) == "forge-engine-vllm-gpu1"


# ── lease arbitration on a single GPU (the classic behaviors) ───────────────


@pytest.fixture
def manager() -> EngineManager:
    return make_manager(1)


@pytest.fixture
def manager2() -> EngineManager:
    return make_manager(2)


@pytest.fixture
def stub_healthwait(monkeypatch):
    """Replace the container-start/healthwait coroutine so lease-arbitration
    tests run instantly and touch neither docker nor HTTP."""

    async def stub(self, model, lease, snapshot):
        lease.state = "ready"
        lease.container_id = f"stub-{model.id}"

    monkeypatch.setattr(EngineManager, "_start_and_healthwait", stub)


class TestLeaseArbitration:
    async def test_second_load_without_force_raises_lease_held(
        self, manager, stub_healthwait
    ):
        first = make_model(id=1, display_name="First Model")
        second = make_model(id=2, display_name="Second Model")

        lease = await manager.load(first)
        await settle(manager)
        assert lease.state == "ready"

        with pytest.raises(LeaseHeldError) as excinfo:
            await manager.load(second)
        (holder,) = excinfo.value.holders
        assert holder["model_id"] == 1
        assert holder["model_name"] == "First Model"
        # The original lease is untouched.
        assert manager.lease is lease

    async def test_second_load_blocked_even_while_still_starting(
        self, manager, monkeypatch
    ):
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_stub(self, model, lease, snapshot):
            started.set()
            await release.wait()
            lease.state = "ready"

        monkeypatch.setattr(EngineManager, "_start_and_healthwait", slow_stub)
        await manager.load(make_model(id=1))
        await asyncio.wait_for(started.wait(), 5)
        with pytest.raises(LeaseHeldError):
            await manager.load(make_model(id=2, display_name="Other Model"))
        release.set()
        await settle(manager)

    async def test_force_load_replaces_the_lease(
        self, manager, stub_healthwait, fake_docker
    ):
        await manager.load(make_model(id=1, display_name="First Model"))
        await settle(manager)

        lease = await manager.load(
            make_model(id=2, display_name="Second Model"), force=True
        )
        assert lease.model_id == 2
        assert manager.lease.model_name == "Second Model"
        assert [le.model_id for le in manager.active_leases()] == [2]

    async def test_failed_lease_does_not_block_the_next_load(
        self, manager, monkeypatch
    ):
        async def failing_stub(self, model, lease, snapshot):
            lease.state = "failed"
            lease.error = "boom"

        monkeypatch.setattr(EngineManager, "_start_and_healthwait", failing_stub)
        await manager.load(make_model(id=1))
        await settle(manager)
        assert manager.lease is None  # failed lease is not active

        async def ok_stub(self, model, lease, snapshot):
            lease.state = "ready"

        monkeypatch.setattr(EngineManager, "_start_and_healthwait", ok_stub)
        lease = await manager.load(make_model(id=2, display_name="Other Model"))
        await settle(manager)
        assert lease.model_id == 2
        assert lease.state == "ready"

    async def test_unload_releases_lease_and_removes_containers(
        self, manager, fake_docker, httpx_mock
    ):
        fake_docker.containers.spawn_status = "running"
        httpx_mock.set_handler(lambda request: httpx.Response(200, json={"data": []}))

        await manager.load(make_model(id=1))
        await settle(manager)
        assert manager.lease.state == "ready"
        assert len(fake_docker.containers.list(filters={"label": "forge.engine"})) == 1

        await manager.unload()
        assert manager.lease is None
        assert manager.active_leases() == []
        assert fake_docker.containers.list(filters={"label": "forge.engine"}) == []


# ── multi-GPU leases ────────────────────────────────────────────────────────


class TestMultiGpu:
    async def test_two_models_serve_concurrently_on_distinct_gpus(
        self, manager2, fake_docker, httpx_mock
    ):
        httpx_mock.set_handler(lambda request: httpx.Response(200, json={"data": []}))
        first = await manager2.load(make_model(id=1, display_name="First Model"))
        second = await manager2.load(make_model(id=2, display_name="Second Model"))
        await settle(manager2)

        assert first.state == "ready" and first.gpu_ids == [0]
        assert second.state == "ready" and second.gpu_ids == [1]
        assert len(manager2.active_leases()) == 2

        names = {c.name for c in fake_docker.containers.run_calls}
        assert names == {"forge-engine-llamacpp-gpu0", "forge-engine-llamacpp-gpu1"}

    async def test_third_load_raises_with_both_holders(
        self, manager2, stub_healthwait
    ):
        await manager2.load(make_model(id=1, display_name="First Model"))
        await manager2.load(make_model(id=2, display_name="Second Model"))
        with pytest.raises(LeaseHeldError) as excinfo:
            await manager2.load(make_model(id=3, display_name="Third Model"))
        assert {h["model_id"] for h in excinfo.value.holders} == {1, 2}

    async def test_gpu_index_pinning(self, manager2, stub_healthwait):
        lease = await manager2.load(make_model(id=1), gpu_index=1)
        assert lease.gpu_ids == [1]
        status = manager2.status()
        assert status["gpus"][0]["lease"] is None
        assert status["gpus"][1]["lease"]["model_id"] == 1

        # The pinned GPU is now busy; pinning there again (another model) 409s.
        with pytest.raises(LeaseHeldError):
            await manager2.load(
                make_model(id=2, display_name="Other Model"), gpu_index=1
            )
        # An out-of-range pin can never be satisfied.
        with pytest.raises(LeaseHeldError):
            await manager2.load(
                make_model(id=3, display_name="Third Model"), gpu_index=5
            )
        await settle(manager2)

    async def test_same_model_returns_existing_lease(self, manager2, stub_healthwait):
        model = make_model(id=1)
        first = await manager2.load(model)
        again = await manager2.load(make_model(id=1))  # same model id
        assert again is first
        assert len(manager2._leases) == 1  # no second GPU claimed
        await settle(manager2)

    async def test_slug_collision_between_different_models_is_409(
        self, manager2, stub_healthwait
    ):
        """Two DIFFERENT models slugifying identically must never be absorbed
        into one lease — the /v1 router routes by slug, so a collision would
        silently serve the wrong model (review finding)."""
        await manager2.load(make_model(id=1, display_name="Foo Bar"))
        with pytest.raises(LeaseHeldError):
            await manager2.load(make_model(id=2, display_name="Foo-Bar"))
        assert len(manager2._leases) == 1
        await settle(manager2)

    async def test_unload_single_gpu_releases_only_that_gpu(
        self, manager2, fake_docker, httpx_mock
    ):
        httpx_mock.set_handler(lambda request: httpx.Response(200, json={"data": []}))
        await manager2.load(make_model(id=1, display_name="First Model"))
        second = await manager2.load(make_model(id=2, display_name="Second Model"))
        await settle(manager2)

        await manager2.unload(gpu_index=0)

        assert [le.model_id for le in manager2.active_leases()] == [2]
        assert manager2.active_leases()[0] is second
        remaining = fake_docker.containers.list(filters={"label": "forge.engine"})
        assert [c.name for c in remaining] == ["forge-engine-llamacpp-gpu1"]

    async def test_vllm_tensor_parallel_spans_both_gpus(
        self, manager2, fake_docker, httpx_mock
    ):
        httpx_mock.set_handler(lambda request: httpx.Response(200, json={"data": []}))
        model = make_model(id=1, engine=EngineKind.vllm, file_path="")
        lease = await manager2.load(model, gpu_count=2)
        await settle(manager2)

        assert lease.state == "ready"
        assert lease.gpu_ids == [0, 1]
        (container,) = fake_docker.containers.run_calls
        assert container.name == "forge-engine-vllm-gpu0"
        kwargs = container.run_kwargs
        (device_request,) = kwargs["device_requests"]
        assert device_request["DeviceIDs"] == ["0", "1"]
        assert flag_value(kwargs["command"], "--tensor-parallel-size") == "2"
        assert kwargs["labels"]["forge.gpus"] == "0,1"

        # Both GPUs are held by the one lease — nothing else fits.
        with pytest.raises(LeaseHeldError):
            await manager2.load(make_model(id=2, display_name="Other Model"))

    async def test_multi_gpu_outside_vllm_lane_raises(self, manager2):
        with pytest.raises(ValueError):
            await manager2.load(make_model(id=1), gpu_count=2)
        with pytest.raises(ValueError):
            await manager2.load(
                make_model(id=2, engine=EngineKind.airllm), gpu_count=2
            )


# ── status() shape ──────────────────────────────────────────────────────────


class TestStatusShape:
    async def test_status_reports_gpus_leases_and_engines(
        self, manager2, stub_healthwait
    ):
        lease = await manager2.load(make_model(id=1))
        await settle(manager2)

        status = manager2.status()
        assert status["gpu_count"] == 2
        # Backcompat single-lease view: the first active lease.
        assert status["lease"] == lease.as_dict()
        assert status["leases"] == [lease.as_dict()]
        assert [gpu["index"] for gpu in status["gpus"]] == [0, 1]
        assert status["gpus"][0]["lease"] == lease.as_dict()
        assert status["gpus"][1]["lease"] is None

        settings = get_settings()
        engines = status["engines"]
        assert set(engines) == {"llamacpp", "vllm", "sglang", "airllm", "imagegen"}
        assert engines["llamacpp"]["port"] == settings.llamacpp_port
        assert engines["llamacpp"]["active_on"] == [0]
        assert engines["vllm"]["active_on"] == []

    def test_empty_status(self, manager):
        status = manager.status()
        assert status["gpu_count"] == 1
        assert status["lease"] is None
        assert status["leases"] == []
        assert status["gpus"] == [{"index": 0, "lease": None}]


# ── real healthwait paths (fake docker + mock transport) ────────────────────


class TestHealthwait:
    async def test_successful_load_reaches_ready(self, manager, fake_docker, httpx_mock):
        httpx_mock.set_handler(lambda request: httpx.Response(200, json={"data": []}))
        model = make_model(id=5)

        lease = await manager.load(model)
        assert lease.state == "starting"
        await settle(manager)
        assert lease.state == "ready"
        assert lease.error == ""

        # The engine container was created with the GPU + lane wiring.
        settings = get_settings()
        (container,) = fake_docker.containers.run_calls
        assert container.name == "forge-engine-llamacpp-gpu0"
        assert container.image == settings.llamacpp_image
        kwargs = container.run_kwargs
        assert kwargs["network"] == settings.docker_network
        assert kwargs["labels"]["forge.engine"] == "llamacpp"
        assert kwargs["labels"]["forge.model_id"] == "5"
        assert kwargs["labels"]["forge.gpus"] == "0"
        assert kwargs["device_requests"], "engine containers must request the GPU"
        assert kwargs["restart_policy"] == {"Name": "no"}
        assert "--jinja" in kwargs["command"]

        # Healthcheck polled the lane's OpenAI surface on the per-GPU name.
        health = httpx_mock.requests[-1]
        assert health.url.host == "forge-engine-llamacpp-gpu0"
        assert health.url.port == settings.llamacpp_port
        assert health.url.path == "/v1/models"

    async def test_container_start_failure_marks_lease_failed(
        self, manager, fake_docker, httpx_mock
    ):
        fake_docker.containers.fail_run = RuntimeError("no NVIDIA driver")
        lease = await manager.load(make_model(id=1))
        await settle(manager)
        assert lease.state == "failed"
        assert "container start failed" in lease.error
        assert "no NVIDIA driver" in lease.error

    async def test_engine_exit_during_healthwait_surfaces_log_tail(
        self, manager, fake_docker, httpx_mock
    ):
        fake_docker.containers.spawn_status = "exited"
        fake_docker.containers.logs_text = b"CUDA error: out of memory"

        lease = await manager.load(make_model(id=1))
        await settle(manager)
        assert lease.state == "failed"
        assert "engine exited during load" in lease.error
        assert "out of memory" in lease.error
        # Failed container is cleaned up so the lease can be retried.
        (container,) = fake_docker.containers.run_calls
        assert container.removed is True

    async def test_healthcheck_timeout_marks_lease_failed(
        self, manager, fake_docker, httpx_mock, monkeypatch
    ):
        monkeypatch.setenv("FORGE_ENGINE_LOAD_TIMEOUT_S", "0")
        get_settings.cache_clear()

        lease = await manager.load(make_model(id=1))
        await settle(manager)
        assert lease.state == "failed"
        assert "timed out" in lease.error
        (container,) = fake_docker.containers.run_calls
        assert container.removed is True


class TestReconcileOnBoot:
    async def test_legacy_container_without_slug_label_resolves_slug_from_db(
        self, fake_docker, db_ready
    ):
        """Engines started by pre-multi-GPU builds carry no forge.model_slug
        label; adoption must recover the slug from the catalog or every new
        session 404s at the /v1 router until a manual reload (review finding)."""
        from app.db import read_session
        from app.opencode_config import opencode_model_id
        from app.services.engine_manager import EngineManager
        from tests.conftest import add_model

        model_id = add_model(display_name="Qwen3 Coder 30B A3B")
        with read_session() as db:
            model = db.get(ModelEntry, model_id)
            expected_slug = opencode_model_id(model)

        fake_docker.containers.run(
            "ghcr.io/ggml-org/llama.cpp:server-cuda",
            name="forge-engine-llamacpp",
            labels={
                "forge.engine": "llamacpp",
                "forge.model_id": str(model_id),
                "forge.model_name": "Qwen3 Coder 30B A3B",
                # legacy: no forge.model_slug, no forge.gpus
            },
        )

        manager = EngineManager()
        manager._gpu_count = 1
        manager.reconcile_on_boot()

        lease = manager.lease
        assert lease is not None and lease.state == "ready"
        assert lease.model_slug == expected_slug
        assert lease.gpu_ids == [0]
