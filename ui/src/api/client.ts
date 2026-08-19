import { announceUnauthorized, getToken } from "../lib/auth";
import type {
  AuthResult,
  AuthStatus,
  ChatDoneFrame,
  ChatStatus,
  Connector,
  Conversation,
  ConversationDetail,
  CustomConnectorBody,
  EnginesStatus,
  FileResponse,
  FilesResponse,
  GitCommitEntry,
  GitStatus,
  ImageGenerationResult,
  Lease,
  ManualAddBody,
  MemoryEntry,
  MemoryKind,
  ModelEntry,
  ModelSearchKind,
  ModelSearchResult,
  PublicUser,
  SearchAddBody,
  Session,
  SettingsPayload,
  Skill,
  Suggestion,
  SystemStats,
  Task,
  ThinkingDirectives,
  ThinkingLevel,
  UploadMeta,
  UserProfile,
} from "./types";

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, detail: unknown) {
    super(typeof detail === "string" ? detail : detailMessage(detail, status));
    this.status = status;
    this.detail = detail;
  }
}

function detailMessage(detail: unknown, status: number): string {
  if (detail && typeof detail === "object") {
    const d = detail as Record<string, unknown>;
    if (typeof d.message === "string") return d.message;
    try {
      return JSON.stringify(detail);
    } catch {
      /* fall through */
    }
  }
  return `Request failed (HTTP ${status})`;
}

/** Human-readable message from any thrown value. */
export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return String(err);
}

async function request<T>(
  path: string,
  init: RequestInit & { skipAuthRedirect?: boolean } = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  // FormData bodies set their own multipart boundary — never override.
  if (
    init.body != null &&
    !(init.body instanceof FormData) &&
    !headers.has("Content-Type")
  ) {
    headers.set("Content-Type", "application/json");
  }

  let resp: Response;
  try {
    resp = await fetch(path, { ...init, headers });
  } catch {
    throw new ApiError(0, "Network error — is the orchestrator reachable?");
  }

  if (resp.status === 401 && !init.skipAuthRedirect) {
    announceUnauthorized();
    throw new ApiError(401, "Not authenticated");
  }

  let payload: unknown = null;
  const text = await resp.text();
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = text;
    }
  }

  if (!resp.ok) {
    const detail =
      payload && typeof payload === "object" && "detail" in (payload as object)
        ? (payload as { detail: unknown }).detail
        : payload;
    throw new ApiError(resp.status, detail ?? `HTTP ${resp.status}`);
  }
  return payload as T;
}

const get = <T>(path: string) => request<T>(path);
const post = <T>(path: string, body?: unknown) =>
  request<T>(path, {
    method: "POST",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
const put = <T>(path: string, body: unknown) =>
  request<T>(path, { method: "PUT", body: JSON.stringify(body) });
const patch = <T>(path: string, body: unknown) =>
  request<T>(path, { method: "PATCH", body: JSON.stringify(body) });
const del = <T>(path: string) => request<T>(path, { method: "DELETE" });

export const api = {
  // auth (multi-user — routers/users.py)
  authStatus: () =>
    request<AuthStatus>("/api/auth/status", { skipAuthRedirect: true }),
  login: (username: string, password: string) =>
    request<AuthResult>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
      skipAuthRedirect: true,
    }),
  register: (username: string, password: string, display_name?: string) =>
    request<AuthResult>("/api/auth/register", {
      method: "POST",
      body: JSON.stringify({ username, password, display_name: display_name ?? "" }),
      skipAuthRedirect: true,
    }),
  authCheck: () => get<{ ok: boolean; user: UserProfile }>("/api/auth/check"),

  // users
  me: () => get<UserProfile>("/api/users/me"),
  patchMe: (body: {
    display_name?: string;
    personal_instructions?: string;
    memory_enabled?: boolean;
    avatar_color?: string;
  }) => patch<UserProfile>("/api/users/me", body),
  changeMyPassword: (current_password: string, new_password: string) =>
    post<{ ok: boolean }>("/api/users/me/password", {
      current_password,
      new_password,
    }),
  listUsers: () => get<PublicUser[]>("/api/users"),
  setRegistration: (allow_registration: boolean) =>
    post<{ allow_registration: boolean }>("/api/users/registration", {
      allow_registration,
    }),

  // chat conversations
  listConversations: (archived = false) =>
    get<Conversation[]>(`/api/chat/conversations?archived=${archived}`),
  createConversation: (body: {
    title?: string;
    model_slug?: string;
    thinking?: ThinkingLevel;
    memory_enabled?: boolean;
  }) => post<Conversation>("/api/chat/conversations", body),
  getConversation: (id: string) =>
    get<ConversationDetail>(`/api/chat/conversations/${id}`),
  patchConversation: (
    id: string,
    body: {
      title?: string;
      model_slug?: string;
      thinking?: ThinkingLevel;
      memory_enabled?: boolean;
      archived?: boolean;
    },
  ) => patch<Conversation>(`/api/chat/conversations/${id}`, body),
  deleteConversation: (id: string) =>
    del<{ ok: boolean }>(`/api/chat/conversations/${id}`),
  chatStatus: () => get<ChatStatus>("/api/chat/status"),
  /** Generate an image in chat — can take minutes; resolves with the upload. */
  generateImage: (body: {
    prompt: string;
    /** Null = temporary chat; the exchange is not recorded server-side. */
    conversation_id: string | null;
    /** "local" (imagegen lane) or an enabled remote connector kind. */
    provider: string;
    size: string;
  }) => post<ImageGenerationResult>("/api/chat/image", body),

  // files (chat attachments)
  uploadFile: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<UploadMeta>("/api/files", { method: "POST", body: form });
  },
  deleteFile: (id: string) => del<{ ok: boolean }>(`/api/files/${id}`),

  // memory
  listMemories: () => get<MemoryEntry[]>("/api/memory"),
  addMemory: (body: { content: string; kind?: MemoryKind; pinned?: boolean }) =>
    post<MemoryEntry>("/api/memory", body),
  patchMemory: (
    id: number,
    body: {
      content?: string;
      kind?: MemoryKind;
      pinned?: boolean;
      importance?: number;
    },
  ) => patch<MemoryEntry>(`/api/memory/${id}`, body),
  deleteMemory: (id: number) => del<{ ok: boolean }>(`/api/memory/${id}`),
  clearMemories: () => del<{ deleted: boolean }>("/api/memory"),
  consolidateMemory: () => post<Record<string, unknown>>("/api/memory/consolidate"),

  // sessions
  listSessions: () => get<Session[]>("/api/sessions"),
  getSession: (id: string) => get<Session>(`/api/sessions/${id}`),
  createSession: (body: {
    name: string;
    model_id: number;
    repo_url?: string;
  }) => post<Session>("/api/sessions", body),
  startSession: (id: string) => post<Session>(`/api/sessions/${id}/start`),
  stopSession: (id: string) => post<Session>(`/api/sessions/${id}/stop`),
  deleteSession: (id: string) => del<{ ok: boolean }>(`/api/sessions/${id}`),

  // session files
  listFiles: (id: string, path: string) =>
    get<FilesResponse>(
      `/api/sessions/${id}/files?path=${encodeURIComponent(path)}`,
    ),
  readFile: (id: string, path: string) =>
    get<FileResponse>(
      `/api/sessions/${id}/file?path=${encodeURIComponent(path)}`,
    ),
  writeFile: (id: string, path: string, content: string) =>
    put<{ ok: boolean }>(`/api/sessions/${id}/file`, { path, content }),

  // session git
  gitStatus: (id: string) => get<GitStatus>(`/api/sessions/${id}/git/status`),
  gitLog: (id: string) => get<GitCommitEntry[]>(`/api/sessions/${id}/git/log`),
  gitDiff: (id: string) =>
    get<{ diff: string }>(`/api/sessions/${id}/git/diff`),
  gitCommit: (id: string, message: string) =>
    post<{ output: string }>(`/api/sessions/${id}/git/commit`, { message }),
  gitPush: (id: string) =>
    post<{ output: string }>(`/api/sessions/${id}/git/push`),

  // tasks
  listSessionTasks: (id: string) => get<Task[]>(`/api/sessions/${id}/tasks`),
  createTask: (id: string, prompt: string, thinking: ThinkingLevel = "auto") =>
    post<Task>(`/api/sessions/${id}/tasks`, { prompt, thinking }),

  // models
  listModels: () => get<ModelEntry[]>("/api/models"),
  addModel: (body: ManualAddBody) => post<ModelEntry>("/api/models", body),
  deleteModel: (id: number) => del<{ ok: boolean }>(`/api/models/${id}`),
  downloadModel: (id: number) =>
    post<{ ok: boolean }>(`/api/models/${id}/download`),
  listSuggestions: () => get<Suggestion[]>("/api/models/suggestions"),
  approveSuggestion: (id: number) =>
    post<ModelEntry>(`/api/models/suggestions/${id}/approve`),
  dismissSuggestion: (id: number) =>
    post<{ ok: boolean }>(`/api/models/suggestions/${id}/dismiss`),
  triggerScan: () =>
    post<{ new_suggestions: number; considered: number }>(
      "/api/models/registry/scan",
    ),
  searchModels: (q: string, kind: ModelSearchKind) =>
    get<ModelSearchResult[]>(
      `/api/models/search?q=${encodeURIComponent(q)}&kind=${kind}`,
    ),
  addFromSearch: (body: SearchAddBody) =>
    post<ModelEntry>("/api/models/search/add", body),
  thinkingDirectives: (modelId: number, level: ThinkingLevel) =>
    get<ThinkingDirectives>(`/api/models/${modelId}/thinking/${level}`),

  // engines
  enginesStatus: () => get<EnginesStatus>("/api/engines"),
  loadEngine: (
    modelId: number,
    opts: { force?: boolean; gpu_index?: number; gpu_count?: number } = {},
  ) =>
    post<{ lease: Lease }>("/api/engines/load", {
      model_id: modelId,
      force: opts.force ?? false,
      ...(opts.gpu_index != null ? { gpu_index: opts.gpu_index } : {}),
      ...(opts.gpu_count != null && opts.gpu_count > 1
        ? { gpu_count: opts.gpu_count }
        : {}),
    }),
  /** Unload one GPU's engine, or everything when gpuIndex is omitted. */
  unloadEngine: (gpuIndex?: number) =>
    post<{ leases: Lease[] }>(
      gpuIndex == null
        ? "/api/engines/unload"
        : `/api/engines/unload?gpu_index=${gpuIndex}`,
    ),

  // system
  systemStats: () => get<SystemStats>("/api/system/stats"),

  // skills
  listSkills: () => get<Skill[]>("/api/skills"),
  installSkill: (git_url: string, subdir?: string) =>
    post<Skill>("/api/skills/install", { git_url, subdir }),
  deleteSkill: (id: number) => del<{ ok: boolean }>(`/api/skills/${id}`),
  patchSkill: (id: number, enabled: boolean) =>
    patch<Skill>(`/api/skills/${id}`, { enabled }),

  // connectors
  listConnectors: () => get<Connector[]>("/api/connectors"),
  patchConnector: (
    kind: string,
    body: { enabled?: boolean; config?: Record<string, string> },
  ) => patch<Connector>(`/api/connectors/${encodeURIComponent(kind)}`, body),
  addCustomConnector: (body: CustomConnectorBody) =>
    post<Connector>("/api/connectors/custom", body),
  deleteConnector: (kind: string) =>
    del<{ ok: boolean }>(`/api/connectors/${encodeURIComponent(kind)}`),

  // settings
  getSettings: () => get<SettingsPayload>("/api/settings"),
  patchSettings: (body: {
    session_idle_min?: number;
    registry_cron?: string;
  }) => patch<SettingsPayload>("/api/settings", body),
};

/**
 * Direct URL for an uploaded file — used in <img> tags where auth headers are
 * impossible, so the token rides along as a query parameter.
 */
export function fileUrl(id: string): string {
  const token = getToken();
  return `/api/files/${id}${token ? `?token=${encodeURIComponent(token)}` : ""}`;
}

// ── Thinking directives (cached per model+level) ────────────────────────────

const thinkingCache = new Map<string, Promise<ThinkingDirectives>>();

/**
 * GET /api/models/{id}/thinking/{level}, memoized per model+level — the
 * directives are pure functions of the model family, so one fetch per
 * combination is enough for the whole app session.
 */
export function getThinkingDirectives(
  modelId: number,
  level: ThinkingLevel,
): Promise<ThinkingDirectives> {
  const key = `${modelId}:${level}`;
  let hit = thinkingCache.get(key);
  if (!hit) {
    hit = api.thinkingDirectives(modelId, level).catch((err: unknown) => {
      thinkingCache.delete(key); // don't cache failures
      throw err;
    });
    thinkingCache.set(key, hit);
  }
  return hit;
}

// ── Engine scratch chat (POST /api/engines/chat, OpenAI-compatible) ─────────

export interface ChatCompletionMessage {
  role: "system" | "user" | "assistant";
  content: string;
}

export interface EngineChatOptions {
  signal?: AbortSignal;
  maxTokens?: number;
  /** Lease slug — picks the serving engine when several models are loaded. */
  model?: string;
  /** Reasoning level; "auto" (default) sends no directive. */
  thinking?: ThinkingLevel;
}

/**
 * POST `body` to a streaming endpoint and return the raw Response so the
 * caller can consume `response.body` incrementally.
 * Throws ApiError on HTTP failure; AbortError is rethrown untouched.
 */
async function streamPost(
  path: string,
  body: unknown,
  signal?: AbortSignal,
): Promise<Response> {
  const headers = new Headers({ "Content-Type": "application/json" });
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);

  let resp: Response;
  try {
    resp = await fetch(path, {
      method: "POST",
      headers,
      signal,
      body: JSON.stringify(body),
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError(0, "Network error — is the orchestrator reachable?");
  }

  if (resp.status === 401) {
    announceUnauthorized();
    throw new ApiError(401, "Not authenticated");
  }
  if (!resp.ok) {
    let detail: unknown = null;
    const text = await resp.text().catch(() => "");
    if (text) {
      try {
        const parsed: unknown = JSON.parse(text);
        detail =
          parsed && typeof parsed === "object" && "detail" in (parsed as object)
            ? (parsed as { detail: unknown }).detail
            : parsed;
      } catch {
        detail = text;
      }
    }
    throw new ApiError(resp.status, detail ?? `HTTP ${resp.status}`);
  }
  return resp;
}

/**
 * Start a streaming chat completion against a loaded engine.
 * Returns the raw Response (text/event-stream of OpenAI chunk frames ending
 * with `data: [DONE]`) so callers can consume `response.body` incrementally.
 * Throws ApiError on HTTP failure — 409 means no engine lease is ready.
 * An AbortError from `opts.signal` is rethrown untouched.
 */
export function engineChatStream(
  messages: ChatCompletionMessage[],
  opts: EngineChatOptions = {},
): Promise<Response> {
  return streamPost(
    "/api/engines/chat",
    {
      messages,
      stream: true,
      ...(opts.maxTokens != null ? { max_tokens: opts.maxTokens } : {}),
      ...(opts.model ? { model: opts.model } : {}),
      ...(opts.thinking && opts.thinking !== "auto"
        ? { thinking: opts.thinking }
        : {}),
    },
    opts.signal,
  );
}

// ── Chat streaming (routers/chat.py, SSE over fetch — EventSource can't POST)

/** POST /api/chat/conversations/{id}/messages — persisted chat turn. */
export function conversationMessageStream(
  conversationId: string,
  body: {
    content: string;
    attachment_ids?: string[];
    /** Overrides the conversation default; omit/auto = use it. */
    thinking?: ThinkingLevel;
  },
  signal?: AbortSignal,
): Promise<Response> {
  return streamPost(
    `/api/chat/conversations/${conversationId}/messages`,
    {
      content: body.content,
      ...(body.attachment_ids?.length
        ? { attachment_ids: body.attachment_ids }
        : {}),
      ...(body.thinking && body.thinking !== "auto"
        ? { thinking: body.thinking }
        : {}),
    },
    signal,
  );
}

/** POST /api/chat/temporary — incognito turn; the client keeps the history. */
export function temporaryChatStream(
  body: {
    messages: { role: string; content: string }[];
    model_slug?: string;
    thinking?: ThinkingLevel;
    attachment_ids?: string[];
  },
  signal?: AbortSignal,
): Promise<Response> {
  return streamPost(
    "/api/chat/temporary",
    {
      messages: body.messages,
      ...(body.model_slug ? { model_slug: body.model_slug } : {}),
      ...(body.thinking ? { thinking: body.thinking } : {}),
      ...(body.attachment_ids?.length
        ? { attachment_ids: body.attachment_ids }
        : {}),
    },
    signal,
  );
}

export interface ChatStreamHandlers {
  /** A non-empty assistant content fragment arrived. */
  onDelta: (fragment: string) => void;
  /** An in-stream `data: {"error": "..."}` frame arrived. */
  onError?: (message: string) => void;
  /** The final `data: {"forge":"done", ...}` frame arrived. */
  onDone?: (frame: ChatDoneFrame) => void;
}

/**
 * Consume a Forge chat SSE body: OpenAI chunk frames, possible in-stream
 * error frames, terminated by a {"forge":"done"} frame ("[DONE]" markers
 * from the upstream engine are passed through and ignored here).
 */
export async function readChatStream(
  body: ReadableStream<Uint8Array>,
  handlers: ChatStreamHandlers,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const handleLine = (line: string): boolean => {
    const trimmed = line.trim();
    if (!trimmed.startsWith("data:")) return false;
    const data = trimmed.slice(5).trim();
    if (!data || data === "[DONE]") return false;
    let frame: unknown;
    try {
      frame = JSON.parse(data);
    } catch {
      return false; // partial/non-JSON frame — never crash the chat
    }
    if (!frame || typeof frame !== "object") return false;
    const obj = frame as Record<string, unknown>;
    if (typeof obj.error === "string") {
      handlers.onError?.(obj.error);
      return false;
    }
    if (obj.forge === "done") {
      handlers.onDone?.(obj as ChatDoneFrame);
      return true;
    }
    const fragment = (
      obj as { choices?: { delta?: { content?: unknown } }[] }
    ).choices?.[0]?.delta?.content;
    if (typeof fragment === "string" && fragment) handlers.onDelta(fragment);
    return false;
  };

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        if (handleLine(line)) return;
      }
    }
    buffer += decoder.decode();
    if (buffer) handleLine(buffer);
  } finally {
    reader.releaseLock();
  }
}
