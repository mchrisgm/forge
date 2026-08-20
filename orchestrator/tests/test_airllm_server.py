"""Regression tests for the AirLLM slow-lane server's split-cache repair.

The server module (engines/airllm/server.py) defers its heavy imports (airllm,
torch) into _load(), so it imports here with only fastapi/pydantic present.
_repair_split_cache is pure stdlib, so we can exercise it directly against
temp directories — no GPU, no model.

Guards the reported failure: an AirLLM split cache whose shard went missing
while its .done marker survived made AirLLM trust the marker and later die with
'No such file … model.embed_tokens.safetensors'.
"""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

_SERVER = Path(__file__).resolve().parents[2] / "engines" / "airllm" / "server.py"


@pytest.fixture(scope="module")
def airllm_server():
    spec = importlib.util.spec_from_file_location("airllm_server_under_test", _SERVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_cache(tmp_path: Path, files: dict[str, int]) -> Path:
    """Build <tmp>/splitted_model.4bit with the given {filename: size} set."""
    split = tmp_path / "splitted_model.4bit"
    split.mkdir(parents=True)
    for name, size in files.items():
        (split / name).write_bytes(b"x" * size)
    return split


def test_healthy_cache_is_preserved(airllm_server, tmp_path):
    split = make_cache(
        tmp_path,
        {
            "model.embed_tokens.safetensors": 10,
            "model.embed_tokens.safetensors.done": 0,
            "model.norm.safetensors": 10,
            "model.norm.safetensors.done": 0,
        },
    )
    airllm_server._repair_split_cache(str(tmp_path))
    assert split.exists()
    assert (split / "model.embed_tokens.safetensors").exists()


def test_marker_without_shard_is_cleared(airllm_server, tmp_path):
    # The exact reported failure: a .done marker whose shard is gone.
    split = make_cache(
        tmp_path,
        {
            "model.embed_tokens.safetensors.done": 0,
            "model.norm.safetensors": 10,
            "model.norm.safetensors.done": 0,
        },
    )
    airllm_server._repair_split_cache(str(tmp_path))
    assert not split.exists()


def test_empty_shard_is_cleared(airllm_server, tmp_path):
    split = make_cache(
        tmp_path,
        {
            "model.embed_tokens.safetensors": 0,  # truncated write
            "model.embed_tokens.safetensors.done": 0,
        },
    )
    airllm_server._repair_split_cache(str(tmp_path))
    assert not split.exists()


def test_shard_without_marker_is_cleared(airllm_server, tmp_path):
    # Killed mid-write: the shard exists but was never marked done.
    split = make_cache(tmp_path, {"model.embed_tokens.safetensors": 10})
    airllm_server._repair_split_cache(str(tmp_path))
    assert not split.exists()


def test_junk_without_markers_is_cleared(airllm_server, tmp_path):
    split = make_cache(tmp_path, {"leftover.bin": 5})
    airllm_server._repair_split_cache(str(tmp_path))
    assert not split.exists()


def test_absent_cache_is_a_noop(airllm_server, tmp_path):
    # No split dir at all — nothing to repair, must not raise.
    airllm_server._repair_split_cache(str(tmp_path))
    assert not (tmp_path / "splitted_model.4bit").exists()


def test_empty_cache_dir_is_preserved(airllm_server, tmp_path):
    # A freshly-created empty dir is not "corrupt" — AirLLM will populate it.
    split = tmp_path / "splitted_model.4bit"
    split.mkdir()
    airllm_server._repair_split_cache(str(tmp_path))
    assert split.exists()


# ── streamability pre-flight + split completeness (kimi-k3 class of failure) ─
#
# Reported: an unrecognized architecture fell back to AirLLM's generic class,
# whose expected llama-style layer names matched NO checkpoint tensors — the
# split "completed" writing nothing (a vacuous all() over an empty layer list)
# and generation died with 'No such file … model.embed_tokens.safetensors'.

LLAMA_NAMES = {"embed": "model.embed_tokens", "layer_prefix": "model.layers",
               "norm": "model.norm", "lm_head": "lm_head"}


class TestCheckpointTensorNames:
    def test_reads_the_safetensors_index(self, airllm_server, tmp_path):
        (tmp_path / "model.safetensors.index.json").write_text(
            json.dumps({"weight_map": {"model.embed_tokens.weight": "a", "model.layers.0.x": "a"}})
        )
        names = airllm_server._checkpoint_tensor_names(str(tmp_path))
        assert "model.embed_tokens.weight" in names

    def test_repo_id_and_broken_index_yield_none(self, airllm_server, tmp_path):
        assert airllm_server._checkpoint_tensor_names("some/hf-repo") is None
        (tmp_path / "model.safetensors.index.json").write_text("{not json")
        assert airllm_server._checkpoint_tensor_names(str(tmp_path)) is None


class TestStreamabilityError:
    def test_matching_layout_is_streamable(self, airllm_server):
        names = ["model.embed_tokens.weight", "model.layers.0.self_attn.q_proj.weight"]
        assert airllm_server._streamability_error(names, "AirLLMBaseModel", LLAMA_NAMES) is None

    def test_mismatched_layout_is_rejected_with_a_clear_reason(self, airllm_server):
        # The kimi-k3 shape: tensors live under language_model.*, the generic
        # class expects model.* — nothing matches.
        names = ["language_model.model.embed_tokens.weight",
                 "language_model.model.layers.0.mlp.w1.weight"]
        error = airllm_server._streamability_error(names, "AirLLMBaseModel", LLAMA_NAMES)
        assert error is not None
        assert "not streamable" in error
        assert "AirLLMBaseModel" in error
        assert "model.embed_tokens" in error
        assert "language_model.model.embed_tokens.weight" in error  # what it found

    def test_partial_match_missing_layers_is_rejected(self, airllm_server):
        names = ["model.embed_tokens.weight", "transformer.h.0.attn.weight"]
        error = airllm_server._streamability_error(names, "AirLLMBaseModel", LLAMA_NAMES)
        assert error is not None and "model.layers" in error


def make_split_model(tmp_path, layer_names, shards: dict[str, int]):
    """A fake loaded AirLLM model + its split dir holding the given shards."""
    split = tmp_path / "splitted_model.4bit"
    split.mkdir(parents=True, exist_ok=True)
    for name, size in shards.items():
        (split / name).write_bytes(b"x" * size)
    return SimpleNamespace(
        checkpoint_path=str(split),
        layer_names=layer_names,
        layer_names_dict=dict(LLAMA_NAMES),
    )


class TestVerifySplitComplete:
    LAYERS = ["model.embed_tokens", "model.layers.0", "model.layers.1",
              "model.norm", "lm_head"]

    def test_complete_split_passes(self, airllm_server, tmp_path):
        model = make_split_model(
            tmp_path, self.LAYERS,
            {f"{n}.safetensors": 8 for n in self.LAYERS},
        )
        airllm_server._verify_split_complete(model)  # no raise

    def test_missing_lm_head_is_fine_when_embeddings_are_tied(
        self, airllm_server, tmp_path
    ):
        shards = {f"{n}.safetensors": 8 for n in self.LAYERS if n != "lm_head"}
        model = make_split_model(tmp_path, self.LAYERS, shards)
        airllm_server._verify_split_complete(model)  # no raise

    def test_empty_split_dir_is_rejected(self, airllm_server, tmp_path):
        model = make_split_model(tmp_path, self.LAYERS, {})
        with pytest.raises(RuntimeError) as excinfo:
            airllm_server._verify_split_complete(model)
        assert "incomplete" in str(excinfo.value)
        assert "model.embed_tokens" in str(excinfo.value)

    def test_missing_middle_layer_is_rejected(self, airllm_server, tmp_path):
        shards = {f"{n}.safetensors": 8 for n in self.LAYERS if n != "model.layers.1"}
        model = make_split_model(tmp_path, self.LAYERS, shards)
        with pytest.raises(RuntimeError) as excinfo:
            airllm_server._verify_split_complete(model)
        assert "model.layers.1" in str(excinfo.value)

    def test_empty_shard_file_is_rejected(self, airllm_server, tmp_path):
        shards = {f"{n}.safetensors": 8 for n in self.LAYERS}
        shards["model.norm.safetensors"] = 0
        model = make_split_model(tmp_path, self.LAYERS, shards)
        with pytest.raises(RuntimeError):
            airllm_server._verify_split_complete(model)

    def test_no_resolved_layers_is_rejected(self, airllm_server, tmp_path):
        model = make_split_model(tmp_path, [], {})
        with pytest.raises(RuntimeError) as excinfo:
            airllm_server._verify_split_complete(model)
        assert "no streamable layers" in str(excinfo.value)
