"""EngineManager tests (PLAN §2, §6.2): command construction for both GPU
lanes, single-GPU lease arbitration, and the healthwait failure paths — all
with docker faked and httpx routed through a MockTransport."""

import asyncio

import httpx
import pytest

from app.config import Settings, get_settings
from app.models import EngineKind, ModelEntry, ToolCallFormat
from app.services.engine_manager import (
    EngineManager,
    LeaseHeldError,
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


# ── build_llamacpp_command ──────────────────────────────────────────────────


class TestLlamacppCommand:
    def test_flags_and_computed_ngl(self):
        settings = Settings()
        model = make_model()
        cmd = build_llamacpp_command(model, settings)

        assert "--jinja" in cmd
        assert "--flash-attn" in cmd
        assert flag_value(cmd, "-m") == f"/data/models/{model.file_path}"
        assert flag_value(cmd, "--host") == "0.0.0.0"
        assert flag_value(cmd, "--port") == str(settings.llamacpp_port)
        assert flag_value(cmd, "--parallel") == str(settings.llamacpp_slots)
        assert flag_value(cmd, "--alias") == model.display_name

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


# ── engine addressing ───────────────────────────────────────────────────────


class TestEngineAddressing:
    def test_base_urls_per_lane(self):
        settings = Settings()
        assert (
            engine_base_url(EngineKind.llamacpp, settings)
            == "http://forge-engine-llamacpp:8081/v1"
        )
        assert (
            engine_base_url(EngineKind.vllm, settings)
            == "http://forge-engine-vllm:8082/v1"
        )
        assert (
            engine_base_url(EngineKind.airllm, settings)
            == "http://forge-engine-airllm:8083/v1"
        )

    def test_ports_follow_settings(self):
        settings = Settings(llamacpp_port=9001, vllm_port=9002, airllm_port=9003)
        assert engine_port(EngineKind.llamacpp, settings) == 9001
        assert engine_base_url(EngineKind.vllm, settings).endswith(":9002/v1")
        assert engine_base_url(EngineKind.airllm, settings).endswith(":9003/v1")

    def test_container_names(self):
        assert engine_container_name(EngineKind.llamacpp) == "forge-engine-llamacpp"
        assert engine_container_name(EngineKind.vllm) == "forge-engine-vllm"


# ── the single-GPU lease ────────────────────────────────────────────────────


@pytest.fixture
def manager() -> EngineManager:
    return EngineManager()


@pytest.fixture
def stub_healthwait(monkeypatch):
    """Replace the container-start/healthwait coroutine so lease-arbitration
    tests run instantly and touch neither docker nor HTTP."""

    async def stub(self, model, lease):
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
        await asyncio.wait_for(manager._load_task, 5)
        assert lease.state == "ready"

        with pytest.raises(LeaseHeldError) as excinfo:
            await manager.load(second)
        assert excinfo.value.holder["model_id"] == 1
        assert excinfo.value.holder["model_name"] == "First Model"
        # The original lease is untouched.
        assert manager.lease is lease

    async def test_second_load_blocked_even_while_still_starting(
        self, manager, monkeypatch
    ):
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_stub(self, model, lease):
            started.set()
            await release.wait()
            lease.state = "ready"

        monkeypatch.setattr(EngineManager, "_start_and_healthwait", slow_stub)
        await manager.load(make_model(id=1))
        await asyncio.wait_for(started.wait(), 5)
        with pytest.raises(LeaseHeldError):
            await manager.load(make_model(id=2))
        release.set()
        await asyncio.wait_for(manager._load_task, 5)

    async def test_force_load_replaces_the_lease(
        self, manager, stub_healthwait, fake_docker
    ):
        await manager.load(make_model(id=1, display_name="First Model"))
        await asyncio.wait_for(manager._load_task, 5)

        lease = await manager.load(
            make_model(id=2, display_name="Second Model"), force=True
        )
        assert lease.model_id == 2
        assert manager.lease.model_name == "Second Model"

    async def test_failed_lease_does_not_block_the_next_load(
        self, manager, monkeypatch
    ):
        async def failing_stub(self, model, lease):
            lease.state = "failed"
            lease.error = "boom"

        monkeypatch.setattr(EngineManager, "_start_and_healthwait", failing_stub)
        await manager.load(make_model(id=1))
        await asyncio.wait_for(manager._load_task, 5)
        assert manager.lease.state == "failed"

        async def ok_stub(self, model, lease):
            lease.state = "ready"

        monkeypatch.setattr(EngineManager, "_start_and_healthwait", ok_stub)
        lease = await manager.load(make_model(id=2))
        await asyncio.wait_for(manager._load_task, 5)
        assert lease.model_id == 2
        assert lease.state == "ready"

    async def test_unload_releases_lease_and_removes_containers(
        self, manager, fake_docker, httpx_mock
    ):
        fake_docker.containers.spawn_status = "running"
        httpx_mock.set_handler(lambda request: httpx.Response(200, json={"data": []}))

        await manager.load(make_model(id=1))
        await asyncio.wait_for(manager._load_task, 10)
        assert manager.lease.state == "ready"
        assert len(fake_docker.containers.list(filters={"label": "forge.engine"})) == 1

        await manager.unload()
        assert manager.lease is None
        assert fake_docker.containers.list(filters={"label": "forge.engine"}) == []


# ── real healthwait paths (fake docker + mock transport) ────────────────────


class TestHealthwait:
    async def test_successful_load_reaches_ready(self, manager, fake_docker, httpx_mock):
        httpx_mock.set_handler(lambda request: httpx.Response(200, json={"data": []}))
        model = make_model(id=5)

        lease = await manager.load(model)
        assert lease.state == "starting"
        await asyncio.wait_for(manager._load_task, 10)
        assert lease.state == "ready"
        assert lease.error == ""

        # The engine container was created with the GPU + lane wiring.
        settings = get_settings()
        (container,) = fake_docker.containers.run_calls
        assert container.name == "forge-engine-llamacpp"
        assert container.image == settings.llamacpp_image
        kwargs = container.run_kwargs
        assert kwargs["network"] == settings.docker_network
        assert kwargs["labels"]["forge.engine"] == "llamacpp"
        assert kwargs["labels"]["forge.model_id"] == "5"
        assert kwargs["device_requests"], "engine containers must request the GPU"
        assert kwargs["restart_policy"] == {"Name": "no"}
        assert "--jinja" in kwargs["command"]

        # Healthcheck polled the lane's OpenAI surface.
        health = httpx_mock.requests[-1]
        assert health.url.host == "forge-engine-llamacpp"
        assert health.url.port == settings.llamacpp_port
        assert health.url.path == "/v1/models"

    async def test_container_start_failure_marks_lease_failed(
        self, manager, fake_docker, httpx_mock
    ):
        fake_docker.containers.fail_run = RuntimeError("no NVIDIA driver")
        lease = await manager.load(make_model(id=1))
        await asyncio.wait_for(manager._load_task, 10)
        assert lease.state == "failed"
        assert "container start failed" in lease.error
        assert "no NVIDIA driver" in lease.error

    async def test_engine_exit_during_healthwait_surfaces_log_tail(
        self, manager, fake_docker, httpx_mock
    ):
        fake_docker.containers.spawn_status = "exited"
        fake_docker.containers.logs_text = b"CUDA error: out of memory"

        lease = await manager.load(make_model(id=1))
        await asyncio.wait_for(manager._load_task, 10)
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
        await asyncio.wait_for(manager._load_task, 10)
        assert lease.state == "failed"
        assert "timed out" in lease.error
        (container,) = fake_docker.containers.run_calls
        assert container.removed is True
