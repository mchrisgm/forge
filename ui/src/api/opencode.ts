// Client for the per-session OpenCode server, reached through the
// orchestrator's authenticated reverse proxy at /api/sessions/{id}/opencode/*.
// OpenCode's event/message shapes drift between versions, so every accessor
// here is defensive: unknown shapes degrade gracefully instead of crashing.

import { announceUnauthorized, getToken } from "../lib/auth";
import { ApiError } from "./client";

export interface OcSession {
  id: string;
  title?: string;
  time?: { created?: number; updated?: number };
  [key: string]: unknown;
}

export interface OcMessageInfo {
  id: string;
  role?: string;
  sessionID?: string;
  time?: { created?: number; completed?: number };
  error?: unknown;
  [key: string]: unknown;
}

export interface OcPart {
  id?: string;
  type?: string;
  text?: string;
  messageID?: string;
  sessionID?: string;
  tool?: string;
  callID?: string;
  state?: {
    status?: string;
    input?: unknown;
    output?: unknown;
    error?: string;
    title?: string;
    [key: string]: unknown;
  };
  [key: string]: unknown;
}

export interface OcMessage {
  info: OcMessageInfo;
  parts: OcPart[];
}

export interface OcPermission {
  id: string;
  sessionID?: string;
  messageID?: string;
  title?: string;
  type?: string;
  pattern?: unknown;
  metadata?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface OcEvent {
  type?: string;
  properties?: Record<string, unknown>;
  [key: string]: unknown;
}

async function ocFetch<T>(
  sessionId: string,
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body != null && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  let resp: Response;
  try {
    resp = await fetch(`/api/sessions/${sessionId}/opencode/${path}`, {
      ...init,
      headers,
    });
  } catch {
    throw new ApiError(0, "Session container unreachable");
  }
  if (resp.status === 401) {
    announceUnauthorized();
    throw new ApiError(401, "Not authenticated");
  }
  const text = await resp.text();
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = text;
    }
  }
  if (!resp.ok) {
    throw new ApiError(resp.status, payload ?? `HTTP ${resp.status}`);
  }
  return payload as T;
}

export const opencode = {
  listSessions: (sessionId: string) =>
    ocFetch<OcSession[]>(sessionId, "session"),

  createSession: (sessionId: string, title: string) =>
    ocFetch<OcSession>(sessionId, "session", {
      method: "POST",
      body: JSON.stringify({ title }),
    }),

  listMessages: async (
    sessionId: string,
    ocSessionId: string,
  ): Promise<OcMessage[]> => {
    const raw = await ocFetch<unknown>(
      sessionId,
      `session/${ocSessionId}/message`,
    );
    if (!Array.isArray(raw)) return [];
    return raw
      .map((item): OcMessage | null => {
        if (!item || typeof item !== "object") return null;
        const record = item as Record<string, unknown>;
        // Newer OpenCode returns {info, parts}; older returned the info
        // object directly with parts inline.
        const info = (record.info ?? record) as OcMessageInfo;
        const parts = Array.isArray(record.parts)
          ? (record.parts as OcPart[])
          : [];
        if (!info || typeof info.id !== "string") return null;
        return { info, parts };
      })
      .filter((m): m is OcMessage => m !== null);
  },

  /**
   * Fire a prompt. Blocks server-side until the agent turn completes; stream
   * updates arrive via the session SSE feed, so callers should not await this
   * for UI updates. Sends both the flat and nested model reference so either
   * OpenCode input schema accepts it. `opts.system` maps to PromptInput.system
   * (extra system-prompt text, used for thinking-level directives).
   */
  sendMessage: (
    sessionId: string,
    ocSessionId: string,
    providerID: string,
    modelID: string,
    text: string,
    opts: { system?: string } = {},
  ) =>
    ocFetch<unknown>(sessionId, `session/${ocSessionId}/message`, {
      method: "POST",
      body: JSON.stringify({
        providerID,
        modelID,
        model: { providerID, modelID },
        parts: [{ type: "text", text }],
        ...(opts.system ? { system: opts.system } : {}),
      }),
    }),

  abort: (sessionId: string, ocSessionId: string) =>
    ocFetch<unknown>(sessionId, `session/${ocSessionId}/abort`, {
      method: "POST",
    }),

  respondPermission: (
    sessionId: string,
    ocSessionId: string,
    permissionId: string,
    response: "once" | "always" | "reject",
  ) =>
    ocFetch<unknown>(
      sessionId,
      `session/${ocSessionId}/permissions/${permissionId}`,
      { method: "POST", body: JSON.stringify({ response }) },
    ),
};

/** Extract a part's session id from a message.part.updated-style event. */
export function eventPart(event: OcEvent): OcPart | null {
  const props = event.properties;
  if (!props || typeof props !== "object") return null;
  const part = (props as Record<string, unknown>).part;
  if (!part || typeof part !== "object") return null;
  return part as OcPart;
}

export interface OcPartDelta {
  sessionID?: string;
  messageID: string;
  partID: string;
  field: string;
  delta: string;
}

/**
 * Parse a `message.part.delta` event: properties = {sessionID, messageID,
 * partID, field, delta} where `field` names a string field on the part
 * (usually "text") and `delta` is the fragment appended to it.
 */
export function eventPartDelta(event: OcEvent): OcPartDelta | null {
  const props = event.properties;
  if (!props || typeof props !== "object") return null;
  const p = props as Record<string, unknown>;
  if (typeof p.messageID !== "string" || typeof p.partID !== "string") {
    return null;
  }
  if (typeof p.delta !== "string") return null;
  return {
    sessionID: typeof p.sessionID === "string" ? p.sessionID : undefined,
    messageID: p.messageID,
    partID: p.partID,
    field: typeof p.field === "string" && p.field ? p.field : "text",
    delta: p.delta,
  };
}

export function eventMessageInfo(event: OcEvent): OcMessageInfo | null {
  const props = event.properties;
  if (!props || typeof props !== "object") return null;
  const info = (props as Record<string, unknown>).info;
  if (!info || typeof info !== "object") return null;
  const typed = info as OcMessageInfo;
  return typeof typed.id === "string" ? typed : null;
}

export function eventPermission(event: OcEvent): OcPermission | null {
  const props = (event.properties ?? event) as Record<string, unknown>;
  // Permission payloads appear either flat in properties or nested.
  const candidate =
    props.permission && typeof props.permission === "object"
      ? (props.permission as Record<string, unknown>)
      : props;
  if (typeof candidate.id === "string") return candidate as OcPermission;
  return null;
}

/**
 * The id of the permission request cleared by a `permission.replied` (or
 * removed) event. OpenCode 1.18.x sends {sessionID, requestID, reply}; older
 * builds used {permissionID} or a full nested permission object.
 */
export function eventPermissionRemovalId(event: OcEvent): string | null {
  const props = event.properties;
  if (props && typeof props === "object") {
    const p = props as Record<string, unknown>;
    if (typeof p.requestID === "string") return p.requestID;
    if (typeof p.permissionID === "string") return p.permissionID;
  }
  return eventPermission(event)?.id ?? null;
}

/**
 * Pick the most recently used OpenCode session from a listing. Background
 * tasks run through OpenCode sessions titled exactly "task-<id>" (see
 * orchestrator task_runner) — those must never hijack the interactive chat.
 */
export function mostRecentOcSession(list: OcSession[]): OcSession | null {
  const interactive = list.filter(
    // Hide ONLY the task runner's own sessions (titled exactly "task-<id>");
    // a user session legitimately named "task-…" must keep its chat thread.
    (s) => !(typeof s.title === "string" && /^task-\d+$/.test(s.title)),
  );
  if (!interactive.length) return null;
  const stamp = (s: OcSession) => s.time?.updated ?? s.time?.created ?? 0;
  return [...interactive].sort((a, b) => stamp(b) - stamp(a))[0] ?? null;
}
