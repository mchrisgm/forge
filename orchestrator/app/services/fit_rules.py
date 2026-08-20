"""Hardware fit rules (PLAN §2, §9). Pure logic — exhaustively unit-tested.

Used by both the registry scorer (lane assignment for suggestions) and the
engine manager (--n-gpu-layers computation at load time).
"""

from dataclasses import dataclass

GB = 1024**3

# Bytes of KV cache per token per layer at fp16 (k+v). Modern models use GQA
# with n_kv_heads*head_dim ≈ 1024 (e.g. 8 kv-heads × 128), so:
# 2 tensors × 2 bytes × 1024 dims = 4 KiB/token/layer. Planning estimate —
# the engine reports the truth at load time.
_KV_BYTES_PER_TOKEN_PER_LAYER = 2 * 2 * 1024


@dataclass(frozen=True)
class Budgets:
    vram_gb: float = 11.0
    ram_offload_gb: float = 32.0


def estimate_n_layers(params_b: float) -> int:
    """Rough dense-transformer layer counts by parameter size."""
    if params_b <= 0:
        return 32
    table = [
        (1.5, 24),
        (3.5, 28),
        (8.5, 32),
        (15.0, 40),
        (24.0, 48),
        (35.0, 48),
        (50.0, 60),
        (75.0, 80),
    ]
    for cap, layers in table:
        if params_b <= cap:
            return layers
    return 80


def kv_cache_gb(n_layers: int, ctx: int) -> float:
    """Estimated fp16 KV cache footprint for a full context window."""
    return n_layers * ctx * _KV_BYTES_PER_TOKEN_PER_LAYER / GB


def compute_ngl(
    file_size_gb: float,
    n_layers: int,
    ctx: int,
    vram_budget_gb: float,
    overhead_gb: float = 1.2,
) -> int:
    """Largest --n-gpu-layers whose estimated VRAM use fits the budget.

    Model layers are assumed uniform (layer ≈ file_size/n_layers). KV cache for
    GPU-resident layers plus a fixed overhead (compute buffers, scratch) must
    also fit. Binary search per PLAN §6.2.
    """
    if n_layers <= 0 or file_size_gb <= 0:
        return 0
    layer_gb = file_size_gb / n_layers
    kv_per_layer_gb = kv_cache_gb(1, ctx)

    def vram_needed(ngl: int) -> float:
        return ngl * (layer_gb + kv_per_layer_gb) + overhead_gb

    lo, hi = 0, n_layers
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if vram_needed(mid) <= vram_budget_gb:
            lo = mid
        else:
            hi = mid - 1
    return lo


def fits_llamacpp(file_size_gb: float, budgets: Budgets) -> bool:
    """GGUF must fit in VRAM-resident layers + RAM offload cap (PLAN §2)."""
    return 0 < file_size_gb <= budgets.vram_gb + budgets.ram_offload_gb


def fits_llamacpp_full_gpu(file_size_gb: float, budgets: Budgets) -> bool:
    """PLAN §9: full-GPU lane ⇔ GGUF file ≤ ~10 GB (VRAM budget minus ~1 GB
    for KV + compute buffers). compute_ngl() does the precise math at load."""
    return 0 < file_size_gb <= budgets.vram_gb - 1.0


def fits_vllm(params_b: float, ctx: int, budgets: Budgets) -> bool:
    """AWQ/GPTQ 4-bit weights + KV must fit VRAM → roughly ≤14–15B at 16k ctx."""
    if params_b <= 0:
        return False
    weights_gb = params_b * 0.55  # ~4.4 bits/param incl. scales/zeros
    kv_gb = kv_cache_gb(estimate_n_layers(params_b), ctx)
    overhead_gb = 0.6  # CUDA graphs, activation workspace
    return weights_gb + kv_gb + overhead_gb <= budgets.vram_gb


def fits_airllm(params_b: float) -> bool:
    """Anything ≤70B fp16-from-disk; chat-only lane (PLAN §2)."""
    return 0 < params_b <= 70


def assign_lane(
    params_b: float,
    gguf_size_gb: float | None,
    has_awq: bool,
    is_moe: bool,
    ctx: int = 16384,
    budgets: Budgets | None = None,
) -> str | None:
    """Best lane for a candidate model, or None if it fits nowhere.

    Preference order mirrors fit_quality scoring (PLAN §6.4):
    vLLM > llama.cpp full-GPU > llama.cpp offload > AirLLM.
    """
    budgets = budgets or Budgets()
    if has_awq and fits_vllm(params_b, ctx, budgets):
        return "vllm"
    if gguf_size_gb and fits_llamacpp(gguf_size_gb, budgets):
        if fits_llamacpp_full_gpu(gguf_size_gb, budgets):
            return "llamacpp-full-gpu"
        # Offloaded models are only pleasant when active params are small (MoE)
        # but dense offload is still allowed — the scorer ranks it lower.
        return "llamacpp-offload"
    if fits_airllm(params_b):
        return "airllm"
    return None


FIT_QUALITY: dict[str, float] = {
    "vllm": 1.0,
    "llamacpp-full-gpu": 0.85,
    "llamacpp-offload": 0.55,
    "airllm": 0.15,
}


# ── config-aware engine detection (reads the model's real config.json) ──────
#
# Name-based guessing put a speculative-decoding draft model on the AirLLM
# lane; the checkpoint's own config carries the truth. HubFacts is everything
# detect_lane needs, assembled by registry.resolve_text_candidate from
# config.json + the repo listing.


# Signals that a checkpoint is the DRAFT half of a speculative-decoding pair
# (SpecForge/DSpark/DFlash, EAGLE, Medusa): it accelerates a target model
# inside the serving engine and cannot chat on its own.
_DRAFT_TAGS = {"speculative-decoding", "specforge", "dspark", "dflash", "eagle", "medusa"}
_DRAFT_ARCH_MARKERS = ("draft", "eagle", "medusa")

# model_type families SGLang serves notably better than vLLM on this class of
# hardware (MLA attention, huge-MoE routing, hybrid layouts) — preferred when
# both could load the checkpoint.
_SGLANG_PREFERRED_TYPES = {
    "deepseek_v2", "deepseek_v3", "deepseek_vl2",
    "kimi_k2", "kimi_k3",
    "glm4_moe", "qwen3_next", "minimax_text",
}

# Standard decoder families every GPU engine (vLLM AND SGLang) implements
# natively. Conservative: an arch outside this set with custom code is only
# servable via a GGUF conversion.
_NATIVE_MODEL_TYPES = _SGLANG_PREFERRED_TYPES | {
    "llama", "llama4", "mistral", "mixtral", "qwen2", "qwen2_moe",
    "qwen3", "qwen3_moe", "gemma", "gemma2", "gemma3", "gemma3_text",
    "phi", "phi3", "phi4", "granite", "gpt_oss", "glm", "glm4",
    "internlm2", "internlm3", "exaone", "olmo2", "smollm3", "seed_oss",
}

# quantization_config quant_method values 4-bit-ish enough to use the AWQ
# weight sizing (EXL3/EXL2 average ~4 bpw); anything else quantized is sized
# like fp8 (~1.1 B/param).
_Q4_METHODS = {
    "awq", "gptq", "compressed-tensors", "quark", "bitsandbytes", "exl3", "exl2",
}


@dataclass(frozen=True)
class HubFacts:
    """What the Hub actually says about a repo (config.json + listing)."""

    params_b: float = 0.0
    model_type: str = ""
    architectures: tuple[str, ...] = ()
    custom_code: bool = False       # config.auto_map present (trust_remote_code)
    quant_method: str = ""          # config.quantization_config.quant_method
    tags: tuple[str, ...] = ()
    gguf_size_gb: float = 0.0       # best single-file GGUF found (0 = none)
    has_awq_variant: bool = False   # separate AWQ build exists (name/tag match)
    is_moe: bool = False


def _is_draft_model(facts: HubFacts) -> bool:
    if any(t in _DRAFT_TAGS for t in facts.tags):
        return True
    return any(
        marker in arch.lower()
        for arch in facts.architectures
        for marker in _DRAFT_ARCH_MARKERS
    )


def _weights_gb(facts: HubFacts) -> float:
    """Estimated on-GPU weight footprint of the checkpoint as published."""
    if facts.quant_method:
        per_param = 0.55 if facts.quant_method in _Q4_METHODS else 1.1
    else:
        per_param = 2.0  # bf16/fp16
    return facts.params_b * per_param


def _fits_gpu(facts: HubFacts, ctx: int, budgets: Budgets) -> bool:
    if facts.params_b <= 0:
        return False
    kv_gb = kv_cache_gb(estimate_n_layers(facts.params_b), ctx)
    return _weights_gb(facts) + kv_gb + 0.6 <= budgets.vram_gb


def detect_lane(
    facts: HubFacts, ctx: int = 16384, budgets: Budgets | None = None
) -> tuple[str | None, str]:
    """(lane, reason) for a checkpoint, from what its config actually says.

    Lane strings extend assign_lane's: "sglang" joins vllm/llamacpp-*/airllm.
    A None lane's reason explains honestly why nothing on this box can run it.
    """
    budgets = budgets or Budgets()

    if _is_draft_model(facts):
        return None, (
            "this is a speculative-decoding DRAFT model "
            f"({', '.join(facts.architectures) or 'per its tags'}): it "
            "accelerates a larger target model inside the serving engine and "
            "cannot chat on its own"
        )

    native = facts.model_type in _NATIVE_MODEL_TYPES
    servable_on_gpu = native or (not facts.custom_code and bool(facts.model_type))

    # EXL3/EXL2 checkpoints belong to the TabbyAPI (ExLlamaV3) lane — the
    # consumer-GPU specialist format; SGLang/vLLM cannot load them at all.
    if facts.quant_method in ("exl3", "exl2"):
        if _fits_gpu(facts, ctx, budgets):
            return "tabby", (
                f"{facts.quant_method} quantization fits in VRAM; served by "
                "TabbyAPI (ExLlamaV3)"
            )
        return None, (
            f"this {facts.quant_method} quantization is too large for the "
            "VRAM budget — pick a lower-bpw build"
        )

    # A checkpoint already quantized (or small enough in bf16) that fits VRAM
    # beats any conversion. SGLang is the default native server; vLLM keeps
    # the separate-AWQ-variant path it has always owned.
    if servable_on_gpu and _fits_gpu(facts, ctx, budgets):
        if facts.model_type in _SGLANG_PREFERRED_TYPES or facts.quant_method:
            return "sglang", (
                f"{facts.quant_method or 'bf16'} checkpoint fits in VRAM; "
                "served natively by SGLang"
            )
        return "sglang", "bf16 checkpoint fits in VRAM; served natively by SGLang"
    if facts.has_awq_variant and fits_vllm(facts.params_b, ctx, budgets):
        return "vllm", "a separate AWQ build fits in VRAM"

    if facts.gguf_size_gb and fits_llamacpp(facts.gguf_size_gb, budgets):
        if fits_llamacpp_full_gpu(facts.gguf_size_gb, budgets):
            return "llamacpp-full-gpu", "GGUF quantization fits fully in VRAM"
        return "llamacpp-offload", "GGUF quantization runs with CPU offload"

    if facts.custom_code and not native:
        return None, (
            f"custom-code architecture ({', '.join(facts.architectures) or facts.model_type}) "
            "— no engine on this box implements it natively and no GGUF "
            "conversion was found"
        )
    if fits_airllm(facts.params_b) and native:
        return "airllm", "too large for VRAM — streamed from disk (slow lane)"
    return None, (
        f"no runnable artifact fits this hardware (params {facts.params_b:.1f}B, "
        f"arch {facts.model_type or 'unknown'})"
    )


FIT_QUALITY["sglang"] = 1.0
