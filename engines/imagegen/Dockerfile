# Forge imagegen lane. Built by compose as image `forge-imagegen`;
# started/stopped by the orchestrator's EngineManager, never by `compose up`.
#
# Base: official PyTorch runtime with CUDA 12.4 — satisfies diffusers'
# torch>=2.6 requirement without a 2+ GB pip download of torch itself.
FROM pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime

# diffusers pulls in huggingface_hub/numpy/Pillow/safetensors; transformers is
# needed for the text encoders (CLIP/T5) and accelerate for fast weight loading.
RUN pip install --no-cache-dir \
    diffusers==0.39.0 \
    transformers==5.15.1 \
    accelerate==1.14.0 \
    safetensors==0.8.0 \
    fastapi==0.115.12 \
    "uvicorn[standard]==0.34.0"

WORKDIR /app
COPY server.py /app/server.py

# Keep HF snapshots on the shared models volume.
ENV HF_HOME=/data/models/hf-cache \
    IMAGEGEN_PORT=8084

EXPOSE 8084

# Shell form so ${IMAGEGEN_PORT} from the orchestrator-provided env is honored.
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${IMAGEGEN_PORT:-8084}"]
