"""Exhaustive tests for the pure hardware-fit rules (PLAN §2, §9).

These encode the budget table from PLAN §9 verbatim: the §9 examples are the
parametrized cases in TestAssignLaneMatrix.
"""

import pytest

from app.services.fit_rules import (
    FIT_QUALITY,
    GB,
    Budgets,
    assign_lane,
    compute_ngl,
    estimate_n_layers,
    fits_airllm,
    fits_llamacpp,
    fits_llamacpp_full_gpu,
    fits_vllm,
    kv_cache_gb,
)

DEFAULT = Budgets()  # 11 GB VRAM, 32 GB RAM offload — PLAN §2


# ── estimate_n_layers ───────────────────────────────────────────────────────


class TestEstimateNLayers:
    @pytest.mark.parametrize(
        ("params_b", "expected"),
        [
            (1.0, 24), (3.0, 28), (7.0, 32), (14.0, 40),
            (15.0, 40), (30.0, 48), (70.0, 80), (200.0, 80),
        ],
    )
    def test_table(self, params_b, expected):
        assert estimate_n_layers(params_b) == expected

    def test_unknown_size_defaults_to_32(self):
        assert estimate_n_layers(0) == 32
        assert estimate_n_layers(-1) == 32

    def test_monotonic_nondecreasing(self):
        sizes = [0.5, 1.5, 3, 7, 13, 14, 20, 30, 40, 60, 70, 100]
        layers = [estimate_n_layers(s) for s in sizes]
        assert layers == sorted(layers)


# ── kv_cache_gb ─────────────────────────────────────────────────────────────


class TestKvCache:
    def test_exact_values(self):
        # 4 KiB per token per layer: 48 layers * 16k ctx = exactly 3 GiB.
        assert kv_cache_gb(48, 16384) == pytest.approx(3.0)
        assert kv_cache_gb(40, 16384) == pytest.approx(2.5)
        assert kv_cache_gb(1, 16384) == pytest.approx(0.0625)

    def test_zero_inputs(self):
        assert kv_cache_gb(0, 16384) == 0.0
        assert kv_cache_gb(48, 0) == 0.0

    def test_linear_in_layers_and_ctx(self):
        base = kv_cache_gb(10, 8192)
        assert kv_cache_gb(20, 8192) == pytest.approx(2 * base)
        assert kv_cache_gb(10, 16384) == pytest.approx(2 * base)

    def test_sane_magnitude_for_target_models(self):
        # A 14B-class model at 16k ctx must leave most of 11 GB for weights.
        assert 1.0 < kv_cache_gb(estimate_n_layers(14), 16384) < 4.0


# ── compute_ngl ─────────────────────────────────────────────────────────────


def _vram_needed(ngl: int, file_size_gb: float, n_layers: int, ctx: int) -> float:
    """Independent re-derivation of the engine's per-layer VRAM model."""
    layer_gb = file_size_gb / n_layers
    return ngl * (layer_gb + kv_cache_gb(1, ctx)) + 1.2  # 1.2 = default overhead


class TestComputeNgl:
    def test_zero_or_negative_file_size_gives_zero_layers(self):
        assert compute_ngl(0.0, 40, 16384, 11.0) == 0
        assert compute_ngl(-3.0, 40, 16384, 11.0) == 0

    def test_zero_layers_gives_zero(self):
        assert compute_ngl(9.0, 0, 16384, 11.0) == 0

    def test_tiny_model_gets_all_layers(self):
        # 2 GB file, 24 layers, 4k ctx: trivially fits 11 GB fully on GPU.
        assert compute_ngl(2.0, 24, 4096, 11.0) == 24

    def test_big_model_gets_partial_layers(self):
        # 18 GB GGUF (30B-A3B class): layer=0.375 GB, kv/layer=0.0625 GB,
        # (11 - 1.2) / 0.4375 = 22.4 -> 22 layers on GPU, rest to RAM.
        ngl = compute_ngl(18.0, 48, 16384, 11.0)
        assert 0 < ngl < 48
        assert ngl == 22

    def test_budget_smaller_than_overhead_gives_zero(self):
        assert compute_ngl(9.0, 40, 16384, 1.0) == 0

    @pytest.mark.parametrize(
        ("file_size_gb", "n_layers", "ctx", "budget"),
        [
            (9.0, 40, 16384, 11.0),
            (18.0, 48, 16384, 11.0),
            (4.5, 32, 8192, 11.0),
            (36.0, 48, 16384, 11.0),
            (2.0, 24, 4096, 11.0),
            (12.0, 40, 32768, 24.0),
            (7.0, 32, 16384, 6.0),
        ],
    )
    def test_result_is_maximal_within_budget(self, file_size_gb, n_layers, ctx, budget):
        ngl = compute_ngl(file_size_gb, n_layers, ctx, budget)
        assert 0 <= ngl <= n_layers
        if ngl > 0:
            assert _vram_needed(ngl, file_size_gb, n_layers, ctx) <= budget
        if ngl < n_layers:
            assert _vram_needed(ngl + 1, file_size_gb, n_layers, ctx) > budget

    def test_monotonic_nonincreasing_in_file_size(self):
        ngls = [compute_ngl(size, 48, 16384, 11.0) for size in (4, 9, 18, 30, 43)]
        assert ngls == sorted(ngls, reverse=True)

    def test_monotonic_nonincreasing_in_ctx(self):
        ngls = [compute_ngl(18.0, 48, ctx, 11.0) for ctx in (2048, 8192, 16384, 32768)]
        assert ngls == sorted(ngls, reverse=True)

    def test_monotonic_nondecreasing_in_budget(self):
        ngls = [compute_ngl(18.0, 48, 16384, budget) for budget in (4, 8, 11, 16, 24)]
        assert ngls == sorted(ngls)


# ── fits_vllm boundary (PLAN §2: "roughly ≤ 14–15B at 16k context") ─────────


class TestFitsVllmBoundary:
    def test_14b_fits_at_16k(self):
        assert fits_vllm(14.0, 16384, DEFAULT) is True

    def test_15b_does_not_fit_at_16k(self):
        # weights 8.25 + kv 2.5 + overhead 0.6 = 11.35 > 11
        assert fits_vllm(15.0, 16384, DEFAULT) is False

    def test_15b_fits_at_8k(self):
        # Halving ctx halves KV: 8.25 + 1.25 + 0.6 = 10.1 <= 11
        assert fits_vllm(15.0, 8192, DEFAULT) is True

    def test_clearly_out_of_range(self):
        assert fits_vllm(30.0, 16384, DEFAULT) is False
        assert fits_vllm(70.0, 16384, DEFAULT) is False

    def test_zero_and_negative_params_never_fit(self):
        assert fits_vllm(0.0, 16384, DEFAULT) is False
        assert fits_vllm(-5.0, 16384, DEFAULT) is False

    def test_small_model_easily_fits(self):
        assert fits_vllm(7.0, 16384, DEFAULT) is True


# ── llama.cpp lanes ─────────────────────────────────────────────────────────


class TestFitsLlamacpp:
    def test_offload_cap_is_vram_plus_ram(self):
        assert fits_llamacpp(43.0, DEFAULT) is True  # exactly 11 + 32
        assert fits_llamacpp(43.1, DEFAULT) is False
        assert fits_llamacpp(0.0, DEFAULT) is False

    def test_full_gpu_cap_is_vram_minus_headroom(self):
        # PLAN §9: full-GPU lane <= ~10 GB file at the 11 GB budget.
        assert fits_llamacpp_full_gpu(10.0, DEFAULT) is True
        assert fits_llamacpp_full_gpu(10.1, DEFAULT) is False
        assert fits_llamacpp_full_gpu(0.0, DEFAULT) is False


class TestFitsAirllm:
    def test_bounds(self):
        assert fits_airllm(70.0) is True
        assert fits_airllm(70.1) is False
        assert fits_airllm(0.0) is False
        assert fits_airllm(1.0) is True


# ── assign_lane: the PLAN §9 matrix ─────────────────────────────────────────


class TestAssignLaneMatrix:
    @pytest.mark.parametrize(
        ("params_b", "gguf_size_gb", "has_awq", "is_moe", "expected"),
        [
            # 14B AWQ (Qwen coder 14B AWQ) -> vLLM fast lane
            (14.0, None, True, False, "vllm"),
            # 14B Q4_K_M GGUF ~9 GB -> llama.cpp full-GPU
            (14.0, 9.0, False, False, "llamacpp-full-gpu"),
            # 30B-A3B MoE GGUF ~18 GB -> llama.cpp offload
            (30.0, 18.0, False, True, "llamacpp-offload"),
            # 70B with no GGUF/AWQ artifacts -> AirLLM chat-only lane
            (70.0, None, False, False, "airllm"),
            # 200B fits nowhere
            (200.0, None, False, False, None),
        ],
    )
    def test_plan_section9_examples(self, params_b, gguf_size_gb, has_awq, is_moe, expected):
        assert assign_lane(params_b, gguf_size_gb, has_awq, is_moe) == expected

    def test_vllm_preferred_over_gguf_when_both_available(self):
        assert assign_lane(14.0, 9.0, True, False) == "vllm"

    def test_awq_too_big_for_vllm_falls_back_to_gguf(self):
        # 30B AWQ doesn't fit vLLM; its 18 GB GGUF lands in the offload lane.
        assert assign_lane(30.0, 18.0, True, True) == "llamacpp-offload"

    def test_gguf_too_big_for_offload_falls_back_to_airllm(self):
        # 45 GB GGUF > 11 + 32; a 70B still qualifies for AirLLM.
        assert assign_lane(70.0, 45.0, False, False) == "airllm"

    def test_200b_gguf_still_fits_nowhere(self):
        assert assign_lane(200.0, 120.0, False, True) is None

    def test_dense_offload_is_allowed_but_ranked_lowest_of_llamacpp(self):
        # Dense 30B GGUF is offload too (MoE preference is a scoring concern).
        assert assign_lane(30.0, 18.0, False, False) == "llamacpp-offload"

    def test_fit_quality_ordering_matches_lane_preference(self):
        assert (
            FIT_QUALITY["vllm"]
            > FIT_QUALITY["llamacpp-full-gpu"]
            > FIT_QUALITY["llamacpp-offload"]
            > FIT_QUALITY["airllm"]
            > 0.0
        )


# ── Budgets overrides ───────────────────────────────────────────────────────


class TestBudgetOverrides:
    def test_bigger_vram_promotes_offload_model_to_full_gpu(self):
        big = Budgets(vram_gb=24.0, ram_offload_gb=32.0)
        assert assign_lane(30.0, 18.0, False, True, budgets=big) == "llamacpp-full-gpu"

    def test_bigger_vram_lets_larger_awq_into_vllm(self):
        big = Budgets(vram_gb=24.0, ram_offload_gb=32.0)
        assert fits_vllm(30.0, 16384, big) is True
        assert assign_lane(30.0, None, True, True, budgets=big) == "vllm"

    def test_smaller_vram_demotes_14b_awq_to_airllm(self):
        small = Budgets(vram_gb=6.0, ram_offload_gb=32.0)
        assert fits_vllm(14.0, 16384, small) is False
        assert assign_lane(14.0, None, True, False, budgets=small) == "airllm"

    def test_smaller_ram_shrinks_offload_lane(self):
        tight = Budgets(vram_gb=11.0, ram_offload_gb=8.0)
        assert fits_llamacpp(18.0, tight) is True  # 18 <= 19
        assert fits_llamacpp(20.0, tight) is False
        assert assign_lane(70.0, 40.0, False, False, budgets=tight) == "airllm"

    def test_compute_ngl_uses_the_given_budget(self):
        assert compute_ngl(18.0, 48, 16384, 24.0) > compute_ngl(18.0, 48, 16384, 11.0)

    def test_default_budgets_match_plan_section2(self):
        assert DEFAULT.vram_gb == 11.0
        assert DEFAULT.ram_offload_gb == 32.0

    def test_gb_constant_is_binary(self):
        assert GB == 1024**3
