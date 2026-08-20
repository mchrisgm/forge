# Forge ROCm llama.cpp lane — an AMD/ROCm build of the llama.cpp server, built
# by compose as `forge-llamacpp-rocm` and spawned by the orchestrator (never by
# `compose up`) with /dev/kfd + /dev/dri mounted and the video/render groups
# added. EXPERIMENTAL / best-effort: AMD support in Forge is limited to this
# GGUF lane; vLLM/SGLang/TabbyAPI/AirLLM stay NVIDIA-only.
#
# The AMD Instinct MI25 (Vega 10 = gfx900) is only supported by ROCm 5.x, so we
# pin ROCm 5.7 and target gfx900 by default. Newer cards can override ROCM_TAG
# and GPU_TARGETS at build time, e.g.:
#   docker build --build-arg ROCM_TAG=6.1.2-complete \
#                --build-arg GPU_TARGETS="gfx1100" engines/llamacpp-rocm
# If your installed ROCm build lacks your card's ISA, also set
# FORGE_HSA_OVERRIDE_GFX_VERSION (e.g. 9.0.0 for gfx900) so HIP falls back to a
# compatible ISA at runtime.
ARG ROCM_TAG=5.7.1-complete
FROM rocm/dev-ubuntu-22.04:${ROCM_TAG}

# Vega/CDNA/RDNA arch list; gfx900 = MI25. Semicolon-separated for CMake.
ARG GPU_TARGETS="gfx900;gfx906;gfx908;gfx90a;gfx1030"
# Pin a llama.cpp ref for reproducible builds; defaults to the tip of master.
ARG LLAMA_CPP_REF=master

RUN apt-get update && apt-get install -y --no-install-recommends \
      git cmake build-essential libcurl4-openssl-dev \
    && rm -rf /var/lib/apt/lists/*

ENV ROCM_PATH=/opt/rocm
ENV HIP_PLATFORM=amd
ENV PATH=/opt/rocm/bin:${PATH}

WORKDIR /opt
RUN git clone --depth 1 --branch "${LLAMA_CPP_REF}" \
        https://github.com/ggml-org/llama.cpp \
    || git clone --depth 1 https://github.com/ggml-org/llama.cpp
WORKDIR /opt/llama.cpp
RUN cmake -S . -B build \
      -DGGML_HIP=ON \
      -DAMDGPU_TARGETS="${GPU_TARGETS}" \
      -DGPU_TARGETS="${GPU_TARGETS}" \
      -DCMAKE_BUILD_TYPE=Release \
      -DLLAMA_CURL=ON \
 && cmake --build build --config Release -j"$(nproc)" --target llama-server \
 && cp build/bin/llama-server /usr/local/bin/llama-server \
 && rm -rf /opt/llama.cpp

# The orchestrator passes the SAME flags as the CUDA lane (-m, -c, --host,
# --port, --n-gpu-layers, --parallel, --jinja, --alias) as the container
# command, so the entrypoint is just the server binary.
ENTRYPOINT ["/usr/local/bin/llama-server"]
