"""Diffusers text-to-image server — the Forge imagegen lane.

Exposes an OpenAI Images API subset so the orchestrator's image service can
use one OpenAI-shaped client with a different base URL, mirroring the other
engine lanes.

Contract (see orchestrator/app/services/engine_manager.py build_imagegen_env):

- env    IMAGEGEN_MODEL_PATH  local diffusers snapshot dir or HF repo id
                              (required to generate)
         IMAGEGEN_MODEL_NAME  name reported by /v1/models   (default: derived)
         IMAGEGEN_PORT        listen port                   (default: 8084)
         IMAGEGEN_STEPS       inference steps override      (default: heuristic)
         IMAGEGEN_GUIDANCE    guidance scale override       (default: heuristic)
- GET  /health                liveness; ok even before the pipeline is loaded
- GET  /v1/models             readiness probe polled by the orchestrator; MUST
                              answer 200 without triggering a pipeline load
- POST /v1/images/generations OpenAI Images subset: prompt (required),
                              n (1..4), size ("WxH", dims clamped to 256..1536
                              and rounded to a multiple of 8), response_format
                              (only b64_json is produced; "url" is accepted
                              but still answered with b64_json entries)

The step/guidance defaults use a turbo heuristic: if the model path or name
mentions "turbo", "lcm", or "lightning", few-step distilled defaults apply
(4 steps, guidance 0.0); otherwise 25 steps at guidance 7.0.

Queue depth is 1: a diffusion run owns the GPU, so a second concurrent
request is rejected with HTTP 429.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import re
import threading
import time
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

log = logging.getLogger("imagegen-server")
logging.basicConfig(level=logging.INFO)

MODEL_PATH = os.environ.get("IMAGEGEN_MODEL_PATH", "")
MODEL_NAME = os.environ.get("IMAGEGEN_MODEL_NAME", "") or (
    os.path.basename(MODEL_PATH.rstrip("/")) if MODEL_PATH else "imagegen-model"
)
PORT = int(os.environ.get("IMAGEGEN_PORT", "8084"))

# Few-step distilled checkpoints (SDXL-Turbo, LCM, Lightning) want almost no
# steps and no classifier-free guidance; everything else gets sane SD defaults.
_IS_DISTILLED = any(
    key in f"{MODEL_PATH} {MODEL_NAME}".lower() for key in ("turbo", "lcm", "lightning")
)
DEFAULT_STEPS = int(os.environ.get("IMAGEGEN_STEPS", "") or (4 if _IS_DISTILLED else 25))
DEFAULT_GUIDANCE = float(
    os.environ.get("IMAGEGEN_GUIDANCE", "") or (0.0 if _IS_DISTILLED else 7.0)
)

MAX_IMAGES = 4
MIN_DIM = 256
MAX_DIM = 1536
DIM_MULTIPLE = 8
_SIZE_RE = re.compile(r"(\d+)\s*[xX]\s*(\d+)")

app = FastAPI(title="forge-imagegen", version="1.0")

# ---------------------------------------------------------------------------
# Pipeline state — loaded lazily on the first generation request, never by the
# readiness probe. threading.Lock (not asyncio) because loading happens inside
# a worker thread.
# ---------------------------------------------------------------------------


class _PipelineState:
    def __init__(self) -> None:
        self.pipe: Any = None
        self._lock = threading.Lock()

    def get(self) -> Any:
        """Return the diffusers pipeline, loading it on first use (blocking)."""
        with self._lock:
            if self.pipe is None:
                self.pipe = self._load()
            return self.pipe

    def _load(self) -> Any:
        if not MODEL_PATH:
            raise RuntimeError("IMAGEGEN_MODEL_PATH is not set")
        import torch  # heavy import — deferred on purpose
        from diffusers import AutoPipelineForText2Image

        log.info("loading diffusers pipeline %r (this can take a while)", MODEL_PATH)
        t0 = time.monotonic()
        try:
            # Forge's downloader keeps only .fp16 component files when a repo
            # ships both variants, so try the fp16 variant first...
            pipe = AutoPipelineForText2Image.from_pretrained(
                MODEL_PATH, torch_dtype=torch.float16, variant="fp16"
            )
        except Exception as exc:  # noqa: BLE001 — any load error retries plain
            # ...but plenty of repos have no .fp16 files at all.
            log.info("fp16 variant load failed (%s); retrying without variant", exc)
            pipe = AutoPipelineForText2Image.from_pretrained(
                MODEL_PATH, torch_dtype=torch.float16
            )
        if torch.cuda.is_available():
            pipe = pipe.to("cuda")
        else:
            log.warning("CUDA not available — generating on CPU will be very slow")
        try:  # cheap VRAM saving; not every pipeline class has it
            pipe.enable_vae_slicing()
        except (AttributeError, NotImplementedError) as exc:
            log.debug("vae slicing unavailable on this pipeline: %s", exc)
        log.info("pipeline loaded in %.1fs", time.monotonic() - t0)
        return pipe


STATE = _PipelineState()

# Queue of depth 1. asyncio.Semaphore: locked() + acquire() with no await in
# between is atomic on the event loop, giving a race-free try-acquire.
GENERATION_SLOT = asyncio.Semaphore(1)


# ---------------------------------------------------------------------------
# Request shape (OpenAI images generations subset)
# ---------------------------------------------------------------------------


class ImageGenerationRequest(BaseModel):
    prompt: str = ""
    n: int = 1
    size: str = "1024x1024"
    response_format: str = "b64_json"
    # Accepted but ignored: this lane serves exactly one model.
    model: str | None = None

    model_config = {"extra": "ignore"}


def _parse_size(size: str) -> tuple[int, int]:
    """Parse "WxH", clamping each dim to MIN..MAX and rounding to a multiple
    of 8 (diffusion UNet/VAE latents need dims divisible by 8)."""
    match = _SIZE_RE.fullmatch(size.strip())
    if match is None:
        raise HTTPException(
            status_code=400,
            detail=f"unparseable size {size!r}: expected \"WIDTHxHEIGHT\", e.g. 1024x1024",
        )

    def _snap(value: int) -> int:
        return max(MIN_DIM, min(MAX_DIM, value)) // DIM_MULTIPLE * DIM_MULTIPLE

    return _snap(int(match.group(1))), _snap(int(match.group(2)))


# ---------------------------------------------------------------------------
# Generation — blocking, runs in a worker thread via asyncio.to_thread
# ---------------------------------------------------------------------------


def _generate_sync(prompt: str, n: int, width: int, height: int) -> list[bytes]:
    pipe = STATE.get()
    log.info(
        "generating %d image(s) %dx%d, steps=%d guidance=%.1f",
        n, width, height, DEFAULT_STEPS, DEFAULT_GUIDANCE,
    )
    t0 = time.monotonic()
    images = pipe(
        prompt,
        num_images_per_prompt=n,
        width=width,
        height=height,
        num_inference_steps=DEFAULT_STEPS,
        guidance_scale=DEFAULT_GUIDANCE,
    ).images
    log.info("generated %d image(s) in %.1fs", len(images), time.monotonic() - t0)
    pngs: list[bytes] = []
    for image in images:
        buffer = io.BytesIO()
        image.save(buffer, "PNG")
        pngs.append(buffer.getvalue())
    return pngs


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/models")
async def list_models() -> dict[str, Any]:
    # Readiness probe (engine_manager healthwait). Must NOT touch the pipeline.
    return {
        "object": "list",
        "data": [
            {
                "id": MODEL_NAME,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "forge-imagegen",
            }
        ],
    }


# The doubled-prefix alias exists because the orchestrator's image_service
# joins lease.base_url (which already ends in /v1) with /v1/images/generations,
# so its requests arrive at /v1/v1/images/generations. Serve both spellings.
@app.post("/v1/images/generations")
@app.post("/v1/v1/images/generations", include_in_schema=False)
async def create_images(request: ImageGenerationRequest) -> dict[str, Any]:
    if not request.prompt.strip():
        raise HTTPException(status_code=400, detail="prompt is required")
    if request.response_format not in ("b64_json", "url"):
        raise HTTPException(
            status_code=400,
            detail=f"unsupported response_format {request.response_format!r}: "
            "only b64_json is supported (url requests are answered with b64_json)",
        )
    if request.response_format == "url":
        log.info("response_format=url requested; answering with b64_json entries")
    n = max(1, min(request.n, MAX_IMAGES))
    width, height = _parse_size(request.size)

    # Try-acquire the single generation slot: no await between the check and
    # the acquire, so this is atomic on the event loop.
    if GENERATION_SLOT.locked():
        raise HTTPException(
            status_code=429,
            detail=(
                "imagegen lane is busy with another generation (queue depth "
                "is 1). Retry after the current image finishes."
            ),
        )
    await GENERATION_SLOT.acquire()
    try:
        pngs = await asyncio.to_thread(_generate_sync, request.prompt, n, width, height)
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("generation failed")
        raise HTTPException(
            status_code=500, detail=f"image generation failed: {exc}"
        ) from exc
    finally:
        GENERATION_SLOT.release()

    return {
        "created": int(time.time()),
        "data": [
            {"b64_json": base64.b64encode(png).decode("ascii")} for png in pngs
        ],
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT)
