"""Auto model routing: a tiny resident LLM sizes the task, then picks a model.

When a conversation's model is set to "auto", a small always-on router model
(chosen on the Settings page — TinyLlama/Qwen-0.6B class, must be a READY
llama.cpp model) reads the user's prompt and decides how HEAVY the task is:

  * light — reading or summarizing text, browsing/looking things up, reading
    news, opening or reading files, translation, casual chat, short factual
    questions → answered by the SMALLEST downloaded model (fast).
  * heavy — writing or debugging code, step-by-step logical reasoning, math,
    planning or complex analysis → answered by the LARGEST downloaded model.

The pick is made across ALL downloaded models by capability (parameter count),
never by what happens to be loaded already — the orchestrator then makes sure
the chosen model is serving (loading it onto a GPU if needed) before
generation starts. The whole dance streams as forge:"status" frames, so the
chat narrates "choosing → routed → loading → generating" instead of sitting
silent.

Placement (the user's spec): with multiple GPUs the router lives on the
"worst" one (smallest VRAM); with a single GPU it runs alongside the big
model with NO GPU layers (--n-gpu-layers 0) — a 1B Q4 model classifies a
prompt on CPU in well under a second, and stealing VRAM from the main lane
would be a worse trade. The router container sits OUTSIDE the per-GPU lease
map (label forge.router, fixed name/port), so it never blocks a lane.

Every failure degrades softly: router not configured, container won't start,
or the reply unusable → classify the prompt with a local keyword heuristic
instead and pick by size the same way, with an honest reason in the status
frame.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

import httpx
from sqlmodel import select

from ..config import get_settings
from ..db import get_setting, read_session
from ..models import EngineKind, ModelEntry, ModelStatus
from . import docker_util
from .engine_manager import (
    Lease,
    LeaseHeldError,
    engine_manager,
    gpu_run_kwargs,
    opencode_model_id_for,
)

log = logging.getLogger(__name__)

ROUTER_LABEL = "forge.router"
ROUTER_CONTAINER = "forge-engine-router"
AUTO_SLUG = "auto"  # the conversation.model_slug sentinel

_HEALTH_TIMEOUT_S = 180.0  # tiny models load in seconds; leave slack for pulls
_CHOOSE_TIMEOUT_S = 20.0

# Module state: the router's lease-shaped handle (never in the lease map).
_router: dict[str, Any] = {"lease": None, "model_id": None}
_router_lock = asyncio.Lock()


def router_model_slug() -> str:
    """The configured router model slug ('' = auto routing disabled)."""
    return (get_setting("router_model_slug") or "").strip()


def router_model_entry() -> ModelEntry | None:
    """The configured router model — must be READY and on the llama.cpp lane
    (tiny GGUF is the only sane always-resident shape)."""
    slug = router_model_slug()
    if not slug:
        return None
    with read_session() as db:
        rows = db.exec(
            select(ModelEntry).where(
                ModelEntry.status == ModelStatus.ready,
                ModelEntry.engine == EngineKind.llamacpp,
            )
        ).all()
    for row in rows:
        if opencode_model_id_for(row) == slug:
            return row
    return None


def worst_gpu(gpus: list[dict]) -> int:
    """The GPU the router should live on when several exist: smallest VRAM;
    ties break to the HIGHEST index (keep GPU 0, the usual primary, clean)."""
    if not gpus:
        return 0
    return min(gpus, key=lambda g: (g.get("vram_total_gb", 0), -g.get("index", 0)))[
        "index"
    ]


def _gpu_stats() -> list[dict]:
    try:
        import pynvml

        pynvml.nvmlInit()
        try:
            out = []
            for index in range(pynvml.nvmlDeviceGetCount()):
                handle = pynvml.nvmlDeviceGetHandleByIndex(index)
                mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
                out.append(
                    {"index": index, "vram_total_gb": round(mem.total / 1024**3, 2)}
                )
            return out
        finally:
            pynvml.nvmlShutdown()
    except Exception:
        return []


def _router_command(model: ModelEntry, settings, gpu_layers: int) -> list[str]:
    ctx = 4096  # classification prompts are short; keep the footprint tiny
    return [
        "-m", f"/data/models/{model.file_path}",
        "--host", "0.0.0.0",
        "--port", str(settings.router_port),
        "-c", str(ctx),
        "--n-gpu-layers", str(gpu_layers),
        "--parallel", "1",
        "--jinja",
        "--alias", opencode_model_id_for(model),
    ]


def _router_base_url(settings) -> str:
    return f"http://{ROUTER_CONTAINER}:{settings.router_port}/v1"


def _spawn_router_blocking(model: ModelEntry) -> Any:
    import docker

    settings = get_settings()
    client = docker_util.client()
    # Replace any previous router container (old model, old image).
    try:
        old = client.containers.get(ROUTER_CONTAINER)
        docker_util.remove_container(old)
    except docker.errors.NotFound:
        pass

    vendor = engine_manager.gpu_vendor
    image = settings.llamacpp_rocm_image if vendor == "amd" else settings.llamacpp_image
    gpu_count = engine_manager.gpu_count
    gpu_kwargs: dict[str, Any] = {}
    extra_env: dict[str, str] = {}
    if gpu_count > 1:
        stats = _gpu_stats()
        gpu = worst_gpu(stats) if stats else gpu_count - 1
        gpu_layers = 999  # tiny model — fully offload onto the worst GPU
        gpu_kwargs, extra_env = gpu_run_kwargs(vendor, [gpu])
        placement = f"gpu {gpu} (smallest VRAM)"
    else:
        # Single GPU: run beside the big model WITHOUT touching its VRAM.
        gpu_layers = 0
        placement = "cpu (sharing the box with the main lane)"
        if vendor == "amd":
            # The ROCm binary probes /dev/kfd on startup, so give it the devices
            # even though --n-gpu-layers 0 keeps the model off the GPU's VRAM.
            gpu_kwargs, extra_env = gpu_run_kwargs(vendor, [0])
    log.info("starting router model %s on %s", model.display_name, placement)

    return client.containers.run(
        image,
        command=_router_command(model, settings, gpu_layers),
        name=ROUTER_CONTAINER,
        labels={ROUTER_LABEL: "1", "forge.model_id": str(model.id or 0)},
        network=settings.docker_network,
        mounts=[
            docker.types.Mount(
                target="/data/models", source=settings.models_volume, type="volume"
            )
        ],
        environment=extra_env or None,
        detach=True,
        restart_policy={"Name": "no"},
        **gpu_kwargs,
    )


async def ensure_router() -> Lease | None:
    """The router model's lease-shaped handle, starting its container on
    first use (and restarting it when the configured model changed or the
    container died). None when unconfigured or the start failed — callers
    fall back to deterministic selection."""
    model = router_model_entry()
    if model is None:
        return None
    settings = get_settings()
    async with _router_lock:
        lease: Lease | None = _router["lease"]
        if lease is not None and _router["model_id"] == model.id:
            try:
                async with httpx.AsyncClient(timeout=3) as http:
                    resp = await http.get(f"{lease.base_url}/models")
                    if resp.status_code == 200:
                        return lease
            except httpx.HTTPError:
                pass  # died — respawn below
        try:
            await asyncio.to_thread(_spawn_router_blocking, model)
        except Exception as exc:
            log.warning("router container failed to start: %s", exc)
            _router["lease"] = None
            return None
        lease = Lease(
            model_id=model.id or 0,
            model_name=model.display_name,
            model_slug=opencode_model_id_for(model),
            engine=EngineKind.llamacpp,
            gpu_ids=[],
            state="ready",
            base_url=_router_base_url(settings),
        )
        deadline = time.monotonic() + _HEALTH_TIMEOUT_S
        async with httpx.AsyncClient(timeout=3) as http:
            while time.monotonic() < deadline:
                try:
                    resp = await http.get(f"{lease.base_url}/models")
                    if resp.status_code == 200:
                        _router["lease"] = lease
                        _router["model_id"] = model.id
                        return lease
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(2)
    log.warning("router model never became healthy")
    _router["lease"] = None
    return None


def ready_candidates() -> list[ModelEntry]:
    """Every downloaded text model auto mode can route to."""
    with read_session() as db:
        rows = db.exec(
            select(ModelEntry).where(ModelEntry.status == ModelStatus.ready)
        ).all()
    return [r for r in rows if r.engine != EngineKind.imagegen]


def model_for_slug(slug: str) -> ModelEntry | None:
    """The downloaded model whose OpenCode id matches `slug` (the id the chat
    picker sends when a specific model is chosen). None when nothing matches —
    e.g. the model was deleted after the conversation pinned it."""
    slug = (slug or "").strip()
    if not slug:
        return None
    for c in ready_candidates():
        if opencode_model_id_for(c) == slug:
            return c
    return None


# ── task sizing ─────────────────────────────────────────────────────────────
# The router classifies each prompt into one of these buckets; the bucket maps
# to a model size, so selection stays capability-based (never "what's loaded").
TASK_LIGHT = "light"
TASK_HEAVY = "heavy"

# Local heuristic used whenever the tiny model can't be reached or answers
# unusably. It biases toward LIGHT and only fires heavy on strong, unambiguous
# signals — bare nouns like "function"/"class"/"reason" are deliberately NOT
# matched on their own, since "what is the function of the pancreas" is a light
# factual lookup, not a coding task. Heavy needs a code fence, code syntax, a
# work-verb paired with a code noun, or a clear math/logic cue.
_HEAVY_SIGNALS = re.compile(
    r"```"                                          # a fenced code block
    r"|\bdef\s+\w+\s*\("                            # a python function def
    r"|\bclass\s+\w+\s*[:(]"                        # a class definition
    r"|\b(?:write|fix|debug|implement|refactor|optimi[sz]e|generate|build)\b"
    r"[^?]{0,60}?\b(?:code|function|script|program|bug|method|api|endpoint"
    r"|regex|query|algorithm|compiler?)\b"          # work-verb + code noun
    r"|\b(?:debug|refactor|traceback|stack ?trace|recursion|"
    r"typescript|javascript|golang|dockerfile|kubernetes)\b"
    r"|\bstep[- ]by[- ]step\b"                      # explicit reasoning ask
    r"|\b(?:prove|proof|theorem|integral|derivative|equation)\b",
    re.IGNORECASE,
)

_CLASSIFY_SYSTEM = (
    "You triage a user's request to the right SIZE of local model. Reply with "
    "exactly one word — either 'light' or 'heavy'.\n"
    "light = reading or summarizing text, browsing or looking up information, "
    "reading the news, opening or reading files, translation, casual "
    "conversation, or short factual questions.\n"
    "heavy = writing or debugging code, step-by-step logical reasoning, math, "
    "planning, or complex analysis.\n"
    "Answer with only the single word 'light' or 'heavy'."
)


def _keyword_task_class(prompt: str) -> str:
    """Router-free task sizing: the fallback when the tiny model is down."""
    return TASK_HEAVY if _HEAVY_SIGNALS.search(prompt or "") else TASK_LIGHT


def _pick_for_class(candidates: list[ModelEntry], task_class: str) -> ModelEntry:
    """The model a task class maps to, chosen across ALL downloaded models by
    capability: light → smallest (fastest), heavy → largest (most capable).
    Deterministic ties: params first, then id.

    Models whose size is UNKNOWN (params_b <= 0 — a GGUF repo whose parameter
    count couldn't be inferred) are set aside so they never masquerade as the
    smallest (and hijack every light route) or block the largest pick. They are
    used only when nothing has a known size."""
    known = [c for c in candidates if (c.params_b or 0) > 0]
    pool = known or candidates
    ordered = sorted(pool, key=lambda c: (c.params_b, c.id or 0))
    return ordered[0] if task_class == TASK_LIGHT else ordered[-1]


async def _classify(prompt: str, lease: Lease) -> str | None:
    """Ask the tiny router model to size the task. None on any failure or an
    answer that is neither 'light' nor 'heavy'."""
    body = {
        "model": lease.model_slug,
        "messages": [
            {"role": "system", "content": _CLASSIFY_SYSTEM},
            {"role": "user", "content": prompt[:2000]},
        ],
        "max_tokens": 4,
        "temperature": 0,
    }
    try:
        async with httpx.AsyncClient(timeout=_CHOOSE_TIMEOUT_S) as http:
            resp = await http.post(f"{lease.base_url}/chat/completions", json=body)
            resp.raise_for_status()
            reply = str(resp.json()["choices"][0]["message"]["content"]).lower()
    except Exception as exc:
        log.warning("router classify failed: %s", exc)
        return None
    # Check heavy first: "this is not light, it's heavy" must resolve to heavy.
    if TASK_HEAVY in reply:
        return TASK_HEAVY
    if TASK_LIGHT in reply:
        return TASK_LIGHT
    return None


async def choose_model(prompt: str) -> tuple[ModelEntry, str]:
    """(model, reason) for this prompt. The tiny router model sizes the task
    (light vs heavy) and we pick the smallest/largest downloaded model to
    match — capability-based, independent of what's currently loaded. Every
    failure path sizes the task with a local keyword heuristic instead, so
    context-aware routing still works with the router container down."""
    candidates = ready_candidates()
    if not candidates:
        raise RuntimeError(
            "no downloaded model is ready — download one from the Models page"
        )
    # The configured router model is a CLASSIFIER, not an answer model — a
    # 0.6-1B model shouldn't answer the user just because it's the smallest.
    # Exclude it from the answer pool unless it's the only thing downloaded.
    router = router_model_entry()
    pool = [c for c in candidates if not (router and c.id == router.id)] or candidates
    if len(pool) == 1:
        return pool[0], "the only ready model"

    lease = await ensure_router()
    if lease is None:
        task_class = _keyword_task_class(prompt)
        pick = _pick_for_class(pool, task_class)
        return pick, f"router model unavailable — {task_class} task by keywords"

    task_class = await _classify(prompt, lease)
    if task_class is None:
        task_class = _keyword_task_class(prompt)
        pick = _pick_for_class(pool, task_class)
        return pick, f"router reply unclear — {task_class} task by keywords"

    pick = _pick_for_class(pool, task_class)
    return pick, f"{task_class} task — routed by {lease.model_name}"


async def ensure_serving(
    model: ModelEntry, push_status, allow_fallback: bool = True
) -> Lease:
    """A READY lease for `model`, loading it onto a GPU when necessary. When
    every GPU is held by OTHER models, `allow_fallback` (Auto's flexible pick)
    answers with whatever is already serving rather than evicting someone's
    active model; an EXPLICIT pick passes ``allow_fallback=False`` so it raises
    instead of silently answering with a different model than the user chose."""
    slug = opencode_model_id_for(model)
    for lease in engine_manager.ready_text_leases():
        if lease.model_slug == slug:
            return lease

    settings = get_settings()
    try:
        lease = await engine_manager.load(model)
    except LeaseHeldError:
        ready = engine_manager.ready_text_leases()
        if allow_fallback and ready:
            push_status(
                f"every GPU is busy — answering with {ready[0].model_name} instead"
            )
            return ready[0]
        raise RuntimeError(
            f"{model.display_name} can't load — every GPU is busy. Try again "
            "shortly, or pick a model that's already loaded."
        ) from None

    push_status(
        f"loading {model.display_name} onto the GPU — this can take a while"
    )
    deadline = time.monotonic() + settings.engine_load_timeout_s
    while time.monotonic() < deadline:
        if lease.state == "ready":
            return lease
        if lease.state == "failed":
            raise RuntimeError(f"loading {model.display_name} failed: {lease.error}")
        await asyncio.sleep(2)
    raise RuntimeError(f"loading {model.display_name} timed out")
