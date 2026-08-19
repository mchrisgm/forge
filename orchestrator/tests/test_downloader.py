"""Snapshot pruning for the imagegen lane (_diffusers_ignore_patterns) and
expected-size accounting (_expected_bytes) — HfApi is faked at the downloader
module boundary, so no test ever dials the Hub."""

from types import SimpleNamespace

import pytest

from app.services import downloader

FORMAT_PATTERNS = ["*.ckpt", "*.onnx", "*.onnx_data", "*.msgpack", "*.h5", "*.tflite"]


def sibling(rfilename: str, size: int) -> SimpleNamespace:
    return SimpleNamespace(rfilename=rfilename, size=size)


@pytest.fixture
def hf(monkeypatch) -> SimpleNamespace:
    """Fake HfApi driven by mutable state: `files` feeds list_repo_files,
    `siblings` feeds model_info, `error` makes both raise."""
    state = SimpleNamespace(files=[], siblings=[], error=None)

    class FakeHfApi:
        def __init__(self, token=None):
            state.token = token

        def list_repo_files(self, repo):
            if state.error is not None:
                raise state.error
            return list(state.files)

        def model_info(self, repo, files_metadata=False):
            if state.error is not None:
                raise state.error
            return SimpleNamespace(siblings=list(state.siblings))

    monkeypatch.setattr(downloader, "HfApi", FakeHfApi)
    return state


# ── _diffusers_ignore_patterns ──────────────────────────────────────────────


class TestDiffusersIgnorePatterns:
    def test_non_diffusers_repo_gets_only_the_format_patterns(self, hf):
        hf.files = ["config.json", "model.safetensors", "tokenizer.json"]
        assert downloader._diffusers_ignore_patterns("org/model", None) == (
            FORMAT_PATTERNS
        )

    def test_diffusers_repo_prunes_root_checkpoints_and_fp32_twins(self, hf):
        hf.files = [
            "model_index.json",
            "sd_xl_turbo_1.0.safetensors",  # root single-file checkpoint
            "sd_xl_turbo_1.0_fp16.bin",  # root legacy checkpoint
            "unet/diffusion_pytorch_model.fp16.safetensors",
            "unet/diffusion_pytorch_model.safetensors",  # fp32 twin
            "text_encoder/model.fp16.safetensors",
            "text_encoder/model.safetensors",  # fp32 twin
            "vae/diffusion_pytorch_model.fp16.safetensors",  # no twin shipped
            "scheduler/scheduler_config.json",
        ]
        patterns = downloader._diffusers_ignore_patterns("org/sdxl", None)
        for base in FORMAT_PATTERNS:
            assert base in patterns
        # Root-level checkpoints are skipped either way.
        assert "sd_xl_turbo_1.0.safetensors" in patterns
        assert "sd_xl_turbo_1.0_fp16.bin" in patterns
        # fp32 twins of shipped fp16 variants are skipped; fp16 files are kept.
        assert "unet/diffusion_pytorch_model.safetensors" in patterns
        assert "text_encoder/model.safetensors" in patterns
        assert "unet/diffusion_pytorch_model.fp16.safetensors" not in patterns
        assert "vae/diffusion_pytorch_model.fp16.safetensors" not in patterns
        # Subfolder safetensors exist, so legacy .bin twins are skipped too.
        assert "*.bin" in patterns

    def test_no_bin_wildcard_without_subfolder_safetensors(self, hf):
        hf.files = ["model_index.json", "unet/diffusion_pytorch_model.bin"]
        patterns = downloader._diffusers_ignore_patterns("org/legacy", None)
        assert "*.bin" not in patterns
        assert patterns == FORMAT_PATTERNS

    def test_fp16_without_a_shipped_fp32_twin_prunes_nothing_extra(self, hf):
        hf.files = ["model_index.json", "unet/model.fp16.safetensors"]
        patterns = downloader._diffusers_ignore_patterns("org/fp16-only", None)
        assert "unet/model.fp16.safetensors" not in patterns
        assert "unet/model.safetensors" not in patterns

    def test_listing_failure_falls_back_to_the_format_patterns(self, hf):
        hf.error = RuntimeError("hub is down")
        assert downloader._diffusers_ignore_patterns("org/model", None) == (
            FORMAT_PATTERNS
        )


# ── _expected_bytes ─────────────────────────────────────────────────────────


class TestExpectedBytes:
    def test_named_file_returns_its_size(self, hf):
        hf.siblings = [sibling("a.gguf", 111), sibling("b.gguf", 222)]
        assert downloader._expected_bytes("org/model", "b.gguf", None) == 222

    def test_missing_named_file_is_zero(self, hf):
        hf.siblings = [sibling("a.gguf", 111)]
        assert downloader._expected_bytes("org/model", "missing.gguf", None) == 0

    def test_snapshot_sum_honors_the_ignore_list(self, hf):
        hf.siblings = [
            sibling("model_index.json", 10),
            sibling("unet/model.fp16.safetensors", 1000),
            sibling("unet/model.safetensors", 2000),
            sibling("root.safetensors", 4000),
            sibling("x.onnx", 500),
        ]
        ignore = ["root.safetensors", "unet/model.safetensors", "*.onnx"]
        assert downloader._expected_bytes("org/model", None, None, ignore) == 1010

    def test_snapshot_sum_without_ignores_counts_everything(self, hf):
        hf.siblings = [sibling("a.safetensors", 100), sibling("b.bin", 200)]
        assert downloader._expected_bytes("org/model", None, None) == 300

    def test_api_failure_is_zero(self, hf):
        hf.error = RuntimeError("hub is down")
        assert downloader._expected_bytes("org/model", None, None) == 0

    def test_pruning_and_size_accounting_agree(self, hf):
        """The ignore list _diffusers_ignore_patterns computes is exactly what
        _expected_bytes subtracts — the progress total matches the download."""
        hf.files = [
            "model_index.json",
            "root.safetensors",
            "unet/m.fp16.safetensors",
            "unet/m.safetensors",
        ]
        hf.siblings = [
            sibling("model_index.json", 10),
            sibling("root.safetensors", 4000),
            sibling("unet/m.fp16.safetensors", 1000),
            sibling("unet/m.safetensors", 2000),
        ]
        ignore = downloader._diffusers_ignore_patterns("org/sdxl", None)
        assert downloader._expected_bytes("org/sdxl", None, None, ignore) == 1010
