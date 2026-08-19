// Mirrors orchestrator/app/models.py + router response shapes exactly.

export type EngineKind = "llamacpp" | "vllm" | "airllm";
export type Quant = "gguf-q4_k_m" | "awq" | "fp16-airllm";
export type ToolCallFormat = "hermes" | "qwen" | "llama3" | "none";
export type ModelStatus =
  | "suggested"
  | "approved"
  | "downloading"
  | "ready"
  | "failed";
export type SessionState = "creating" | "running" | "idle" | "stopped" | "error";
export type TaskState = "queued" | "running" | "done" | "failed";
export type ConnectorKind =
  | "github"
  | "searxng"
  | "fetch"
  | "playwright"
  | "skills";

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

export interface Connector {
  id: number;
  kind: ConnectorKind;
  enabled: boolean;
  config: Record<string, string>;
  has_token: boolean;
}

export interface Lease {
  model_id: number;
  model_name: string;
  engine: EngineKind;
  state: "starting" | "ready" | "failed";
  container_id: string;
  base_url: string;
  error: string;
  acquired_at: string;
}

export interface EnginesStatus {
  lease: Lease | null;
  engines: Record<
    string,
    { port: number; container: string; active: boolean }
  >;
}

export interface GpuStats {
  name: string;
  vram_total_gb: number;
  vram_used_gb: number;
  utilization_pct: number;
}

export interface SystemStats {
  gpu: GpuStats | null;
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

// ── Global SSE events (orchestrator/app/services/events.py publishers) ──────

export interface ForgeEvent {
  kind: string;
  ts: number;
  // engine.state
  lease?: Lease | null;
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
