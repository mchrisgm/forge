# Engine survey — what serves models on Forge, and what could

Forge's job is to run *any* worthwhile open-weight model on one consumer box
(reference: 12 GB RTX 4070 Ti Super, 48 GB RAM). This document is the working
survey behind the engine-lane roster: what each candidate engine would add,
what it costs to integrate, and the verdict. Every claim below was verified
against the project's live registry/README on 2026-08-20 — versions and image
names included — not recalled from memory.

The bar for a new lane: (1) it must serve something the current lanes cannot,
(2) an official, maintained container image must exist, (3) it must speak
OpenAI-compatible `/v1` (Forge's router, chat, and sessions all assume it),
and (4) it must earn its keep on a 12 GB card.

## Current roster

| Lane | Serves | Why it exists |
|---|---|---|
| llama.cpp | GGUF quants, full-GPU or CPU-offload | the workhorse: best format coverage per GB |
| vLLM | separate AWQ variant builds | high-throughput batching for 4-bit AWQ |
| SGLang | any native safetensors as published (bf16 / embedded awq / gptq / fp8) | newest-arch coverage (Kimi/DeepSeek MoE first), RadixAttention |
| TabbyAPI (ExLlamaV3) | EXL3/EXL2 quants | consumer-GPU specialist — see below |
| AirLLM | fp16-from-disk streaming, ≤ 70B | last resort when nothing fits |
| imagegen (diffusers) | text-to-image | the image lane |

Engine choice is automatic: `fit_rules.detect_lane` reads each checkpoint's
real `config.json` (architectures, `model_type`, `quantization_config`,
custom code) and routes it — see the README's Engine lanes section.

One more container sits *beside* the lanes rather than in them: the **auto
router** (`forge-engine-router`, label `forge.router`, port 8087) — a tiny
llama.cpp instance serving the Settings-chosen router model that picks the
answering model for "Auto" conversations (`app/services/model_router.py`).
It never holds a GPU lease: multi-GPU boxes park it fully offloaded on the
smallest-VRAM GPU, single-GPU boxes run it on CPU so the main lane keeps
its VRAM.

## Added from this survey: TabbyAPI (ExLlamaV3)

**What it adds.** ExLlamaV3's EXL3 format is the state of the art for
squeezing models onto single consumer GPUs: variable bitrate (2–6 bpw) with
quality-per-GB ahead of AWQ/GPTQ at the same size, so a 12 GB card runs
bigger models (or the same model with far more context) than the vLLM lane
can. TabbyAPI is its official server: OpenAI-compatible API with OAI-style
tool calling, vision-model support, and even MoE expert CPU-offload
(`cpu_moe_offload_layers`). None of the existing lanes can load EXL3/EXL2
repos at all — before this lane, detection had nowhere to send them.

**Verified:** official image `ghcr.io/theroyallab/tabbyapi` (`latest` /
`cu13` — rolling tags, the project does not publish semver image tags; pin by
digest in `.env` if you need reproducibility). Current main is
ExLlamaV3-first (EXL2 support preserved on a branch). Image entrypoint is
`python3` + `main.py`, every config key doubles as a CLI flag
(`--model-name`, `--max-seq-len`, `--disable-auth`, …), port 5000 by default
(Forge runs it on its own lane port).

**Detection:** an EXL3/EXL2 checkpoint carries
`quantization_config.quant_method` in its `config.json`; `detect_lane`
routes those to this lane (SGLang/vLLM cannot load them).

## AMD / ROCm (llama.cpp lane)

Forge is NVIDIA-first, but the GPU is identified by vendor from the kernel
devices (`scripts/gpu-detect.sh`), so an AMD box is detected and gets the
`docker-compose.rocm.yml` overlay instead of the NVIDIA one. Support is scoped
to the **llama.cpp (GGUF) lane** — it is the only engine with a mature HIP
backend and the widest format coverage per GB, which matches the "workhorse"
role in the roster. vLLM/SGLang/TabbyAPI/AirLLM remain CUDA-only; the engine
manager refuses those lanes on AMD with an actionable message rather than
letting a CUDA image crash.

How it wires up:

- **Detection** — `/dev/kfd` + a `/dev/dri/renderD*` node ⇒ vendor `amd`. VRAM,
  used, and busy% come straight from amdgpu sysfs
  (`/sys/class/drm/card*/device/mem_info_vram_*`, `gpu_busy_percent`), so the
  orchestrator needs no ROCm libraries — only `/dev/dri` mounted (the overlay).
- **Device wiring** — `engine_manager.gpu_run_kwargs` mounts `/dev/kfd` +
  `/dev/dri`, joins the `video`/`render` groups, sets `HIP_VISIBLE_DEVICES`,
  and applies `HSA_OVERRIDE_GFX_VERSION` when configured.
- **Image** — `engines/llamacpp-rocm/Dockerfile` builds `llama-server` with
  `GGML_HIP` for `gfx900;gfx906;gfx908;gfx90a;gfx1030` on ROCm 5.7. gfx900 is
  the **Instinct MI25** (Vega 10); ROCm 6 dropped it, hence the 5.7 pin.
  Override `ROCM_TAG`/`GPU_TARGETS` (build args) for newer cards.

Caveat: legacy datacenter cards like the MI25 are best-effort — driver/ROCm
version and per-card ISA support vary; `FORGE_HSA_OVERRIDE_GFX_VERSION` is the
usual lever when HIP can't find a matching kernel.

## Evaluated and not (yet) added

### KTransformers — the one to watch
CPU+GPU hybrid serving for huge MoE models: experts live in RAM (AMX/AVX
kernels), attention on GPU — DeepSeek/Kimi-class models on a desktop. This is
the *only* engine that could genuinely replace AirLLM's niche with usable
speed. Verified state: `approachingai/ktransformers` on Docker Hub, stable
`v0.5.3` (2026-04) plus very active but *model-family-specific* experimental
images (`DSV4-specific*`, avx2/tp variants, 2026-08). Two blockers for a lane
today: the images are per-model-family rather than general, and its flagship
use (671B-class MoE) wants 192 GB+ RAM — the reference box's 48 GB covers
only the smaller MoE tier. Revisit when a general-purpose versioned image
lands; the lane would slot in exactly like SGLang did.

### Ollama — redundant by construction
Same llama.cpp backend and GGUF format as the existing lane (verified active,
`ollama/ollama:0.32.15`). Adding it would duplicate the llama.cpp lane with a
second model store and lifecycle Forge already provides. Nothing new fits
because of it. Skipped.

### LMDeploy (TurboMind) — capable, fully overlapped
Active (`openmmlab/lmdeploy:v0.16.0`), fast AWQ serving, OpenAI-compatible.
Everything it serves on this hardware, vLLM or SGLang already serves.
A third overlapping GPU lane adds a scheduler choice with no new models.
Skipped.

### Aphrodite Engine — interesting format breadth, weaker pulse
vLLM fork with wide quant-format support. Verified `alpindale/
aphrodite-openai:latest` last updated 2025-11 — noticeably slower cadence
than every lane Forge ships. Its unique formats are covered by the TabbyAPI
lane (EXL2/EXL3) and llama.cpp (GGUF). Skipped.

### Text Generation Inference (TGI) — solid, adds nothing here
Active (`ghcr.io/huggingface/text-generation-inference`). Datacenter-oriented
serving of the same safetensors/AWQ/GPTQ space as vLLM/SGLang. Skipped.

### MLC-LLM — wrong distribution story
Compiler-based, its own weight format, no official container registry
presence (pip/conda wheels). Its real promise is non-NVIDIA hardware
(Vulkan/Metal/ROCm) — relevant only if Forge targets AMD boxes someday.
Skipped.

### TensorRT-LLM — wrong maintenance economics
Fastest NVIDIA serving, but engines are compiled per GPU/driver/version;
every model download would need a build step measured in tens of minutes,
and upgrades invalidate the cache. Wrong trade for a self-hosted box.
Skipped.

## Beyond chat: modality lanes worth planning for

These aren't chat engines, so they're roadmap notes rather than lanes:

- **Embeddings** — `ghcr.io/huggingface/text-embeddings-inference` (TEI,
  verified active). A small always-on embeddings service would upgrade
  Forge's memory retrieval from FTS to semantic search and enable RAG over
  workspace files. Tiny VRAM cost; biggest bang-per-GB on the roadmap.
- **Speech** — an STT/TTS pair (Whisper-family server + Kokoro/Piper) would
  give the chat voice notes and read-aloud. CPU-friendly options exist;
  candidates need the same official-image + OpenAI-audio-API bar as above.

## How to add the next lane

SGLang and TabbyAPI were added the same way; the recipe is five small
touches plus tests (grep any one of them for the pattern):

1. `EngineKind` + `Quant` in `orchestrator/app/models.py`
2. image/port/concurrency in `config.py`, service in `docker-compose.yml`
3. launch command + port map + container branch in `engine_manager.py`,
   slot budget in `chat_jobs.py`
4. routing rule in `fit_rules.detect_lane` (+ `_artifact_for_lane` in
   `models_api.py`)
5. prefetch/sweep lists in `Makefile`, `scripts/setup.*`, `scripts/verify.sh`;
   UI badge + types; README lanes table
