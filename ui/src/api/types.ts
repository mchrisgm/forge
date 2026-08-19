// Mirrors orchestrator/app/models.py + router response shapes exactly.

export type EngineKind = "llamacpp" | "vllm" | "airllm" | "imagegen";
export type Quant = "gguf-q4_k_m" | "awq" | "fp16-airllm" | "fp16-diffusers";
export type ToolCallFormat = "hermes" | "qwen" | "llama3" | "none";
export type ModelStatus =
  | "suggested"
  | "approved"
  | "downloading"
  | "ready"
  | "failed";
export type SessionState = "creating" | "running" | "idle" | "stopped" | "error";
export type TaskState = "queued" | "running" | "done" | "failed";
export type ThinkingLevel = "auto" | "off" | "low" | "high";
export type ConnectorCategory =
  | "core"
  | "productivity"
  | "developer"
  | "design"
  | "business"
  | "custom";
export type McpType = "remote" | "local";

export interface ModelEntry {
  id: number;
  hf_repo: string;
  display_name: string;
  family: string;
  params_b: number;
  quant: Quant;
  file_path: string;
  size_gb: number;
  engine: EngineKind;
  ctx_max: number;
  n_layers: number;
  is_moe: boolean;
  tool_call_format: ToolCallFormat;
  status: ModelStatus;
  score: number;
  note: string;
  added_at: string;
}

export interface SuggestionReason {
  trend?: number;
  recency?: number;
  coding_signal?: number;
  fit?: number;
  lane?: string | null;
  score?: number;
  params_b?: number;
  is_moe?: boolean;
  gguf_repo?: string | null;
  gguf_file?: string | null;
  gguf_size_gb?: number;
  has_awq?: boolean;
}

export interface Suggestion {
  id: number;
  hf_repo: string;
  reason: SuggestionReason;
  created_at: string;
  dismissed: boolean;
}

export interface Session {
  id: string;
  name: string;
  container_id: string;
  state: SessionState;
  workspace_path: string;
  model_id: number | null;
  created_at: string;
  last_active_at: string;
  repo_url: string | null;
  last_error: string;
}

export interface Task {
  id: number;
  session_id: string;
  prompt: string;
  state: TaskState;
  opencode_session_id: string;
  result: string;
  created_at: string;
  finished_at: string | null;
  thinking?: ThinkingLevel;
}

/** GET /api/models/{id}/thinking/{level} — per-family reasoning directives. */
export interface ThinkingDirectives {
  family: string;
  level: ThinkingLevel;
  system: string;
  user_suffix: string;
}

export interface Skill {
  id: number;
  name: string;
  description: string;
  source_url: string;
  path: string;
  installed_at: string;
  enabled: boolean;
}

/** One credential/config field a connector needs (GET /api/connectors). */
export interface ConnectorAuthField {
  key: string;
  label: string;
  secret: boolean;
  placeholder: string;
  /** Secret + configured fields come back as the "••••••" mask. */
  value: string;
  configured: boolean;
}

export interface Connector {
  kind: string;
  name: string;
  description: string;
  category: ConnectorCategory;
  mcp_type: McpType;
  enabled: boolean;
  auth_fields: ConnectorAuthField[];
  auth_note: string;
  docs_url: string;
  is_custom: boolean;
  has_token: boolean;
}

/** POST /api/connectors/custom body. */
export interface CustomConnectorBody {
  name: string;
  mcp_type: McpType;
  url?: string;
  command?: string[];
  headers?: Record<string, string>;
  environment?: Record<string, string>;
}

export interface Lease {
  model_id: number;
  model_name: string;
  model_slug: string;
  engine: EngineKind;
  gpu_ids: number[];
  gpu_index: number;
  state: "starting" | "ready" | "failed";
  container_id: string;
  base_url: string;
  error: string;
  acquired_at: string;
}

export interface GpuSlot {
  index: number;
  lease: Lease | null;
}

export interface EnginesStatus {
  gpu_count: number;
  /** Backcompat: first active lease (single-GPU view). */
  lease: Lease | null;
  leases: Lease[];
  gpus: GpuSlot[];
  engines: Record<string, { port: number; active_on: number[] }>;
}

export interface GpuStats {
  index: number;
  name: string;
  vram_total_gb: number;
  vram_used_gb: number;
  utilization_pct: number;
}

export interface SystemStats {
  /** Backcompat: first GPU. */
  gpu: GpuStats | null;
  gpus: GpuStats[] | null;
  ram: { total_gb: number; used_gb: number; pct: number };
  cpu_pct: number;
  disk: { total_gb: number; used_gb: number; free_gb: number } | null;
  engine: EnginesStatus;
  session_containers: { name: string; status: string; session_id: string }[];
  docker_ok: boolean;
  budgets: { vram_gb: number; ram_offload_gb: number };
}

export interface SettingsPayload {
  session_idle_min: number;
  registry_cron: string;
  max_parallel_sessions: number;
  vram_budget_gb: number;
  ram_offload_budget_gb: number;
  llamacpp_slots: number;
}

export interface DirEntry {
  name: string;
  type: "file" | "dir" | "link" | "other";
  size: number;
}

export interface FilesResponse {
  path: string;
  entries: DirEntry[];
}

export interface FileResponse {
  path: string;
  content: string;
}

export interface GitStatus {
  branch: string;
  changes: { status: string; path: string }[];
}

export interface GitCommitEntry {
  hash: string;
  author: string;
  date: string;
  subject: string;
}

export interface ManualAddBody {
  hf_repo: string;
  display_name: string;
  engine: EngineKind;
  gguf_filename: string;
  params_b: number;
  ctx_max: number;
  tool_call_format: ToolCallFormat;
  auto_download: boolean;
}

// ── Hub search (GET /api/models/search) ─────────────────────────────────────

/** Which Hub pipeline to search: chat/code models or text-to-image. */
export type ModelSearchKind = "text" | "image";

export interface ModelSearchResult {
  hf_repo: string;
  downloads: number;
  likes: number;
  tags: string[];
  gated: boolean;
  created_at: string | null;
  /** Estimated size; 0 = unknown (always 0 for image models). */
  params_b: number;
  in_catalog: boolean;
}

/** POST /api/models/search/add body. */
export interface SearchAddBody {
  hf_repo: string;
  kind: ModelSearchKind;
  auto_download: boolean;
}

// ── Global SSE events (orchestrator/app/services/events.py publishers) ──────

export interface ForgeEvent {
  kind: string;
  ts: number;
  // engine.state
  lease?: Lease | null;
  gpu_index?: number;
  // download.*
  model_id?: number;
  hf_repo?: string;
  downloaded_gb?: number;
  total_gb?: number | null;
  pct?: number | null;
  size_gb?: number;
  error?: string;
  // session.state / session.deleted
  session_id?: string;
  state?: string;
  name?: string;
  // task.state
  task_id?: number;
  result?: string;
  // skill.installed / removed
  id?: number;
  // registry.scan_done
  new_suggestions?: number;
  // suggestion.approved
  suggestion_id?: number;
}

export interface DownloadProgress {
  model_id: number;
  downloaded_gb: number;
  total_gb: number | null;
  pct: number | null;
}

// ── Multi-user auth (orchestrator/app/routers/users.py) ─────────────────────

/** GET /api/auth/status — public, drives setup wizard + register screen. */
export interface AuthStatus {
  setup_required: boolean;
  allow_registration: boolean;
  user_count: number;
}

/** services/user_service.py public_profile() — the current user's profile. */
export interface UserProfile {
  id: number;
  username: string;
  display_name: string;
  is_admin: boolean;
  avatar_color: string;
  memory_enabled: boolean;
  personal_instructions: string;
  created_at: string;
}

/** GET /api/users — who else is on this Forge (public info only). */
export interface PublicUser {
  id: number;
  username: string;
  display_name: string;
  is_admin: boolean;
  avatar_color: string;
}

/** POST /api/auth/register and /api/auth/login. */
export interface AuthResult {
  token: string;
  user: UserProfile;
}

// ── Chat (orchestrator/app/routers/chat.py) ─────────────────────────────────

export interface Conversation {
  id: string;
  user_id: number;
  title: string;
  /** "" = whatever single model is serving. */
  model_slug: string;
  thinking: ThinkingLevel;
  memory_enabled: boolean;
  archived: boolean;
  summarized_until: number;
  created_at: string;
  updated_at: string;
}

export type AttachmentKind = "image" | "text" | "pdf" | "other";

/** Attachment metadata on chat messages (files_api Upload rows). */
export interface AttachmentMeta {
  id: string;
  filename: string;
  kind: AttachmentKind;
  mime: string;
  size_bytes: number;
  /** True when Forge generated this file (image generation). */
  generated?: boolean;
  /** The generation prompt — caption/alt text for generated images. */
  prompt?: string;
}

export interface ChatMessage {
  id: number;
  conversation_id: string;
  role: "user" | "assistant" | "system";
  content: string;
  token_estimate: number;
  created_at: string;
  attachments: AttachmentMeta[];
}

/** GET /api/chat/conversations/{id} — conversation plus full history. */
export interface ConversationDetail extends Conversation {
  summary: string;
  messages: ChatMessage[];
}

/** GET /api/chat/status — which models the composer can talk to. */
export interface ChatStatus {
  serving: Lease[];
  /** Ready imagegen-lane lease, or null when no image model is serving. */
  image: Lease | null;
}

/** POST /api/chat/image response — the generated file plus, for a saved
 *  conversation, the recorded exchange's message ids. */
export interface ImageGenerationResult {
  upload: AttachmentMeta;
  conversation_id: string | null;
  user_message_id: number | null;
  assistant_message_id: number | null;
}

/** data: {"forge":"done", ...} — final SSE frame of a chat stream. */
export interface ChatDoneFrame {
  conversation_id?: string;
  assistant_message_id?: number | null;
  temporary?: boolean;
}

// ── Files (orchestrator/app/routers/files_api.py) ───────────────────────────

/** POST /api/files response. */
export interface UploadMeta {
  id: string;
  filename: string;
  mime: string;
  kind: AttachmentKind;
  size_bytes: number;
  created_at: string;
}

// ── Memory (orchestrator/app/routers/memory_api.py) ─────────────────────────

export type MemoryKind = "fact" | "preference" | "project" | "episode";

export interface MemoryEntry {
  id: number;
  user_id: number;
  kind: MemoryKind;
  content: string;
  /** 0.1–2.0; decays with age, boosted by use. */
  importance: number;
  pinned: boolean;
  source_conversation_id: string;
  use_count: number;
  created_at: string;
  updated_at: string;
  last_used_at: string;
}
