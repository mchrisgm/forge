#!/usr/bin/env python3
"""Seed the Forge model catalog (PLAN §10).

Run inside the orchestrator container (where /app is on disk and the DB
volume is mounted):

    docker compose exec orchestrator python /app/scripts/seed_models.py
    # or: make seed

Inserts a curated, hardware-verified starter catalog for the target box
(RTX 4070 Ti Super 12 GB / 48 GB RAM). Every hf_repo, filename, size,
layer count, and context length below was verified against the Hugging
Face Hub on 2026-08-19.

Entries are created with status=approved — weights are NOT downloaded
here. Kick off downloads from the Models page in the UI or via
POST /api/models/{id}/download, then load with POST /api/engines/load.

Idempotent: re-running skips entries that already exist (matched by
hf_repo + engine + GGUF filename, so it stays a no-op after the
downloader rewrites file_path to gguf/<repo-slug>/<file>).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Runnable as `python /app/scripts/seed_models.py`: put /app (the parent of
# scripts/, which also contains the `app` package) on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import select  # noqa: E402

from app.db import init_db, write_session  # noqa: E402
from app.models import (  # noqa: E402
    EngineKind,
    ModelEntry,
    ModelStatus,
    Quant,
    ToolCallFormat,
)

# ── Seed catalog (verified on Hugging Face, 2026-08-19) ─────────────────────
#
# file_path rules (must match routers/models_api.py manual add):
#   llamacpp     -> JUST the .gguf filename; downloader.py stores the file at
#                   gguf/<repo-slug>/<filename> and rewrites file_path itself.
#   vllm/airllm  -> "" (whole-repo snapshot; downloader rewrites to hf/<slug>).
#
# size_gb is the real artifact size in GiB (downloader recomputes from disk
# after download; seeding the true value makes the fit rules and VRAM gauge
# meaningful before any download happens).

SEED_MODELS: list[ModelEntry] = [
    # (a) vLLM fast lane — official Qwen AWQ build, 14.8B dense, 48 layers.
    #     Weights ≈ 9.3 GiB fit the 11 GB budget at 16k ctx (PLAN §9).
    ModelEntry(
        hf_repo="Qwen/Qwen2.5-Coder-14B-Instruct-AWQ",
        display_name="Qwen2.5 Coder 14B Instruct (AWQ)",
        family="qwen2.5-coder",
        params_b=14.77,
        quant=Quant.awq,
        file_path="",  # snapshot download (vllm lane)
        size_gb=9.3,
        engine=EngineKind.vllm,
        ctx_max=32768,
        n_layers=48,
        is_moe=False,
        tool_call_format=ToolCallFormat.hermes,
        status=ModelStatus.approved,
        note=(
            "Official Qwen AWQ build. Tool calling is reliable via vLLM "
            "--tool-call-parser hermes; verified Qwen2.5-Coder works with "
            "OpenCode function calls."
        ),
    ),
    # (b) llama.cpp full-GPU lane — same 14B as a single-file Q4_K_M GGUF
    #     (8.37 GiB ≤ ~10 GiB file → all layers VRAM-resident). bartowski's
    #     repo is used because the official Qwen GGUF repo ships Q4_K_M as
    #     split files, which the single-file downloader doesn't handle.
    ModelEntry(
        hf_repo="bartowski/Qwen2.5-Coder-14B-Instruct-GGUF",
        display_name="Qwen2.5 Coder 14B Instruct (Q4_K_M)",
        family="qwen2.5-coder",
        params_b=14.77,
        quant=Quant.gguf_q4_k_m,
        file_path="Qwen2.5-Coder-14B-Instruct-Q4_K_M.gguf",
        size_gb=8.37,
        engine=EngineKind.llamacpp,
        ctx_max=32768,
        n_layers=48,
        is_moe=False,
        tool_call_format=ToolCallFormat.hermes,
        status=ModelStatus.approved,
        note=(
            "Fits fully in VRAM. Hermes-style tool calls work through "
            "llama-server --jinja; solid mid-size coding model."
        ),
    ),
    # (c) llama.cpp offload lane — Qwen3-Coder 30B MoE (30.5B total / 3.3B
    #     active, 128 experts, 8 per token). 17.28 GiB Q4_K_M splits across
    #     11 GB VRAM + RAM; small active params keep it fast. The expected
    #     daily driver on this hardware.
    ModelEntry(
        hf_repo="unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF",
        display_name="Qwen3 Coder 30B-A3B Instruct (Q4_K_M)",
        family="qwen3-coder",
        params_b=30.5,
        quant=Quant.gguf_q4_k_m,
        file_path="Qwen3-Coder-30B-A3B-Instruct-Q4_K_M.gguf",
        size_gb=17.28,
        engine=EngineKind.llamacpp,
        ctx_max=262144,
        n_layers=48,
        is_moe=True,
        tool_call_format=ToolCallFormat.hermes,
        status=ModelStatus.approved,
        note=(
            "Expected daily driver: MoE with 3.3B active params, native 256k "
            "context, purpose-built for agentic coding. Tool calling works "
            "through llama-server --jinja (Qwen3-Coder XML-ish tool format is "
            "handled by the bundled chat template); occasional malformed "
            "call under long contexts — retry usually succeeds."
        ),
    ),
    # (d) llama.cpp offload lane — OpenAI gpt-oss-20b (20.9B total / 3.6B
    #     active MoE, 24 layers). ggml-org's official conversion keeps the
    #     native MXFP4 quantization (~11.3 GiB). quant is recorded as the
    #     closest catalog bucket; see note.
    ModelEntry(
        hf_repo="ggml-org/gpt-oss-20b-GGUF",
        display_name="gpt-oss-20b (MXFP4)",
        family="gpt-oss",
        params_b=20.9,
        quant=Quant.gguf_q4_k_m,  # catalog bucket; actual file is MXFP4
        file_path="gpt-oss-20b-MXFP4.gguf",
        size_gb=11.28,
        engine=EngineKind.llamacpp,
        ctx_max=131072,
        n_layers=24,
        is_moe=True,
        tool_call_format=ToolCallFormat.none,
        status=ModelStatus.approved,
        note=(
            "Native MXFP4 GGUF (not Q4_K_M) from ggml-org's official "
            "conversion. Uses the Harmony chat format; llama.cpp --jinja can "
            "emit tool calls via the chat template, but reliability through "
            "the OpenAI-compat surface was inconsistent in testing, so "
            "tool_call_format is 'none' — prefer it for chat/reasoning, not "
            "agentic sessions."
        ),
    ),
    # (e) imagegen lane — SDXL-Turbo, 1-4 step distilled SDXL for near-realtime
    #     text-to-image. The downloader prunes the snapshot to the fp16
    #     component set (~7 GiB); the fp16 UNet + VAE fit the 12 GB card with
    #     room to spare.
    ModelEntry(
        hf_repo="stabilityai/sdxl-turbo",
        display_name="SDXL Turbo",
        family="sdxl",
        params_b=3.5,
        quant=Quant.fp16_diffusers,
        file_path="",  # snapshot download (imagegen lane)
        size_gb=6.9,
        engine=EngineKind.imagegen,
        ctx_max=0,
        n_layers=0,
        is_moe=False,
        tool_call_format=ToolCallFormat.none,
        status=ModelStatus.approved,
        note=(
            "Distilled SDXL for 1-4 step generation — seconds per image on "
            "this hardware. Powers chat image generation without any external "
            "connector. Non-commercial license (stability.ai membership "
            "needed for commercial use)."
        ),
    ),
    # (f) small utility lane — 7B coder for quick tasks (4.36 GiB, fully
    #     VRAM-resident, fast even with parallel slots).
    ModelEntry(
        hf_repo="bartowski/Qwen2.5-Coder-7B-Instruct-GGUF",
        display_name="Qwen2.5 Coder 7B Instruct (Q4_K_M)",
        family="qwen2.5-coder",
        params_b=7.62,
        quant=Quant.gguf_q4_k_m,
        file_path="Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf",
        size_gb=4.36,
        engine=EngineKind.llamacpp,
        ctx_max=32768,
        n_layers=28,
        is_moe=False,
        tool_call_format=ToolCallFormat.hermes,
        status=ModelStatus.approved,
        note=(
            "Utility model for quick edits and smoke tests. Tool calling "
            "works but is noticeably less reliable than the 14B/30B options "
            "on multi-step agentic tasks."
        ),
    ),
]


def _is_same_artifact(existing: ModelEntry, seed: ModelEntry) -> bool:
    """True when `existing` already represents `seed`'s artifact.

    Compared by hf_repo + engine + GGUF basename. Basename comparison keeps
    this idempotent after the downloader rewrites file_path to
    gguf/<repo-slug>/<filename>. For snapshot lanes (vllm/airllm) the repo
    itself is the artifact.
    """
    if existing.hf_repo != seed.hf_repo or existing.engine != seed.engine:
        return False
    if seed.engine == EngineKind.llamacpp:
        return Path(existing.file_path).name == Path(seed.file_path).name
    return True


def seed() -> tuple[int, int]:
    inserted = 0
    skipped = 0
    with write_session() as db:
        existing = db.exec(select(ModelEntry)).all()
        for template in SEED_MODELS:
            if any(_is_same_artifact(row, template) for row in existing):
                skipped += 1
                print(
                    f"  skip   {template.hf_repo} ({template.display_name}) — already in catalog"
                )
                continue
            # Fresh instance per call: adding the module-level template itself
            # would leave it expired+detached after commit, breaking any later
            # seed() in the same process.
            entry = ModelEntry(**template.model_dump(exclude={"id"}))
            db.add(entry)
            inserted += 1
            print(
                f"  insert {entry.hf_repo} [{entry.engine.value}] -> {entry.display_name}"
            )
    return inserted, skipped


def main() -> int:
    init_db()  # idempotent create_all — safe even if the orchestrator booted first
    print("Seeding Forge model catalog (PLAN §10)...")
    inserted, skipped = seed()
    print(f"Done: {inserted} inserted, {skipped} already present.")
    if inserted:
        print(
            "Next: open the Models page (or POST /api/models/{id}/download) "
            "to fetch weights, then load an engine."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
