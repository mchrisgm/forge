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
from pathlib import Path

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
