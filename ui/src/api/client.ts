import { announceUnauthorized, getToken } from "../lib/auth";
import type {
  Connector,
  ConnectorKind,
  EnginesStatus,
  FileResponse,
  FilesResponse,
  GitCommitEntry,
  GitStatus,
  Lease,
  ManualAddBody,
  ModelEntry,
  Session,
  SettingsPayload,
  Skill,
  Suggestion,
  SystemStats,
  Task,
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
  if (init.body != null && !headers.has("Content-Type")) {
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
  // auth
  login: (password: string) =>
    request<{ token: string }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ password }),
      skipAuthRedirect: true,
    }),

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
  createTask: (id: string, prompt: string) =>
    post<Task>(`/api/sessions/${id}/tasks`, { prompt }),

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

  // engines
  enginesStatus: () => get<EnginesStatus>("/api/engines"),
  loadEngine: (modelId: number, force = false) =>
    post<{ lease: Lease }>("/api/engines/load", {
      model_id: modelId,
      force,
    }),
  unloadEngine: () => post<{ lease: null }>("/api/engines/unload"),

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
    kind: ConnectorKind,
    body: { enabled?: boolean; config?: Record<string, string> },
  ) => patch<Connector>(`/api/connectors/${kind}`, body),

  // settings
  getSettings: () => get<SettingsPayload>("/api/settings"),
  patchSettings: (body: {
    session_idle_min?: number;
    registry_cron?: string;
  }) => patch<SettingsPayload>("/api/settings", body),
  changePassword: (current_password: string, new_password: string) =>
    post<{ ok: boolean }>("/api/settings/password", {
      current_password,
      new_password,
    }),
};

// ── Engine scratch chat (POST /api/engines/chat, OpenAI-compatible) ─────────

export interface ChatCompletionMessage {
  role: "system" | "user" | "assistant";
  content: string;
}

/**
 * Start a streaming chat completion against the currently loaded engine.
 * Returns the raw Response (text/event-stream of OpenAI chunk frames ending
 * with `data: [DONE]`) so callers can consume `response.body` incrementally.
 * Throws ApiError on HTTP failure — 409 means no engine lease is ready.
 * An AbortError from `signal` is rethrown untouched.
 */
export async function engineChatStream(
  messages: ChatCompletionMessage[],
  signal?: AbortSignal,
  maxTokens?: number,
): Promise<Response> {
  const headers = new Headers({ "Content-Type": "application/json" });
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);

  let resp: Response;
  try {
    resp = await fetch("/api/engines/chat", {
      method: "POST",
      headers,
      signal,
      body: JSON.stringify({
        messages,
        stream: true,
        ...(maxTokens != null ? { max_tokens: maxTokens } : {}),
      }),
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
