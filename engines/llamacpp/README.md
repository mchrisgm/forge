# llama.cpp engine — default lane

No build here: this lane runs the upstream image
**`ghcr.io/ggml-org/llama.cpp:server-cuda`** directly (see
`FORGE_LLAMACPP_IMAGE` in `orchestrator/app/config.py`). The orchestrator's
EngineManager starts/stops the container; compose never does.

## Flags passed by the orchestrator

`build_llamacpp_command` in `orchestrator/app/services/engine_manager.py`:

```
llama-server -m /data/models/<file_path> --host 0.0.0.0 --port 8081 \
  -c <ctx> --n-gpu-layers <ngl> --parallel <slots> \
  --jinja --flash-attn --alias <display_name>
```

- `<ctx>` = `min(model.ctx_max, FORGE_DEFAULT_CTX)` (default 16384).
- `<ngl>` computed at load time by `fit_rules.compute_ngl`: binary-search the
  largest layer count whose estimated VRAM (layer ≈ file_size/n_layers, plus
  KV for those layers, plus ~1.2 GB overhead) fits the 11 GB budget; remaining
  layers offload to RAM (32 GB cap, PLAN §2).
- `--jinja` enables the model's chat template so OpenAI-style tool calling
  works through OpenCode.
- `--alias` makes `/v1/models` report the catalog display name.

## Slots vs context (PLAN §6.2)

`--parallel <slots>` (default 2, `FORGE_LLAMACPP_SLOTS`) lets parallel
sessions share one server — but **each slot splits the context budget**: with
`-c 16384` and 2 slots, every request gets 8192 tokens of context. More
concurrent sessions ⇒ raise slots and accept a smaller per-session context, or
keep slots low for long-context work. The UI model detail view documents this
tradeoff.

Health/readiness: `GET http://forge-engine-llamacpp:8081/v1/models` → 200.
