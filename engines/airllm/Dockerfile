# Forge AirLLM slow lane (PLAN §6.2). Built by compose as image `forge-airllm`;
# started/stopped by the orchestrator's EngineManager, never by `compose up`.
#
# Base: official PyTorch runtime with CUDA 12.4 — satisfies airllm's
# torch>=2.4 requirement without a 2+ GB pip download of torch itself.
FROM pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime

# torch pulls in Triton, which JIT-compiles its CUDA driver shim (driver.c)
# on first use and shells out to a C compiler. The -runtime base ships no
# toolchain, so 4bit/bitsandbytes generation failed with "Failed to find C
# compiler. Please specify via CC environment variable." gcc is the smallest
# package that satisfies triton/runtime/build.py.
RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*
ENV CC=gcc

# airllm pulls in transformers/accelerate/safetensors/sentencepiece;
# bitsandbytes is required for compression='4bit' (block-wise quantization).
RUN pip install --no-cache-dir \
    airllm==3.2.0 \
    bitsandbytes==0.45.3 \
    fastapi==0.115.12 \
    "uvicorn[standard]==0.34.0"

WORKDIR /app
COPY server.py /app/server.py

# Keep HF snapshots and AirLLM's per-layer shards on the shared models volume
# (the layer-split step can need ~2x the model's disk size).
ENV HF_HOME=/data/models/hf-cache \
    AIRLLM_SHARDS_DIR=/data/models/airllm-shards \
    AIRLLM_PORT=8083

EXPOSE 8083

# Shell form so ${AIRLLM_PORT} from the orchestrator-provided env is honored.
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${AIRLLM_PORT:-8083}"]
