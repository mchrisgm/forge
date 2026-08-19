# vLLM engine — fast lane (≤ 14–15B AWQ)

No build here: this lane runs the upstream image **`vllm/vllm-openai:v0.10.1`**
(see `FORGE_VLLM_IMAGE` in `orchestrator/app/config.py`). The orchestrator's
EngineManager starts/stops the container; compose never does. Weights + KV must
fit entirely in the 11 GB VRAM budget (PLAN §2) — roughly ≤ 14–15B dense
params at 4-bit AWQ with 16k context.

## Flags passed by the orchestrator

`build_vllm_command` in `orchestrator/app/services/engine_manager.py`
(the image's entrypoint is the OpenAI API server; these are its args):

```
--model <local /data/models path, or HF repo id> \
--served-model-name <model-slug> <display_name> --host 0.0.0.0 --port 8082 \
--quantization awq --max-model-len <ctx, capped at 16384> \
--gpu-memory-utilization 0.90 \
--enable-auto-tool-choice --tool-call-parser <parser>   # unless tool_call_format=none
```

Env: `VLLM_NO_USAGE_STATS=1`; `HF_TOKEN` passed through when set.

## Tool-call parser mapping

Per-model `tool_call_format` → vLLM `--tool-call-parser` (`_vllm_parser`):

| `tool_call_format` | parser |
|---|---|
| `hermes` | `hermes` |
| `qwen` | `hermes` (Qwen family uses Hermes-style tool calls) |
| `llama3` | `llama3_json` |
| `none` | tool flags omitted entirely |
| anything else | `hermes` (fallback) |

Health/readiness: `GET http://forge-engine-vllm:8082/v1/models` → 200 (model
load can take minutes; the EngineManager healthwaits and streams state).

## Tensor parallel (multi-GPU)

With more than one free GPU, `POST /api/engines/load {model_id, gpu_count: N}`
starts vLLM with `--tensor-parallel-size N` pinned to N GPUs (device_ids), so
models up to ~N × the per-GPU budget fit the fast lane. Only the vLLM lane
supports this; llama.cpp and AirLLM leases stay single-GPU (llama.cpp still
splits layers across whatever devices it is given).
