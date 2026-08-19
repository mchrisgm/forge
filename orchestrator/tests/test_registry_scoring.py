"""Pure scoring tests for the model registry (PLAN §6.4).

No network: everything runs against literal inputs plus a fixture JSON of
HF-like candidates (tests/fixtures/hf_candidates.json) that drives an
end-to-end lane-assignment + score-ordering check.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.services.fit_rules import assign_lane
from app.services.registry import (
    coding_signal,
    estimate_params_b,
    is_moe,
    recency_decay,
    score_candidate,
    trend_rank_norm,
)

FIXTURE = Path(__file__).parent / "fixtures" / "hf_candidates.json"
NOW = datetime(2026, 8, 19, tzinfo=UTC)


# ── trend_rank_norm ─────────────────────────────────────────────────────────


class TestTrendRankNorm:
    def test_top_rank_is_one(self):
        assert trend_rank_norm(0, 100) == 1.0

    def test_last_rank_is_zero(self):
        assert trend_rank_norm(99, 100) == 0.0

    def test_midpoint(self):
        assert trend_rank_norm(50, 101) == pytest.approx(0.5)

    def test_single_item_listing(self):
        assert trend_rank_norm(0, 1) == 1.0
        assert trend_rank_norm(0, 0) == 1.0

    def test_rank_beyond_total_clamps_to_zero(self):
        assert trend_rank_norm(500, 100) == 0.0

    def test_strictly_decreasing_within_listing(self):
        values = [trend_rank_norm(rank, 10) for rank in range(10)]
        assert values == sorted(values, reverse=True)
        assert values[0] == 1.0 and values[-1] == 0.0


# ── recency_decay ───────────────────────────────────────────────────────────


class TestRecencyDecay:
    def test_exactly_half_at_60_days(self):
        assert recency_decay(NOW - timedelta(days=60), now=NOW) == pytest.approx(0.5)

    def test_one_at_zero_age(self):
        assert recency_decay(NOW, now=NOW) == pytest.approx(1.0)

    def test_quarter_at_two_half_lives(self):
        assert recency_decay(NOW - timedelta(days=120), now=NOW) == pytest.approx(0.25)

    def test_future_dates_clamp_to_one(self):
        assert recency_decay(NOW + timedelta(days=30), now=NOW) == pytest.approx(1.0)

    def test_naive_datetime_treated_as_utc(self):
        naive = datetime(2026, 6, 20)  # 60 days before NOW, no tzinfo
        assert recency_decay(naive, now=NOW) == pytest.approx(0.5)

    def test_custom_half_life(self):
        assert recency_decay(
            NOW - timedelta(days=30), now=NOW, half_life_days=30
        ) == pytest.approx(0.5)


# ── coding_signal ───────────────────────────────────────────────────────────


class TestCodingSignal:
    @pytest.mark.parametrize(
        "model_id",
        [
            "Qwen/Qwen2.5-Coder-14B-Instruct",
            "mistralai/Codestral-22B-v0.1",
            "mistralai/Devstral-Small-2505",
            "org/swe-bench-champion-7B",
        ],
    )
    def test_strong_coder_match_is_full_score(self, model_id):
        assert coding_signal(model_id, []) == 1.0

    def test_instruct_only_is_partial_score(self):
        assert coding_signal("meta-llama/Llama-3.1-8B-Instruct", []) == 0.6

    def test_unrelated_model_is_zero(self):
        assert coding_signal("org/StoryWriter-7B", []) == 0.0
        assert coding_signal("org/RolePlay-13B-chat", []) == 0.0

    def test_tags_contribute_to_the_signal(self):
        assert coding_signal("org/mystery-model", ["swe-bench"]) == 1.0
        assert coding_signal("org/mystery-model", ["instruct"]) == 0.6
        assert coding_signal("org/mystery-model", ["conversational"]) == 0.0

    def test_strong_beats_weak_when_both_present(self):
        assert coding_signal("Qwen/Qwen2.5-Coder-14B-Instruct", ["instruct"]) == 1.0


# ── estimate_params_b ───────────────────────────────────────────────────────


class TestEstimateParams:
    def test_dense_coder_name(self):
        assert estimate_params_b("Qwen/Qwen2.5-Coder-14B-Instruct") == 14.0

    def test_moe_name_takes_total_not_active(self):
        # "30B-A3B": 30B total params, 3B active — total must win.
        assert estimate_params_b("Qwen/Qwen3-Coder-30B-A3B-Instruct") == 30.0

    def test_fractional_size(self):
        assert estimate_params_b("org/tiny-1.5B-chat") == 1.5

    def test_safetensors_total_wins_over_name(self):
        assert estimate_params_b("org/Foo-7B", safetensors_total=32_000_000_000) == 32.0
        assert (
            estimate_params_b("Qwen/Qwen2.5-Coder-14B", safetensors_total=14_800_000_000)
            == 14.8
        )

    def test_zero_safetensors_falls_back_to_name(self):
        assert estimate_params_b("org/Foo-7B", safetensors_total=0) == 7.0

    def test_no_signal_returns_zero(self):
        assert estimate_params_b("org/mystery-model") == 0.0

    def test_b_followed_by_letters_is_not_a_size(self):
        # "8Bit" must not read as "8B params".
        assert estimate_params_b("org/quantized-8Bit-model") == 0.0


# ── is_moe ──────────────────────────────────────────────────────────────────


class TestIsMoe:
    def test_active_param_suffix(self):
        assert is_moe("Qwen/Qwen3-Coder-30B-A3B-Instruct", []) is True

    def test_mixtral_and_moe_keywords(self):
        assert is_moe("mistralai/Mixtral-8x7B-Instruct-v0.1", []) is True
        assert is_moe("org/plain-model", ["moe"]) is True

    def test_dense_models_are_not_moe(self):
        assert is_moe("Qwen/Qwen2.5-Coder-14B-Instruct", []) is False
        assert is_moe("meta-llama/Llama-3.1-8B-Instruct", []) is False


# ── score_candidate ─────────────────────────────────────────────────────────


class TestScoreCandidate:
    def test_perfect_candidate_scores_one(self):
        breakdown = score_candidate(
            rank=0,
            total=10,
            created_at=NOW,
            model_id="org/Great-Coder-7B",
            tags=[],
            lane="vllm",
            now=NOW,
        )
        assert breakdown["trend"] == 1.0
        assert breakdown["recency"] == 1.0
        assert breakdown["coding_signal"] == 1.0
        assert breakdown["fit"] == 1.0
        assert breakdown["score"] == pytest.approx(1.0)

    def test_weighted_sum_formula(self):
        # PLAN §6.4: 0.35·trend + 0.25·recency + 0.25·coding + 0.15·fit
        breakdown = score_candidate(
            rank=20,
            total=101,
            created_at=NOW - timedelta(days=60),
            model_id="meta-llama/Llama-3.1-8B-Instruct",
            tags=[],
            lane="llamacpp-full-gpu",
            now=NOW,
        )
        trend, recency, coding, fit = 0.8, 0.5, 0.6, 0.85
        assert breakdown["trend"] == pytest.approx(trend, abs=1e-3)
        assert breakdown["recency"] == pytest.approx(recency, abs=1e-3)
        assert breakdown["coding_signal"] == pytest.approx(coding, abs=1e-3)
        assert breakdown["fit"] == pytest.approx(fit, abs=1e-3)
        expected = 0.35 * trend + 0.25 * recency + 0.25 * coding + 0.15 * fit
        assert breakdown["score"] == pytest.approx(expected, abs=2e-3)

    def test_no_lane_zeroes_the_fit_component(self):
        breakdown = score_candidate(
            rank=0,
            total=10,
            created_at=NOW,
            model_id="org/Great-Coder-999B",
            tags=[],
            lane=None,
            now=NOW,
        )
        assert breakdown["fit"] == 0.0
        assert breakdown["lane"] is None
        assert breakdown["score"] == pytest.approx(0.85)  # 0.35 + 0.25 + 0.25

    def test_breakdown_carries_the_lane(self):
        breakdown = score_candidate(
            rank=0, total=2, created_at=NOW, model_id="x/y-7B",
            tags=[], lane="airllm", now=NOW,
        )
        assert breakdown["lane"] == "airllm"

    def test_components_recombine_into_score(self):
        # For arbitrary inputs the published score must always equal the
        # weighted sum of the published components (modulo rounding).
        for rank, days, model_id, lane in [
            (3, 10, "a/b-coder-7B", "vllm"),
            (50, 200, "a/plain-70B", "airllm"),
            (99, 0, "a/thing-13B-Instruct", "llamacpp-offload"),
        ]:
            b = score_candidate(
                rank=rank, total=100, created_at=NOW - timedelta(days=days),
                model_id=model_id, tags=[], lane=lane, now=NOW,
            )
            expected = (
                0.35 * b["trend"]
                + 0.25 * b["recency"]
                + 0.25 * b["coding_signal"]
                + 0.15 * b["fit"]
            )
            assert b["score"] == pytest.approx(expected, abs=2e-3)


# ── fixture-JSON-driven ordering ────────────────────────────────────────────


class TestFixtureDrivenScoring:
    @pytest.fixture(scope="class")
    def fixture_data(self):
        return json.loads(FIXTURE.read_text())

    def test_lane_assignment_per_candidate(self, fixture_data):
        for c in fixture_data["candidates"]:
            lane = assign_lane(
                c["params_b"],
                c["gguf_size_gb"],
                c["has_awq"],
                is_moe(c["id"], c["tags"]),
            )
            assert lane == c["expected_lane"], c["id"]

    def _score_all(self, fixture_data):
        now = datetime.fromisoformat(fixture_data["now"])
        total = fixture_data["total"]
        scored = {}
        for c in fixture_data["candidates"]:
            lane = assign_lane(
                c["params_b"], c["gguf_size_gb"], c["has_awq"], is_moe(c["id"], c["tags"])
            )
            scored[c["id"]] = score_candidate(
                rank=c["rank"],
                total=total,
                created_at=datetime.fromisoformat(c["created_at"]),
                model_id=c["id"],
                tags=c["tags"],
                lane=lane,
                now=now,
            )
        return scored

    def test_score_ordering_matches_expected(self, fixture_data):
        scored = self._score_all(fixture_data)
        ordered = sorted(scored, key=lambda mid: scored[mid]["score"], reverse=True)
        assert ordered == fixture_data["expected_order"]

    def test_fresh_awq_coder_dominates(self, fixture_data):
        scored = self._score_all(fixture_data)
        best = scored["qwen/NewCoder-14B-AWQ"]
        assert best["score"] > 0.9
        assert best["lane"] == "vllm"

    def test_misfit_scores_lowest_with_zero_fit_and_coding(self, fixture_data):
        scored = self._score_all(fixture_data)
        worst = scored["org/Colossus-200B"]
        assert worst["fit"] == 0.0
        assert worst["coding_signal"] == 0.0
        assert worst["score"] < 0.2
        assert all(worst["score"] <= s["score"] for s in scored.values())

    def test_scores_are_distinct_enough_to_rank(self, fixture_data):
        scores = sorted(
            (b["score"] for b in self._score_all(fixture_data).values()), reverse=True
        )
        gaps = [a - b for a, b in zip(scores, scores[1:], strict=False)]
        assert min(gaps) > 0.01
