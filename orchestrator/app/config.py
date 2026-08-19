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
    engine_load_timeout_s: int = 900
    default_ctx: int = 16384

    # GPUs: 0 = auto-detect via NVML at startup (falls back to 1).
    # vram_budget_gb stays PER GPU.
    gpu_count: int = 0

    # Docker plumbing
    docker_network: str = "forge-internal"
    models_volume: str = "forge-models"
    skills_volume: str = "forge-skills"
    workspaces_volume: str = "forge-workspaces"
    session_image: str = "forge-session-runner"
    llamacpp_image: str = "ghcr.io/ggml-org/llama.cpp:server-cuda"
    vllm_image: str = "vllm/vllm-openai:v0.10.1"
    airllm_image: str = "forge-airllm"
    imagegen_image: str = "forge-imagegen"

    # Engine ports (fixed per lane, PLAN §4)
    llamacpp_port: int = 8081
    vllm_port: int = 8082
    airllm_port: int = 8083
    imagegen_port: int = 8084

    # Where session containers reach the orchestrator's /v1 model router
    # (forge-internal DNS name of the compose service).
    orchestrator_internal_url: str = "http://orchestrator:8000"

    # Headroom context-compression proxy (compose service `headroom`). When
    # enabled AND healthy, ALL chat-completion traffic chains through it; the
    # Setting-table key "headroom_enabled" overrides the default at runtime.
    headroom_url: str = "http://headroom:8787/v1"
    headroom_enabled: bool = True

    # Optional integrations (no FORGE_ prefix, PLAN §8)
    hf_token: str = Field("", validation_alias=AliasChoices("HF_TOKEN", "FORGE_HF_TOKEN"))
    github_pat: str = Field("", validation_alias=AliasChoices("GITHUB_PAT", "FORGE_GITHUB_PAT"))


@lru_cache
def get_settings() -> Settings:
    return Settings()
