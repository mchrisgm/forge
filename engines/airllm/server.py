"""OpenAI-compatible AirLLM server — the Forge slow lane (PLAN §6.2).

Wraps ``airllm.AutoModel`` behind the same ``/v1`` surface the other two
engine lanes expose, so the orchestrator and the PWA chat page can use one
OpenAI client with a different base URL.

Contract (see orchestrator/app/services/engine_manager.py build_airllm_env):

- env    AIRLLM_MODEL_PATH   local snapshot dir or HF repo id  (required to generate)
         AIRLLM_MODEL_NAME   name reported by /v1/models       (default: derived)
         AIRLLM_PORT         listen port                       (default: 8083)
         AIRLLM_MAX_TOKENS   hard cap on max_tokens            (default: 512)
- GET  /health               liveness; ok even before the model is loaded
- GET  /v1/models            readiness probe polled by the orchestrator; MUST
                             answer 200 without triggering a model load
- POST /v1/chat/completions  streaming + non-streaming; no tool calling
                             (a ``tools`` field is silently ignored)

Queue depth is 1: AirLLM streams layers through VRAM, so a single generation
owns the GPU. A second concurrent request is rejected with HTTP 429.

Token-by-token streaming is impractical with AirLLM (each token re-streams
every layer from disk, and model.generate is a blocking HF-style call), so
``stream: true`` generates the full reply first, then replays it as SSE
chunks. Slow lane: expect minutes-to-hours for the first byte.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

log = logging.getLogger("airllm-server")
logging.basicConfig(level=logging.INFO)

MODEL_PATH = os.environ.get("AIRLLM_MODEL_PATH", "")
MODEL_NAME = os.environ.get("AIRLLM_MODEL_NAME", "") or (
    os.path.basename(MODEL_PATH.rstrip("/")) if MODEL_PATH else "airllm-model"
)
PORT = int(os.environ.get("AIRLLM_PORT", "8083"))
MAX_TOKENS_CAP = int(os.environ.get("AIRLLM_MAX_TOKENS", "512"))
# Must match the compression passed to AutoModel.from_pretrained below —
# AirLLM names its split dir "splitted_model.<compression>".
COMPRESSION = "4bit"
# Prompt-side truncation guard: AirLLM throughput is seconds-per-token, so an
# unbounded prompt would take hours before the first generated token.
MAX_PROMPT_TOKENS = int(os.environ.get("AIRLLM_MAX_PROMPT_TOKENS", "4096"))
STREAM_WORDS_PER_CHUNK = 4

app = FastAPI(title="forge-airllm", version="1.0")

def _repair_split_cache(shards_dir: str) -> None:
    """Drop an inconsistent AirLLM layer-split cache so it rebuilds cleanly.

    AirLLM splits the model into per-layer safetensors under
    ``<shards_dir>/splitted_model.<compression>/`` and marks each finished
    shard with a sibling ``<layer>.safetensors.done`` file. It decides the
    split is complete from the presence of the file AND its marker. On the slow
    lane a split is frequently interrupted (idle reap, OOM, unload) or a shard
    goes missing while its marker survives — AirLLM then trusts the marker and
    later dies at read time with 'No such file … model.embed_tokens.safetensors'.

    We only touch a cache that is provably inconsistent, so a healthy cache is
    preserved (re-splitting a 70B model costs hours). Inconsistent means any of:
    a ``.done`` marker whose shard is missing or empty; a shard with no marker
    (killed mid-write); or a non-empty dir with no markers at all.
    """
    split_dir = Path(shards_dir) / f"splitted_model.{COMPRESSION}"
    if not split_dir.is_dir():
        return
    entries = list(split_dir.iterdir())
    if not entries:
        return
    markers = [p for p in entries if p.name.endswith(".safetensors.done")]
    shards = [p for p in entries if p.name.endswith(".safetensors")]

    reason = ""
    if not markers:
        reason = "no completion markers (partial split)"
    for marker in markers:
        shard = marker.with_name(marker.name[: -len(".done")])
        if not shard.exists() or shard.stat().st_size == 0:
            reason = f"shard for {marker.name} is missing or empty"
            break
    if not reason:
        for shard in shards:
            if not shard.with_name(shard.name + ".done").exists():
                reason = f"{shard.name} has no completion marker"
                break
    if reason:
        log.warning(
            "clearing inconsistent AirLLM split cache %s (%s)", split_dir, reason
        )
        shutil.rmtree(split_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Model state — loaded lazily on the first completion request, never by the
# readiness probe. threading.Lock (not asyncio) because loading happens inside
# a worker thread.
# ---------------------------------------------------------------------------


class _ModelState:
    def __init__(self) -> None:
        self.model: Any = None
        self.load_error: str = ""
        self._lock = threading.Lock()

    def get(self) -> Any:
        """Return the AirLLM model, loading it on first use (blocking)."""
        with self._lock:
            if self.model is None:
                self.model = self._load()
            return self.model

    def _load(self) -> Any:
        if not MODEL_PATH:
            raise RuntimeError("AIRLLM_MODEL_PATH is not set")
        from airllm import AutoModel  # heavy import — deferred on purpose

        kwargs: dict[str, Any] = {"compression": COMPRESSION}
        hf_token = os.environ.get("HF_TOKEN", "")
        if hf_token:
            kwargs["hf_token"] = hf_token
        shards_dir = os.environ.get("AIRLLM_SHARDS_DIR", "")
        if shards_dir:
            kwargs["layer_shards_saving_path"] = shards_dir
            _repair_split_cache(shards_dir)
        log.info("loading AirLLM model %r (this can take a long time)", MODEL_PATH)
        t0 = time.monotonic()
        model = AutoModel.from_pretrained(MODEL_PATH, **kwargs)
        log.info("model loaded in %.1fs", time.monotonic() - t0)
        return model


STATE = _ModelState()

# Queue of depth 1. asyncio.Semaphore: locked() + acquire() with no await in
# between is atomic on the event loop, giving a race-free try-acquire.
GENERATION_SLOT = asyncio.Semaphore(1)


# ---------------------------------------------------------------------------
# Request/response shapes (OpenAI chat completions subset)
# ---------------------------------------------------------------------------


class ChatMessage(BaseModel):
    role: str
    content: Any = ""  # str, or OpenAI content-part list

    def text(self) -> str:
        if isinstance(self.content, str):
            return self.content
        if isinstance(self.content, list):  # [{"type": "text", "text": ...}, ...]
            return "".join(
                part.get("text", "")
                for part in self.content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        return "" if self.content is None else str(self.content)


class ChatCompletionRequest(BaseModel):
    model: str | None = None
    messages: list[ChatMessage] = Field(min_length=1)
    stream: bool = False
    max_tokens: int | None = None
    max_completion_tokens: int | None = None  # newer OpenAI alias
    # Accepted but ignored: this lane has no tool calling and no sampling knobs.
    tools: Any = None
    tool_choice: Any = None
    temperature: float | None = None
    top_p: float | None = None

    model_config = {"extra": "ignore"}

    def effective_max_tokens(self) -> int:
        requested = (
            self.max_completion_tokens
            if self.max_completion_tokens is not None
            else self.max_tokens
        )
        if requested is None:
            requested = MAX_TOKENS_CAP
        return max(1, min(requested, MAX_TOKENS_CAP))


class GenerationResult(BaseModel):
    text: str
    prompt_tokens: int
    completion_tokens: int
    finish_reason: str


# ---------------------------------------------------------------------------
# Generation — blocking, runs in a worker thread via asyncio.to_thread
# ---------------------------------------------------------------------------


def _build_prompt(tokenizer: Any, messages: list[ChatMessage]) -> str:
    """Apply the model's chat template; fall back to a plain transcript."""
    dicts = [{"role": m.role, "content": m.text()} for m in messages]
    try:
        return tokenizer.apply_chat_template(
            dicts, tokenize=False, add_generation_prompt=True
        )
    except Exception as exc:  # no template on this tokenizer — degrade gracefully
        log.warning("chat template failed (%s); using plain transcript", exc)
        lines = [f"{m['role']}: {m['content']}" for m in dicts]
        return "\n".join(lines) + "\nassistant:"


def _generate_sync(messages: list[ChatMessage], max_new_tokens: int) -> GenerationResult:
    """Tokenize -> model.generate -> decode. AirLLM's generate mirrors the HF
    GenerationMixin API (see the airllm README): it takes input_ids and
    returns .sequences when return_dict_in_generate=True."""
    model = STATE.get()
    tokenizer = model.tokenizer
    prompt = _build_prompt(tokenizer, messages)

    input_tokens = tokenizer(
        prompt,
        return_tensors="pt",
        return_attention_mask=False,
        truncation=True,
        max_length=MAX_PROMPT_TOKENS,
        padding=False,
    )
    input_ids = input_tokens["input_ids"]
    try:  # AirLLM computes on GPU when available
        import torch

        if torch.cuda.is_available():
            input_ids = input_ids.cuda()
    except Exception:
        pass
    prompt_tokens = int(input_ids.shape[-1])

    output = model.generate(
        input_ids,
        max_new_tokens=max_new_tokens,
        use_cache=True,
        return_dict_in_generate=True,
    )
    # Defensive: return_dict_in_generate gives .sequences; some paths return
    # the raw tensor.
    sequences = getattr(output, "sequences", output)
    full = sequences[0]
    new_tokens = full[prompt_tokens:] if len(full) > prompt_tokens else full[0:0]
    completion_tokens = int(len(new_tokens))
    text = tokenizer.decode(new_tokens, skip_special_tokens=True).strip()
    finish = "length" if completion_tokens >= max_new_tokens else "stop"
    return GenerationResult(
        text=text,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        finish_reason=finish,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/models")
async def list_models() -> dict[str, Any]:
    # Readiness probe (engine_manager healthwait). Must NOT touch the model.
    return {
        "object": "list",
        "data": [
            {
                "id": MODEL_NAME,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "forge-airllm",
            }
        ],
    }


def _chunk_payload(
    completion_id: str, created: int, delta: dict[str, Any], finish: str | None
) -> str:
    payload = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": MODEL_NAME,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(payload)}\n\n"


async def _sse_stream(completion_id: str, created: int, result: GenerationResult):
    yield _chunk_payload(completion_id, created, {"role": "assistant", "content": ""}, None)
    words = re.findall(r"\S+\s*", result.text)
    for i in range(0, len(words), STREAM_WORDS_PER_CHUNK):
        content = "".join(words[i : i + STREAM_WORDS_PER_CHUNK])
        yield _chunk_payload(completion_id, created, {"content": content}, None)
        await asyncio.sleep(0)  # let the chunk flush
    yield _chunk_payload(completion_id, created, {}, result.finish_reason)
    yield "data: [DONE]\n\n"


@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest):
    if request.tools:
        log.info("ignoring `tools` field: the AirLLM lane has no tool calling")

    # Try-acquire the single generation slot: no await between the check and
    # the acquire, so this is atomic on the event loop.
    if GENERATION_SLOT.locked():
        raise HTTPException(
            status_code=429,
            detail=(
                "AirLLM slow lane is busy with another generation "
                "(queue depth is 1). Retry after the current reply finishes."
            ),
        )
    await GENERATION_SLOT.acquire()
    try:
        result = await asyncio.to_thread(
            _generate_sync, request.messages, request.effective_max_tokens()
        )
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("generation failed")
        raise HTTPException(
            status_code=500, detail=f"AirLLM generation failed: {exc}"
        ) from exc
    finally:
        GENERATION_SLOT.release()

    completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())

    if request.stream:
        return StreamingResponse(
            _sse_stream(completion_id, created, result),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return JSONResponse(
        {
            "id": completion_id,
            "object": "chat.completion",
            "created": created,
            "model": MODEL_NAME,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": result.text},
                    "finish_reason": result.finish_reason,
                }
            ],
            "usage": {
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
                "total_tokens": result.prompt_tokens + result.completion_tokens,
            },
        }
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT)
