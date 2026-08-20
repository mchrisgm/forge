"""Config-aware automatic engine detection + the SGLang lane.

Ground truth over guesswork: detect_lane reads what the checkpoint's
config.json actually says (architectures, model_type, quantization_config,
custom code) instead of inferring a lane from the repo name — the failure
that put RadixArk/Kimi-K3-DSpark (a speculative-decoding DRAFT model) on the
AirLLM lane."""

from types import SimpleNamespace

import pytest

from app.config import get_settings
from app.models import EngineKind, ModelStatus, Quant, ToolCallFormat
from app.routers import models_api as models_api_module
from app.services.chat_jobs import lease_capacity
from app.services.engine_manager import build_sglang_command, engine_port
from app.services.fit_rules import Budgets, HubFacts, detect_lane

from .conftest import add_model
from .test_models_search_api import TEXT_REPO, entry_for


@pytest.fixture
def resolved(monkeypatch) -> dict:
    """resolve_text_candidate stub (same shape as test_models_search_api's)."""
    state = {
        "lane": "sglang",
        "reason": "",
        "params_b": 4.0,
        "is_moe": False,
        "gguf_repo": None,
        "gguf_file": None,
        "gguf_size_gb": 0.0,
    }
    monkeypatch.setattr(
        models_api_module,
        "resolve_text_candidate",
        lambda repo, token=None: dict(state),
    )
    return state


@pytest.fixture
def download_spy(monkeypatch) -> list[str]:
    from app.services import downloader

    calls: list[str] = []

    async def fake_start_download(entry, token=None) -> None:
        calls.append(entry.hf_repo)

    monkeypatch.setattr(downloader, "start_download", fake_start_download)
    return calls


# ── detect_lane policy matrix ───────────────────────────────────────────────


class TestDetectLane:
    def test_the_real_kimi_k3_dspark_shape_is_named_a_draft_model(self):
        # Exactly what huggingface.co/RadixArk/Kimi-K3-DSpark publishes:
        # 2.25B params, model_type qwen3, custom-code DSparkDraftModel arch,
        # sglang/speculative-decoding tags.
        facts = HubFacts(
            params_b=2.25,
            model_type="qwen3",
            architectures=("DSparkDraftModel",),
            custom_code=True,
            tags=("sglang", "speculative-decoding", "dspark", "dflash"),
        )
        lane, reason = detect_lane(facts)
        assert lane is None
        assert "DRAFT model" in reason
        assert "cannot chat on its own" in reason

    def test_small_bf16_chat_model_goes_to_sglang(self):
        lane, reason = detect_lane(
            HubFacts(params_b=3.0, model_type="qwen3", architectures=("Qwen3ForCausalLM",))
        )
        assert lane == "sglang"
        assert "SGLang" in reason

    def test_prequantized_checkpoint_that_fits_goes_to_sglang(self):
        lane, _ = detect_lane(
            HubFacts(params_b=7.0, model_type="llama", quant_method="awq")
        )
        assert lane == "sglang"

    def test_separate_awq_variant_keeps_the_vllm_lane(self):
        # bf16 doesn't fit; a *-AWQ sibling build does — vLLM's classic path.
        lane, _ = detect_lane(
            HubFacts(params_b=13.0, model_type="llama", has_awq_variant=True)
        )
        assert lane == "vllm"

    def test_gguf_beats_airllm_for_big_models(self):
        lane, _ = detect_lane(
            HubFacts(params_b=32.0, model_type="qwen2", gguf_size_gb=19.0)
        )
        assert lane == "llamacpp-offload"

    def test_custom_code_arch_without_gguf_is_honestly_unrunnable(self):
        lane, reason = detect_lane(
            HubFacts(
                params_b=30.0,
                model_type="weird",
                architectures=("WeirdForCausalLM",),
                custom_code=True,
            )
        )
        assert lane is None
        assert "custom-code architecture" in reason

    def test_huge_standard_model_still_falls_back_to_airllm(self):
        lane, _ = detect_lane(HubFacts(params_b=65.0, model_type="llama"))
        assert lane == "airllm"

    def test_sglang_preferred_family_wins_when_it_fits(self):
        lane, _ = detect_lane(
            HubFacts(params_b=3.0, model_type="kimi_k3", quant_method="fp8")
        )
        assert lane == "sglang"

    def test_tiny_vram_budget_pushes_bf16_out_of_the_gpu_lane(self):
        lane, _ = detect_lane(
            HubFacts(params_b=8.0, model_type="llama"),
            budgets=Budgets(vram_gb=6.0),
        )
        assert lane == "airllm"  # 16GB of bf16 weights can't fit 6GB


# ── SGLang engine plumbing ──────────────────────────────────────────────────


class TestSglangEngine:
    def make(self, **kw):
        defaults = dict(
            id=7,
            hf_repo="Qwen/Qwen3-4B-Instruct-2507",
            display_name="Qwen3 4B",
            file_path="hf/Qwen__Qwen3-4B-Instruct-2507",
            engine=EngineKind.sglang,
            quant=Quant.safetensors,
            ctx_max=16384,
            params_b=4.0,
            tool_call_format=ToolCallFormat.hermes,
        )
        defaults.update(kw)
        return SimpleNamespace(**defaults)

    def test_launch_command_serves_the_slug_on_the_lane_port(self):
        settings = get_settings()
        cmd = build_sglang_command(self.make(), settings)
        joined = " ".join(cmd)
        assert "python3 -m sglang.launch_server" in joined
        assert "--model-path /data/models/hf/Qwen__Qwen3-4B-Instruct-2507" in joined
        assert f"--port {settings.sglang_port}" in joined
        assert "--tool-call-parser qwen25" in joined
        assert "--tp-size" not in joined

    def test_tensor_parallel_and_no_tools(self):
        settings = get_settings()
        cmd = build_sglang_command(
            self.make(tool_call_format=ToolCallFormat.none), settings, tensor_parallel=2
        )
        joined = " ".join(cmd)
        assert "--tp-size 2" in joined
        assert "--tool-call-parser" not in joined

    def test_port_map_and_chat_capacity(self):
        settings = get_settings()
        assert engine_port(EngineKind.sglang, settings) == settings.sglang_port
        lease = SimpleNamespace(engine=EngineKind.sglang)
        assert lease_capacity(lease) == settings.sglang_max_concurrency


# ── the add/refit paths pick sglang automatically ───────────────────────────


class TestSglangSelection:
    def test_search_add_maps_the_sglang_lane(
        self, api, auth_headers, resolved, download_spy
    ):
        resolved.update(lane="sglang", reason="bf16 fits", params_b=4.0)
        r = api.post(
            "/api/models/search/add",
            json={"hf_repo": TEXT_REPO, "kind": "text"},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        entry = entry_for(TEXT_REPO)
        assert entry.engine == EngineKind.sglang
        assert entry.quant == Quant.safetensors
        assert download_spy == [TEXT_REPO]

    def test_refit_can_move_an_airllm_model_to_sglang(
        self, api, auth_headers, resolved, download_spy
    ):
        model_id = add_model(
            hf_repo=TEXT_REPO,
            engine=EngineKind.airllm,
            quant=Quant.fp16_airllm,
            file_path="",
            status=ModelStatus.ready,
        )
        resolved.update(lane="sglang", reason="bf16 fits", params_b=7.0)
        r = api.post(f"/api/models/{model_id}/refit", headers=auth_headers)
        assert r.status_code == 200, r.text
        assert r.json()["engine"] == "sglang"

    def test_manual_add_without_engine_detects_automatically(
        self, api, auth_headers, resolved, download_spy
    ):
        resolved.update(lane="sglang", reason="bf16 checkpoint fits in VRAM")
        r = api.post(
            "/api/models",
            json={"hf_repo": TEXT_REPO},  # no engine given — auto
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["engine"] == "sglang"
        assert "detected automatically" in body["note"]
        assert download_spy == [TEXT_REPO]

    def test_manual_add_of_a_draft_model_is_refused_with_the_reason(
        self, api, auth_headers, resolved, download_spy
    ):
        resolved.update(
            lane=None,
            reason="this is a speculative-decoding DRAFT model (DSparkDraftModel)",
        )
        r = api.post(
            "/api/models",
            json={"hf_repo": "RadixArk/Kimi-K3-DSpark"},
            headers=auth_headers,
        )
        assert r.status_code == 409
        assert "DRAFT model" in r.json()["detail"]
        assert download_spy == []

    def test_manual_add_with_explicit_engine_skips_detection(
        self, api, auth_headers, download_spy, monkeypatch
    ):
        def boom(repo, token=None):
            raise AssertionError("resolution must not run for explicit engines")

        monkeypatch.setattr(models_api_module, "resolve_text_candidate", boom)
        r = api.post(
            "/api/models",
            json={
                "hf_repo": TEXT_REPO,
                "engine": "llamacpp",
                "gguf_filename": "model-q4_k_m.gguf",
            },
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["engine"] == "llamacpp"
