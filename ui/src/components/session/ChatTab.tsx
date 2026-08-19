import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import { api, errorMessage } from "../../api/client";
import {
  eventMessageInfo,
  eventPart,
  eventPermission,
  mostRecentOcSession,
  opencode,
  type OcEvent,
  type OcMessageInfo,
  type OcPart,
  type OcPermission,
} from "../../api/opencode";
import type { ModelEntry, Session } from "../../api/types";
import { useToast } from "../../hooks/toast";
import { getToken } from "../../lib/auth";
import { cx, opencodeModelId } from "../../lib/utils";
import { IconCheck, IconPlay, IconSend, IconStop, IconWrench, IconX } from "../icons";
import { Markdown } from "../lazy-markdown";
import { Button, Collapsible, EmptyState, SkeletonBlock, Spinner } from "../ui";

// ── Chat state: messages merged from history + SSE part updates ─────────────

interface ChatMessage {
  info: OcMessageInfo;
  partOrder: string[];
  parts: Record<string, OcPart>;
}

interface ChatState {
  order: string[];
  byId: Record<string, ChatMessage>;
}

type ChatAction =
  | { type: "reset"; messages: { info: OcMessageInfo; parts: OcPart[] }[] }
  | { type: "info"; info: OcMessageInfo }
  | { type: "part"; part: OcPart };

function partKey(part: OcPart, fallbackIndex: number): string {
  return part.id ?? part.callID ?? `part-${fallbackIndex}`;
}

function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case "reset": {
      const next: ChatState = { order: [], byId: {} };
      for (const m of action.messages) {
        const msg: ChatMessage = { info: m.info, partOrder: [], parts: {} };
        m.parts.forEach((p, i) => {
          const key = partKey(p, i);
          if (!(key in msg.parts)) msg.partOrder.push(key);
          msg.parts[key] = p;
        });
        next.order.push(m.info.id);
        next.byId[m.info.id] = msg;
      }
      return next;
    }
    case "info": {
      const id = action.info.id;
      const existing = state.byId[id];
      const msg: ChatMessage = existing
        ? { ...existing, info: { ...existing.info, ...action.info } }
        : { info: action.info, partOrder: [], parts: {} };
      return {
        order: existing ? state.order : [...state.order, id],
        byId: { ...state.byId, [id]: msg },
      };
    }
    case "part": {
      const messageId = action.part.messageID;
      if (!messageId) return state;
      const existing = state.byId[messageId];
      const base: ChatMessage = existing ?? {
        info: { id: messageId },
        partOrder: [],
        parts: {},
      };
      const key = partKey(action.part, base.partOrder.length);
      const msg: ChatMessage = {
        ...base,
        partOrder: key in base.parts ? base.partOrder : [...base.partOrder, key],
        parts: { ...base.parts, [key]: { ...base.parts[key], ...action.part } },
      };
      return {
        order: existing ? state.order : [...state.order, messageId],
        byId: { ...state.byId, [messageId]: msg },
      };
    }
  }
}

// ── Part renderers ──────────────────────────────────────────────────────────

function safeJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2) ?? "";
  } catch {
    return String(value);
  }
}

function ToolPartCard({ part }: { part: OcPart }) {
  const status = part.state?.status ?? "unknown";
  const running = status === "running" || status === "pending";
  const failed = status === "error" || Boolean(part.state?.error);
  const input = part.state?.input;
  const output = part.state?.output ?? part.state?.error;
  const title = part.state?.title;

  return (
    <div
      className={cx(
        "my-1.5 rounded-lg border bg-raised/60 px-3 py-2",
        failed ? "border-danger/30" : "border-border",
      )}
    >
      <Collapsible
        summary={
          <span className="flex items-center gap-2 font-mono text-xs">
            {running ? (
              <Spinner size={13} />
            ) : failed ? (
              <IconX size={13} className="shrink-0 text-danger" />
            ) : (
              <IconWrench size={13} className="shrink-0 text-accent/80" />
            )}
            <span className="font-semibold text-text">
              {part.tool ?? "tool"}
            </span>
            {typeof title === "string" && title && (
              <span className="truncate text-faint">{title}</span>
            )}
            <span
              className={cx(
                "ml-auto shrink-0",
                failed ? "text-danger" : running ? "text-info" : "text-faint",
              )}
            >
              {status}
            </span>
          </span>
        }
      >
        <div className="space-y-2 pb-1">
          {input !== undefined && (
            <div>
              <p className="mb-0.5 text-[10px] font-semibold tracking-wider text-faint uppercase">
                Arguments
              </p>
              <pre className="max-h-48 overflow-auto rounded-md border border-border bg-bg px-2.5 py-2 font-mono text-[11px] leading-relaxed whitespace-pre-wrap text-muted">
                {safeJson(input)}
              </pre>
            </div>
          )}
          {output !== undefined && output !== "" && (
            <div>
              <p className="mb-0.5 text-[10px] font-semibold tracking-wider text-faint uppercase">
                Result
              </p>
              <pre className="max-h-56 overflow-auto rounded-md border border-border bg-bg px-2.5 py-2 font-mono text-[11px] leading-relaxed whitespace-pre-wrap text-muted">
                {typeof output === "string" ? output : safeJson(output)}
              </pre>
            </div>
          )}
          {running && input === undefined && (
            <p className="text-xs text-faint">Waiting for the tool…</p>
          )}
        </div>
      </Collapsible>
    </div>
  );
}

function MessageBubble({ message }: { message: ChatMessage }) {
  const role = message.info.role ?? "assistant";
  const isUser = role === "user";
  const parts = message.partOrder
    .map((k) => message.parts[k])
    .filter((p): p is OcPart => Boolean(p));

  const rendered = parts
    .map((part, i) => {
      if (part.type === "text" && typeof part.text === "string" && part.text) {
        return isUser ? (
          <p key={i} className="text-sm break-words whitespace-pre-wrap">
            {part.text}
          </p>
        ) : (
          <Markdown key={i} text={part.text} />
        );
      }
      if (part.type === "tool") {
        return <ToolPartCard key={i} part={part} />;
      }
      if (
        part.type === "reasoning" &&
        typeof part.text === "string" &&
        part.text.trim()
      ) {
        return (
          <div key={i} className="my-1 text-xs text-faint italic">
            <Collapsible summary="Reasoning">
              <p className="break-words whitespace-pre-wrap">{part.text}</p>
            </Collapsible>
          </div>
        );
      }
      return null;
    })
    .filter(Boolean);

  if (!rendered.length) return null;

  return (
    <div className={cx("flex", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cx(
          "max-w-[92%] md:max-w-[85%]",
          isUser
            ? "rounded-2xl rounded-br-md bg-accent/15 px-3.5 py-2.5 text-text"
            : "w-full",
        )}
      >
        {rendered}
      </div>
    </div>
  );
}

function PermissionCard({
  permission,
  onRespond,
  busy,
}: {
  permission: OcPermission;
  onRespond: (response: "once" | "always" | "reject") => void;
  busy: boolean;
}) {
  const label =
    (typeof permission.title === "string" && permission.title) ||
    (typeof permission.type === "string" && permission.type) ||
    "Tool permission requested";
  return (
    <div className="my-2 rounded-xl border border-warn/40 bg-warn/5 p-3.5">
      <p className="text-sm font-semibold text-warn">Permission request</p>
      <p className="mt-1 font-mono text-xs break-words text-text">{label}</p>
      {permission.metadata && Object.keys(permission.metadata).length > 0 && (
        <pre className="mt-2 max-h-36 overflow-auto rounded-md border border-border bg-bg px-2.5 py-2 font-mono text-[11px] whitespace-pre-wrap text-muted">
          {safeJson(permission.metadata)}
        </pre>
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        <Button
          size="sm"
          variant="primary"
          disabled={busy}
          onClick={() => onRespond("once")}
        >
          <IconCheck size={14} />
          Allow once
        </Button>
        <Button size="sm" disabled={busy} onClick={() => onRespond("always")}>
          Always
        </Button>
        <Button
          size="sm"
          variant="danger"
          disabled={busy}
          onClick={() => onRespond("reject")}
        >
          Deny
        </Button>
      </div>
    </div>
  );
}

// ── Main chat tab ───────────────────────────────────────────────────────────

export default function ChatTab({
  session,
  model,
}: {
  session: Session;
  model: ModelEntry | null;
}) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [chat, dispatch] = useReducer(chatReducer, { order: [], byId: {} });
  const [permissions, setPermissions] = useState<Record<string, OcPermission>>(
    {},
  );
  const [draft, setDraft] = useState("");
  const [streamLost, setStreamLost] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const running = session.state === "running";

  // 1) Resolve (or create) the OpenCode session inside the container.
  const ocSession = useQuery({
    queryKey: ["ocSession", session.id],
    enabled: running,
    // OpenCode may still be booting right after the container starts.
    retry: 5,
    retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 8000),
    queryFn: async () => {
      const list = await opencode.listSessions(session.id);
      const recent = mostRecentOcSession(Array.isArray(list) ? list : []);
      if (recent) return recent.id;
      const created = await opencode.createSession(session.id, session.name);
      return created.id;
    },
  });
  const ocId = ocSession.data ?? null;

  // 2) Load history whenever the OpenCode session resolves.
  const history = useQuery({
    queryKey: ["ocMessages", session.id, ocId],
    enabled: Boolean(ocId) && running,
    queryFn: () => opencode.listMessages(session.id, ocId!),
  });

  useEffect(() => {
    if (history.data) {
      dispatch({ type: "reset", messages: history.data });
    }
  }, [history.data]);

  // 3) Live updates over the per-session SSE feed, filtered to this OC session.
  useEffect(() => {
    if (!ocId || !running) return;
    const token = getToken();
    if (!token) return;
    let source: EventSource | null = null;
    let retryTimer: number | undefined;
    let disposed = false;

    const matchesSession = (sid: unknown) => sid == null || sid === ocId;

    const handleEvent = (event: OcEvent) => {
      const type = typeof event.type === "string" ? event.type : "";
      if (type === "forge.disconnected") {
        setStreamLost(true);
        return;
      }
      if (type.includes("permission")) {
        const perm = eventPermission(event);
        if (!perm || !matchesSession(perm.sessionID)) return;
        if (type.includes("replied") || type.includes("removed")) {
          setPermissions((prev) => {
            const next = { ...prev };
            delete next[perm.id];
            return next;
          });
        } else {
          setPermissions((prev) => ({ ...prev, [perm.id]: perm }));
        }
        return;
      }
      const part = eventPart(event);
      if (part && matchesSession(part.sessionID)) {
        dispatch({ type: "part", part });
        return;
      }
      const info = eventMessageInfo(event);
      if (info && matchesSession(info.sessionID)) {
        dispatch({ type: "info", info });
      }
    };

    const connect = () => {
      if (disposed) return;
      source = new EventSource(
        `/api/sessions/${session.id}/events?token=${encodeURIComponent(token)}`,
      );
      source.onopen = () => setStreamLost(false);
      source.onmessage = (msg) => {
        try {
          handleEvent(JSON.parse(msg.data) as OcEvent);
        } catch {
          // unknown frame — never crash the chat
        }
      };
      source.onerror = () => {
        source?.close();
        source = null;
        setStreamLost(true);
        retryTimer = window.setTimeout(connect, 3000);
      };
    };
    connect();
    return () => {
      disposed = true;
      source?.close();
      if (retryTimer) window.clearTimeout(retryTimer);
    };
  }, [session.id, ocId, running]);

  // Auto-scroll when content grows.
  const messageCount = chat.order.length;
  const partsFingerprint = useMemo(
    () =>
      chat.order
        .map((id) => {
          const m = chat.byId[id];
          return `${id}:${m.partOrder.length}:${
            m.partOrder.reduce(
              (n, k) => n + (m.parts[k]?.text?.length ?? 0),
              0,
            )
          }`;
        })
        .join("|"),
    [chat],
  );
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [messageCount, partsFingerprint]);

  const isGenerating = useMemo(() => {
    const lastId = chat.order[chat.order.length - 1];
    if (!lastId) return false;
    const last = chat.byId[lastId];
    return (
      last.info.role === "assistant" &&
      last.info.time?.completed == null &&
      !last.info.error
    );
  }, [chat]);

  const send = useMutation({
    mutationFn: async (text: string) => {
      if (!ocId || !model) throw new Error("Session model not resolved yet");
      return opencode.sendMessage(
        session.id,
        ocId,
        "forge-local",
        opencodeModelId(model.display_name, model.id),
        text,
      );
    },
    onSuccess: () => {
      // Resync once the turn completes — SSE already streamed the parts.
      void queryClient.invalidateQueries({
        queryKey: ["ocMessages", session.id, ocId],
      });
    },
    onError: (err) => toast("error", errorMessage(err)),
  });

  const abort = useMutation({
    mutationFn: () => {
      if (!ocId) throw new Error("No active OpenCode session");
      return opencode.abort(session.id, ocId);
    },
    onError: (err) => toast("error", errorMessage(err)),
  });

  const respond = useMutation({
    mutationFn: ({
      id,
      response,
    }: {
      id: string;
      response: "once" | "always" | "reject";
    }) => {
      if (!ocId) throw new Error("No active OpenCode session");
      return opencode.respondPermission(session.id, ocId, id, response);
    },
    onSuccess: (_data, vars) => {
      setPermissions((prev) => {
        const next = { ...prev };
        delete next[vars.id];
        return next;
      });
    },
    onError: (err) => toast("error", errorMessage(err)),
  });

  const doSend = useCallback(() => {
    const text = draft.trim();
    if (!text || send.isPending || !ocId || !model) return;
    setDraft("");
    if (textareaRef.current) textareaRef.current.style.height = "auto";
    send.mutate(text);
  }, [draft, send, ocId, model]);

  const onComposerKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      doSend();
    }
  };

  const autoGrow = () => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  };

  const busy = send.isPending || isGenerating;
  const messages = chat.order
    .map((id) => chat.byId[id])
    .filter((m): m is ChatMessage => Boolean(m));

  return (
    <div className="flex min-h-[60dvh] flex-col">
      {streamLost && running && (
        <p
          role="alert"
          className="mb-2 rounded-md border border-warn/30 bg-warn/10 px-3 py-1.5 text-center text-xs text-warn"
        >
          Live stream interrupted — reconnecting…
        </p>
      )}

      <div className="flex-1 space-y-4 pb-4">
        {!running && (
          <EmptyState
            icon="box"
            title="Session is not running"
            hint="Start the container to chat with the agent."
          />
        )}

        {running && (ocSession.isLoading || history.isLoading) && (
          <div className="space-y-3" aria-busy="true" aria-label="Loading chat">
            <SkeletonBlock className="ml-auto h-10 w-2/3" />
            <SkeletonBlock className="h-24 w-5/6" />
            <SkeletonBlock className="ml-auto h-10 w-1/2" />
          </div>
        )}

        {running && ocSession.isError && (
          <EmptyState
            icon="search"
            title="Agent unreachable"
            hint={`OpenCode inside the container did not answer: ${errorMessage(ocSession.error)}`}
            action={
              <Button onClick={() => void ocSession.refetch()}>Retry</Button>
            }
          />
        )}

        {running &&
          ocId &&
          !history.isLoading &&
          messages.length === 0 && (
            <EmptyState
              icon="spark"
              title="Say hello to your agent"
              hint="It can read, write and run code in this workspace."
            />
          )}

        {messages.map((m) => (
          <MessageBubble key={m.info.id} message={m} />
        ))}

        {Object.values(permissions).map((perm) => (
          <PermissionCard
            key={perm.id}
            permission={perm}
            busy={respond.isPending}
            onRespond={(response) => respond.mutate({ id: perm.id, response })}
          />
        ))}

        {busy && (
          <div className="flex items-center gap-2 text-xs text-muted">
            <Spinner size={14} />
            Generating…
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Composer */}
      <div className="sticky bottom-0 -mx-4 border-t border-border bg-bg/95 px-4 pt-3 pb-safe backdrop-blur md:mx-0 md:rounded-t-xl">
        {!running ? (
          <div className="flex items-center justify-between gap-3 pb-3">
            <p className="text-sm text-muted">
              The session container is {session.state}. Start it to send
              messages.
            </p>
            <StartButton sessionId={session.id} />
          </div>
        ) : (
          <div className="flex items-end gap-2 pb-3">
            <label htmlFor="chat-composer" className="sr-only">
              Message the agent
            </label>
            <textarea
              id="chat-composer"
              ref={textareaRef}
              rows={1}
              value={draft}
              placeholder={
                model
                  ? `Message ${model.display_name}…`
                  : "Resolving session model…"
              }
              disabled={!ocId || !model}
              onChange={(e) => {
                setDraft(e.target.value);
                autoGrow();
              }}
              onKeyDown={onComposerKeyDown}
              className="max-h-40 min-h-11 flex-1 resize-none rounded-xl border border-edge bg-raised px-3.5 py-2.5 text-sm text-text placeholder:text-faint focus:border-accent focus:outline-none"
            />
            {busy ? (
              <Button
                variant="danger"
                aria-label="Stop generating"
                onClick={() => abort.mutate()}
                loading={abort.isPending}
                className="h-11 w-11 shrink-0 rounded-xl p-0"
              >
                <IconStop size={18} />
              </Button>
            ) : (
              <Button
                variant="primary"
                aria-label="Send message"
                onClick={doSend}
                disabled={!draft.trim() || !ocId || !model}
                className="h-11 w-11 shrink-0 rounded-xl p-0"
              >
                <IconSend size={18} />
              </Button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function StartButton({ sessionId }: { sessionId: string }) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const start = useMutation({
    mutationFn: () => api.startSession(sessionId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["session", sessionId] });
      void queryClient.invalidateQueries({ queryKey: ["sessions"] });
    },
    onError: (err) => toast("error", errorMessage(err)),
  });
  return (
    <Button variant="primary" loading={start.isPending} onClick={() => start.mutate()}>
      <IconPlay size={15} />
      Start
    </Button>
  );
}
