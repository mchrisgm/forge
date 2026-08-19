# AirLLM engine — slow lane

OpenAI-compatible server over [airllm](https://pypi.org/project/airllm/) for
models too big for VRAM (≤ 70B fp16-from-disk, PLAN §2/§9). AirLLM streams
layers through the GPU per token, so throughput is **seconds-per-token —
minutes to hours per reply**. Chat-page only; hidden from the session model
picker.

## Build / run

Built by compose as `forge-airllm`; the orchestrator's EngineManager starts it
with env from `build_airllm_env`:

| Env | Meaning | Default |
|---|---|---|
| `AIRLLM_MODEL_PATH` | local snapshot dir or HF repo id | (required) |
| `AIRLLM_MODEL_NAME` | id reported by `/v1/models` | derived from path |
| `AIRLLM_PORT` | listen port | `8083` |
| `AIRLLM_MAX_TOKENS` | hard cap on `max_tokens` | `512` |
| `HF_TOKEN` | gated models | – |

## API

- `GET /health` — liveness, ok before model load.
- `GET /v1/models` — orchestrator readiness probe; never triggers a load.
- `POST /v1/chat/completions` — streaming + non-streaming. The model loads
  lazily on the first request (`AutoModel.from_pretrained(path,
  compression='4bit')`). No tool calling (`tools` ignored); sampling params
  ignored; `max_tokens` capped at `AIRLLM_MAX_TOKENS`.

**Queue depth 1:** one generation at a time; a concurrent second request gets
HTTP 429.

**Streaming caveat:** token-by-token streaming is impractical with AirLLM
(every token re-streams all layers from disk), so `stream: true` generates the
full reply first, then replays it as SSE word-chunks ending with
`data: [DONE]`. Time-to-first-byte equals total generation time.
