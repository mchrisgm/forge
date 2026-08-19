"""Hardware fit rules (PLAN §2, §9). Pure logic — exhaustively unit-tested.

Used by both the registry scorer (lane assignment for suggestions) and the
engine manager (--n-gpu-layers computation at load time).
"""

from dataclasses import dataclass

GB = 1024**3

# Bytes of KV cache per token per layer at fp16 (k+v), for a typical
# ~5120-dim 40-head model; scaled by a per-size factor below. This is a
# planning estimate, not an exact figure — llama.cpp will tell us the truth.
_KV_BYTES_PER_TOKEN_PER_LAYER = 2 * 2 * 4096  # 2 tensors * fp16 * head_dim*n_kv_heads≈4096


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


def fits_llamacpp_full_gpu(
    file_size_gb: float, n_layers: int, ctx: int, budgets: Budgets
) -> bool:
    return (
        fits_llamacpp(file_size_gb, budgets)
        and compute_ngl(file_size_gb, n_layers, ctx, budgets.vram_gb) >= n_layers
    )


def fits_vllm(params_b: float, ctx: int, budgets: Budgets) -> bool:
    """AWQ/GPTQ 4-bit weights + KV must fit VRAM → roughly ≤14–15B at 16k ctx."""
    if params_b <= 0:
        return False
    weights_gb = params_b * 0.55  # ~4.4 bits/param incl. scales/zeros
    kv_gb = kv_cache_gb(estimate_n_layers(params_b), ctx)
    overhead_gb = 1.5  # CUDA graphs, activation workspace
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
        n_layers = estimate_n_layers(params_b)
        if fits_llamacpp_full_gpu(gguf_size_gb, n_layers, ctx, budgets):
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
