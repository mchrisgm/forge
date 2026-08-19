"""SQLModel tables (PLAN §5)."""

import uuid
from datetime import UTC, datetime
from enum import Enum

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_uuid() -> str:
    return str(uuid.uuid4())


class EngineKind(str, Enum):
    llamacpp = "llamacpp"
    vllm = "vllm"
    airllm = "airllm"


class Quant(str, Enum):
    gguf_q4_k_m = "gguf-q4_k_m"
    awq = "awq"
    fp16_airllm = "fp16-airllm"


class ToolCallFormat(str, Enum):
    hermes = "hermes"
    qwen = "qwen"
    llama3 = "llama3"
    none = "none"


class ModelStatus(str, Enum):
    suggested = "suggested"
    approved = "approved"
    downloading = "downloading"
    ready = "ready"
    failed = "failed"


class SessionState(str, Enum):
    creating = "creating"
    running = "running"
    idle = "idle"
    stopped = "stopped"
    error = "error"


class TaskState(str, Enum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"


class ConnectorKind(str, Enum):
    """The five core connectors (PLAN §1.8). The Connector table's kind column
    is a free string so catalog integrations (notion, linear, figma, …) and
    custom user-added MCP servers share the same storage."""

    github = "github"
    searxng = "searxng"
    fetch = "fetch"
    playwright = "playwright"
    skills = "skills"


class ThinkingLevel(str, Enum):
    auto = "auto"  # no directives — the model's native default
    off = "off"
    low = "low"
    high = "high"


class ModelEntry(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    hf_repo: str = Field(index=True)
    display_name: str
    family: str = ""
    params_b: float = 0.0
    quant: Quant = Quant.gguf_q4_k_m
    file_path: str = ""  # gguf filename (llamacpp) or snapshot dir (vllm/airllm), under models_dir
    size_gb: float = 0.0
    engine: EngineKind = EngineKind.llamacpp
    ctx_max: int = 16384
    n_layers: int = 0  # 0 = estimate from params_b at load time
    is_moe: bool = False
    tool_call_format: ToolCallFormat = ToolCallFormat.none
    status: ModelStatus = ModelStatus.approved
    score: float = 0.0
    note: str = ""  # tool-reliability notes (PLAN §14)
    added_at: datetime = Field(default_factory=utcnow)


class Suggestion(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    hf_repo: str = Field(index=True, unique=True)
    reason: str = "{}"  # json: {trend, recency, coding_signal, fit, lane, score, ...}
    created_at: datetime = Field(default_factory=utcnow)
    dismissed: bool = False


class Session(SQLModel, table=True):
    id: str = Field(default_factory=new_uuid, primary_key=True)
    name: str
    container_id: str = ""
    state: SessionState = SessionState.creating
    workspace_path: str = ""
    model_id: int | None = Field(default=None, foreign_key="modelentry.id")
    created_at: datetime = Field(default_factory=utcnow)
    last_active_at: datetime = Field(default_factory=utcnow)
    repo_url: str | None = None
    last_error: str = ""


class Task(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    session_id: str = Field(foreign_key="session.id", index=True)
    prompt: str
    thinking: ThinkingLevel = ThinkingLevel.auto
    state: TaskState = TaskState.queued
    opencode_session_id: str = ""  # OpenCode-side session created for this task
    result: str = ""  # last assistant text or error detail
    created_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None


class Skill(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    description: str = ""
    source_url: str = ""
    path: str = ""
    installed_at: datetime = Field(default_factory=utcnow)
    enabled: bool = True


class Connector(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    # Free string: core kinds (ConnectorKind values), catalog integration ids
    # (notion, linear, …), or "custom-<slug>" for user-defined MCP servers.
    kind: str = Field(index=True, unique=True)
    enabled: bool = True
    config_json: str = "{}"  # secrets + custom definitions — LAN-only threat model (PLAN §7)


class Setting(SQLModel, table=True):
    key: str = Field(primary_key=True)
    value: str = ""
