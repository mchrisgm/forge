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
    imagegen = "imagegen"  # diffusers-based text-to-image lane


class Quant(str, Enum):
    gguf_q4_k_m = "gguf-q4_k_m"
    awq = "awq"
    fp16_airllm = "fp16-airllm"
    fp16_diffusers = "fp16-diffusers"


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


class MemoryKind(str, Enum):
    fact = "fact"            # durable facts about the user or their world
    preference = "preference"  # how they like things done
    project = "project"      # ongoing work context
    episode = "episode"      # summarized notable past interaction


class User(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True)
    display_name: str = ""
    password_hash: str = ""
    is_admin: bool = False
    personal_instructions: str = ""  # injected into every chat system prompt
    memory_enabled: bool = True
    avatar_color: str = ""  # UI accent, e.g. "#f59e0b"
    created_at: datetime = Field(default_factory=utcnow)
    last_active_at: datetime = Field(default_factory=utcnow)


class Conversation(SQLModel, table=True):
    id: str = Field(default_factory=new_uuid, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    title: str = "New chat"
    model_slug: str = ""  # "" = whatever single model is serving
    thinking: ThinkingLevel = ThinkingLevel.auto
    memory_enabled: bool = True
    archived: bool = False
    # Rolling compression: messages with id <= summarized_until are folded
    # into `summary` and dropped from the live context (see services/memory).
    summary: str = ""
    summarized_until: int = 0
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ChatMessage(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    conversation_id: str = Field(foreign_key="conversation.id", index=True)
    role: str = "user"  # user | assistant | system
    content: str = ""
    attachments_json: str = "[]"  # list of Upload ids
    token_estimate: int = 0
    created_at: datetime = Field(default_factory=utcnow)


class Upload(SQLModel, table=True):
    id: str = Field(default_factory=new_uuid, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    filename: str = ""
    mime: str = ""
    kind: str = "other"  # image | text | pdf | other
    size_bytes: int = 0
    path: str = ""  # under uploads_dir
    generated: bool = False  # produced by Forge (image gen), not user-uploaded
    prompt: str = ""  # generation prompt, when generated
    created_at: datetime = Field(default_factory=utcnow)


class MemoryEntry(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    kind: MemoryKind = MemoryKind.fact
    content: str = ""
    importance: float = 1.0  # decays with age, boosted by use
    pinned: bool = False  # always injected, never auto-pruned
    source_conversation_id: str = ""
    use_count: int = 0
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    last_used_at: datetime = Field(default_factory=utcnow)


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
    vision: bool = False  # multimodal: chat sends images as data-URI parts
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
    user_id: int | None = Field(default=None, foreign_key="user.id", index=True)
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
    user_id: int | None = Field(default=None, foreign_key="user.id", index=True)
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
    # Connectors are PER USER: each profile has its own catalog rows and
    # tokens (uniqueness of (user_id, kind) is enforced in code — SQLite
    # cannot alter constraints in place).
    user_id: int | None = Field(default=None, foreign_key="user.id", index=True)
    # Free string: core kinds (ConnectorKind values), catalog integration ids
    # (notion, linear, …), or "custom-<slug>" for user-defined MCP servers.
    kind: str = Field(index=True)
    enabled: bool = True
    config_json: str = "{}"  # secrets + custom definitions — LAN-only threat model (PLAN §7)


class Setting(SQLModel, table=True):
    key: str = Field(primary_key=True)
    value: str = ""
