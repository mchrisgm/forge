"""All environment configuration in one place (PLAN §8).

Hardware budgets are config, not comments (PLAN §2): the fit rules and the
engine manager both read them from here.
"""

from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FORGE_", extra="ignore")

    # Auth
    password: str = "changeme"
    secret_key: str = ""  # empty -> generated once and persisted in the Setting table

    # Paths (inside the orchestrator container)
    db_path: str = "/data/db/forge.db"
    models_dir: str = "/data/models"
    skills_dir: str = "/data/skills"
    workspaces_dir: str = "/data/workspaces"
    uploads_dir: str = "/data/uploads"

    # Uploads (chat attachments)
    upload_max_mb: int = 20

    # Memory engine
    memory_token_budget: int = 700       # injected memory block cap
    chat_context_tokens: int = 6000      # prompt-side budget before compression
    chat_keep_tail_messages: int = 12    # recent turns never summarized away

    # Hardware budgets (PLAN §2)
    vram_budget_gb: float = 11.0
    ram_offload_budget_gb: float = 32.0

    # Sessions
    session_idle_min: int = 120
    max_parallel_sessions: int = 4
    session_mem_limit: str = "4g"
    session_cpus: float = 4.0
    session_pids_limit: int = 512

    # Registry
    registry_cron: str = "0 6 * * 1"
    registry_score_threshold: float = 0.45
    registry_scan_limit: int = 100

    # Engines
    llamacpp_slots: int = 2
    # Concurrent chat generations a vLLM lease accepts before Forge queues
    # more (vLLM batches internally; this just bounds how many jobs it fans in).
    vllm_max_concurrency: int = 8
    sglang_max_concurrency: int = 8  # same role as vllm's: bound the fan-in
    tabby_max_concurrency: int = 4   # exllamav3 batches, but more modestly
    # Safety net for a wedged engine: if a background generation receives no new
    # output for this long, Forge aborts it so the job reaches a terminal state
    # and frees its lane instead of hanging every reader forever. Generous by
    # default because AirLLM streams layers from disk and can be very slow;
    # raise it if huge cold models legitimately pause longer than this between
    # tokens.
    chat_stream_idle_timeout_s: float = 600.0
    engine_load_timeout_s: int = 900
    default_ctx: int = 16384

    # GPUs: 0 = auto-detect at startup (NVML for NVIDIA, sysfs for AMD; falls
    # back to 1). vram_budget_gb stays PER GPU.
    gpu_count: int = 0

    # GPU vendor: "auto" identifies NVIDIA vs AMD/ROCm from kernel devices;
    # override with FORGE_GPU_VENDOR=nvidia|amd|cpu when auto-detect is
    # ambiguous. Decides device wiring (NVIDIA device requests vs ROCm
    # /dev/kfd + /dev/dri mounts) and which llama.cpp image the lane uses.
    gpu_vendor: str = Field(
        "auto", validation_alias=AliasChoices("FORGE_GPU_VENDOR", "GPU_VENDOR")
    )
    # ROCm llama.cpp server image for AMD boxes (built locally from
    # engines/llamacpp-rocm — targets gfx900/gfx906/gfx908/gfx90a incl. the MI25).
    llamacpp_rocm_image: str = "forge-llamacpp-rocm"
    # HSA_OVERRIDE_GFX_VERSION for AMD cards the installed ROCm build doesn't
    # list natively (e.g. "9.0.0" for a gfx900 MI25 on a ROCm without gfx900).
    # Empty leaves it unset.
    hsa_override_gfx_version: str = Field(
        "", validation_alias=AliasChoices("HSA_OVERRIDE_GFX_VERSION")
    )

    # Docker plumbing
    docker_network: str = "forge-internal"
    models_volume: str = "forge-models"
    skills_volume: str = "forge-skills"
    workspaces_volume: str = "forge-workspaces"
    session_image: str = "forge-session-runner"
    llamacpp_image: str = "ghcr.io/ggml-org/llama.cpp:server-cuda"
    vllm_image: str = "vllm/vllm-openai:v0.10.1"
    sglang_image: str = "lmsysorg/sglang:v0.5.17"
    # TabbyAPI publishes rolling tags only (latest/cu13, no semver) — pin a
    # digest via FORGE_TABBY_IMAGE in .env for reproducible boxes.
    tabby_image: str = "ghcr.io/theroyallab/tabbyapi:latest"
    airllm_image: str = "forge-airllm"
    imagegen_image: str = "forge-imagegen"

    # Engine ports (fixed per lane, PLAN §4)
    llamacpp_port: int = 8081
    vllm_port: int = 8082
    sglang_port: int = 8085
    tabby_port: int = 8086
    router_port: int = 8087  # tiny auto-routing model (model_router.py)
    airllm_port: int = 8083
    imagegen_port: int = 8084

    # Where session containers reach the orchestrator's /v1 model router
    # (forge-internal DNS name of the compose service).
    orchestrator_internal_url: str = "http://orchestrator:8000"

    # Scrapling MCP service (compose service mcp-scrapling) — chat "read page".
    scrapling_mcp_url: str = "http://mcp-scrapling:8000/mcp"

    # Headroom context-compression proxy (compose service `headroom`). When
    # enabled AND healthy, ALL chat-completion traffic chains through it; the
    # Setting-table key "headroom_enabled" overrides the default at runtime.
    headroom_url: str = "http://headroom:8787/v1"
    headroom_enabled: bool = True

    sandbox_url: str = "http://smolvm:9000"  # smolvm sandbox lane (compose profile "sandbox")

    # Optional integrations (no FORGE_ prefix, PLAN §8)
    hf_token: str = Field("", validation_alias=AliasChoices("HF_TOKEN", "FORGE_HF_TOKEN"))
    github_pat: str = Field("", validation_alias=AliasChoices("GITHUB_PAT", "FORGE_GITHUB_PAT"))

    # OAuth app credentials for per-user connector sign-in (Settings-page
    # values in the Setting table override these env defaults). The admin
    # registers one OAuth app per provider pointing at their Forge host;
    # every profile then connects its own account through it.
    github_oauth_client_id: str = Field(
        "",
        validation_alias=AliasChoices(
            "GITHUB_OAUTH_CLIENT_ID", "FORGE_GITHUB_OAUTH_CLIENT_ID"
        ),
    )
    hf_oauth_client_id: str = Field(
        "",
        validation_alias=AliasChoices(
            "HF_OAUTH_CLIENT_ID", "FORGE_HF_OAUTH_CLIENT_ID"
        ),
    )
    hf_oauth_client_secret: str = Field(
        "",
        validation_alias=AliasChoices(
            "HF_OAUTH_CLIENT_SECRET", "FORGE_HF_OAUTH_CLIENT_SECRET"
        ),
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
