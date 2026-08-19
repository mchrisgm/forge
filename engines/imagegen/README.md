# Imagegen engine — text-to-image lane

Diffusers-based text-to-image server exposing an OpenAI Images API subset.
Works with any `AutoPipelineForText2Image`-loadable checkpoint (SD 1.5/2.x,
SDXL, SDXL-Turbo, LCM, Lightning, ...). Backs the chat page's `local` image
provider (`orchestrator/app/services/image_service.py`).

## Build / run

Built by compose as `forge-imagegen`; the orchestrator's EngineManager starts
it with env from `build_imagegen_env`:

| Env | Meaning | Default |
|---|---|---|
| `IMAGEGEN_MODEL_PATH` | local diffusers snapshot dir or HF repo id | (required) |
| `IMAGEGEN_MODEL_NAME` | id reported by `/v1/models` | derived from path |
| `IMAGEGEN_PORT` | listen port | `8084` |
| `IMAGEGEN_STEPS` | inference steps override | heuristic (see below) |
| `IMAGEGEN_GUIDANCE` | guidance scale override | heuristic (see below) |
| `HF_TOKEN` | gated models | – |

**Step/guidance heuristic:** if the model path or name contains `turbo`,
`lcm`, or `lightning`, defaults are 4 steps at guidance 0.0 (few-step
distilled checkpoints); otherwise 25 steps at guidance 7.0.

## API

- `GET /health` — liveness, ok before pipeline load.
- `GET /v1/models` — orchestrator readiness probe; never triggers a load.
- `POST /v1/images/generations` — OpenAI Images subset. The pipeline loads
  lazily on the first request (`AutoPipelineForText2Image.from_pretrained`
  in fp16, preferring the `fp16` variant when the snapshot has one).
  Body: `prompt` (required), `n` (default 1, capped at 4), `size` (default
  `1024x1024`; `WxH` with each dim clamped to 256..1536 and rounded down to a
  multiple of 8), `response_format`. Response:
  `{"created": <ts>, "data": [{"b64_json": "<base64 PNG>"}, ...]}`.
  Also served at `/v1/v1/images/generations` because the orchestrator joins
  the lease base URL (which already ends in `/v1`) with the full path.

**`response_format` note:** only `b64_json` is produced. `url` is accepted
but still answered with `b64_json` entries — this server has no place to
host files.

**Queue depth 1:** one generation at a time; a concurrent second request gets
HTTP 429. Errors: 400 for a missing/empty `prompt` or an unparseable `size`,
500 with detail if the diffusion run fails.
