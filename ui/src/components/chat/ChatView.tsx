// The conversation surface: history, token-by-token streaming over SSE
// (fetch + ReadableStream — EventSource cannot POST), inline error frames with
// retry, and a temporary (incognito) mode that keeps everything in component
// state and stores nothing.
//
// Generation runs as a SERVER-SIDE background job, so the SSE reader's lifetime
// is decoupled from the component: leaving a chat only DETACHES the viewer
// (closing the reader never stops the job), and returning RE-ATTACHES via GET
// /conversations/{id}/stream — which replays the buffered tokens then streams
// live to `forge:done`. On open we always reattach: an `idle` frame means
// nothing is running (just show stored history); otherwise the pending bubble
// appears the moment the stream opens (eagerly when the history ends on a user
// turn, else on the first frame) and replayed stage/thinking/answer frames
// restore the live view. `forge:queued`/`status` frames drive the pre-token
// stage line (busy lane, "prompt sent … — processing" + elapsed); reasoning
// deltas and `<think>` spans stream into a collapsed Thinking expander.
// Because jobs are server-side, multiple conversations generate at once; the
// composer lock is per open conversation.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useBlocker, useNavigate } from "react-router-dom";
import {
  api,
  ApiError,
  conversationMessageStream,
  conversationReattachStream,
  errorMessage,
  readChatStream,
  temporaryChatStream,
} from "../../api/client";
import type {
  AttachmentMeta,
  ConversationDetail,
  ThinkingLevel,
  UploadMeta,
} from "../../api/types";
import { useToast } from "../../hooks/toast";
import { cx, opencodeModelId } from "../../lib/utils";
import { IconChevronLeft, IconCube, IconGhost, IconImage } from "../icons";
import {
  loadStoredThinking,
  storeThinking,
} from "../ThinkingSelect";
import { Button, EmptyState, LaneBadge, Spinner } from "../ui";
import {
  Composer,
  type ComposerHandle,
  type ImageProvider,
} from "./Composer";
import { ModelPicker, type ModelOption } from "./ModelPicker";
import {
  MessageBubble,
  splitStoredThinking,
  type UiMessage,
} from "./messages";
import { SandboxContext, type SandboxRunner } from "./sandbox-context";
import { StarterPanel } from "./StarterPanel";

/** The model_slug sentinel for router-picked models (backend AUTO_SLUG). */
const AUTO_SLUG = "auto";
const AUTO_LABEL = "Auto — picks the best model";

const THINKING_STORAGE_KEY = "forge.thinking.chats";

const IMAGE_SIZE = "1024x1024";

interface SendError {
  kind: "no-model" | "other";
  message: string;
}

/** The last sent turn, kept for one-click retry after a failure. */
type LastSend =
  | { type: "text"; content: string; metas: AttachmentMeta[] }
  | { type: "image"; prompt: string; provider: string };

/** 409s from /api/chat/image carry {message, detail} — join both so the
 *  inline error explains what to do, not just what failed. */
function imageErrorMessage(err: unknown): string {
  if (err instanceof ApiError && err.detail && typeof err.detail === "object") {
    const d = err.detail as { message?: unknown; detail?: unknown };
    if (typeof d.message === "string" && typeof d.detail === "string") {
      return `${d.message} — ${d.detail}`;
    }
  }
  return errorMessage(err);
}

/**
 * Live stage line while a generation has produced no thinking/answer tokens
 * yet. Queued shows the backend's detail verbatim (it names the busy lane);
 * once processing starts, the status detail ("prompt sent to <model>
 * (<engine>) — processing") gets a live elapsed-seconds ticker until the
 * first token arrives.
 */
function PendingStageLine({
  detail,
  queued,
}: {
  detail?: string;
  queued: boolean;
}) {
  const [elapsed, setElapsed] = useState(0);
  const startedAtRef = useRef<number | null>(null);

  useEffect(() => {
    if (queued) {
      // Still waiting for a slot — the processing clock hasn't started.
      startedAtRef.current = null;
      setElapsed(0);
      return;
    }
    startedAtRef.current ??= Date.now();
    const timer = window.setInterval(() => {
      const started = startedAtRef.current ?? Date.now();
      setElapsed(Math.round((Date.now() - started) / 1000));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [queued]);

  return (
    <div role="status" className="flex items-center gap-2 text-xs text-muted">
      <Spinner size={14} />
      <span className="min-w-0 break-words">
        {detail ??
          (queued
            ? "Queued — waiting for a free slot…"
            : "Contacting the model…")}
      </span>
      {!queued && elapsed > 0 && (
        <span className="shrink-0 text-faint">· {elapsed}s</span>
      )}
    </div>
  );
}

export function ChatView({
  conversationId,
}: {
  /** Null = a fresh, not-yet-created chat (/chats or /chats/new). */
  conversationId: string | null;
}) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { toast } = useToast();

  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [generatingImage, setGeneratingImage] = useState(false);
  const [tempMode, setTempMode] = useState(false);
  const [sendError, setSendError] = useState<SendError | null>(null);
  const [newChatSlug, setNewChatSlug] = useState("");
  const [thinking, setThinkingState] = useState<ThinkingLevel>(() =>
    loadStoredThinking(THINKING_STORAGE_KEY),
  );

  const abortRef = useRef<AbortController | null>(null);
  const composerRef = useRef<ComposerHandle>(null);
  const messagesRef = useRef<UiMessage[]>(messages);
  /** Which conversation the current `messages` state belongs to. */
  const loadedForRef = useRef<string | null>(null);
  /** Conversation we streamed into — its local state beats server snapshots
   *  (an aborted stream keeps partial text the server may have dropped). */
  const holdRef = useRef<string | null>(null);
  /** Last server snapshot applied, to avoid redundant reloads. */
  const lastLoadedDataRef = useRef<ConversationDetail | null>(null);
  /** Conversation we've already attempted a reattach for this visit — so the
   *  GET /stream probe fires once per open, not on every history refetch. */
  const reattachedForRef = useRef<string | null>(null);
  const lastSendRef = useRef<LastSend | null>(null);
  const keyRef = useRef(0);
  const bottomRef = useRef<HTMLDivElement>(null);

  messagesRef.current = messages;
  const nextKey = () => `local-${++keyRef.current}`;

  const setThinking = (level: ThinkingLevel) => {
    setThinkingState(level);
    storeThinking(THINKING_STORAGE_KEY, level);
  };

  // ── serving models ────────────────────────────────────────────────────────
  const status = useQuery({
    queryKey: ["chat-status"],
    queryFn: api.chatStatus,
    refetchInterval: 15_000,
  });
  const serving = status.data?.serving ?? [];
  const imageLease = status.data?.image ?? null;

  // ── downloaded models ──────────────────────────────────────────────────────
  // The backend loads the chosen model on demand, so EVERY downloaded text
  // model is selectable — not just the ones currently serving. `serving` only
  // marks which are already loaded (the "loaded" dot) plus the image lane.
  const models = useQuery({ queryKey: ["models"], queryFn: api.listModels });
  const downloadedModels = useMemo<ModelOption[]>(
    () =>
      (models.data ?? [])
        .filter((m) => m.status === "ready" && m.engine !== "imagegen")
        .map((m) => {
          const slug = opencodeModelId(m.display_name, m.id);
          return {
            slug,
            name: m.display_name,
            paramsB: m.params_b,
            engine: m.engine,
            loaded: serving.some((l) => l.model_slug === slug),
          };
        }),
    [models.data, serving],
  );
  const noModels = models.data != null && downloadedModels.length === 0;
  // "Auto": the tiny router model picks the answering model per prompt (the
  // stream narrates the routing via forge:"status" frames). Available whenever
  // ≥1 text model is downloaded — prefer the backend's signal, falling back to
  // the derived list on older backends that omit the `auto` block.
  const autoAvailable =
    status.data?.auto?.available ?? downloadedModels.length > 0;

  // ── sandbox lane: fetched once, cached; drives the code-block Run button ──
  const sandbox = useQuery({
    queryKey: ["sandbox-status"],
    queryFn: api.sandboxStatus,
    staleTime: Infinity,
    retry: false,
  });
  const sandboxRunner = useMemo<SandboxRunner | null>(
    () =>
      sandbox.data?.healthy
        ? { run: (language, code) => api.sandboxRun({ language, code }) }
        : null,
    [sandbox.data?.healthy],
  );

  // ── image providers: the local imagegen lane plus enabled remote
  //    connectors that advertise image generation (e.g. Higgsfield) ─────────
  const connectors = useQuery({
    queryKey: ["connectors"],
    queryFn: api.listConnectors,
  });
  const imageProviders = useMemo<ImageProvider[]>(() => {
    const providers: ImageProvider[] = [];
    if (imageLease) {
      providers.push({ id: "local", label: `Local · ${imageLease.model_name}` });
    }
    for (const c of connectors.data ?? []) {
      if (
        c.enabled &&
        c.mcp_type === "remote" &&
        /image/i.test(`${c.name} ${c.description}`)
      ) {
        providers.push({ id: c.kind, label: c.name });
      }
    }
    return providers;
  }, [imageLease, connectors.data]);

  // Default a fresh chat to "Auto" once models load (or "" when Auto is
  // unavailable), and drop a pick that names a model no longer downloaded.
  // "auto" is a virtual slug (no model row) that stays valid while the router
  // option is available.
  useEffect(() => {
    if (models.data == null) return;
    if (!newChatSlug) {
      if (autoAvailable) setNewChatSlug(AUTO_SLUG);
      return;
    }
    if (newChatSlug === AUTO_SLUG) {
      // A stale Auto pick (all models removed) falls back to no selection.
      if (!autoAvailable) setNewChatSlug("");
      return;
    }
    if (!downloadedModels.some((m) => m.slug === newChatSlug)) {
      setNewChatSlug(autoAvailable ? AUTO_SLUG : "");
    }
  }, [models.data, downloadedModels, newChatSlug, autoAvailable]);

  // ── conversation history ──────────────────────────────────────────────────
  const conversation = useQuery({
    queryKey: ["conversation", conversationId],
    queryFn: () => api.getConversation(conversationId as string),
    enabled: conversationId != null,
  });

  // Reset state when the surface switches conversations (but not right after
  // this surface created the conversation itself — loadedForRef already
  // points at it then, and the stream must keep running). Aborting here only
  // DETACHES the viewer from a server-side job; the generation keeps running
  // and is picked back up by the reattach path on return.
  useEffect(() => {
    if (conversationId === loadedForRef.current) return;
    abortRef.current?.abort();
    setSendError(null);
    setMessages([]);
    setStreaming(false);
    lastSendRef.current = null;
    holdRef.current = null;
    lastLoadedDataRef.current = null;
    loadedForRef.current = null;
    reattachedForRef.current = null;
    if (conversationId != null) setTempMode(false);
  }, [conversationId]);

  // Fill from the server once history arrives — never mid-stream, and never
  // over local state we streamed into ourselves this visit.
  useEffect(() => {
    const data = conversation.data;
    if (!data || streaming) return;
    if (holdRef.current === data.id) return;
    if (lastLoadedDataRef.current === data) return;
    lastLoadedDataRef.current = data;
    loadedForRef.current = data.id;
    setMessages(
      data.messages
        .filter((m) => m.role !== "system")
        .map((m) => {
          // Persisted assistant turns may carry literal <think> blocks —
          // extract them into the expander; markdown only gets the answer.
          const { thinking, answer } =
            m.role === "assistant"
              ? splitStoredThinking(m.content)
              : { thinking: "", answer: m.content };
          return {
            key: `srv-${m.id}`,
            role: m.role as "user" | "assistant",
            content: answer,
            thinking: thinking || undefined,
            attachments: m.attachments,
          };
        }),
    );
  }, [conversation.data, streaming]);

  // Abort any in-flight generation when leaving the surface entirely.
  useEffect(() => () => abortRef.current?.abort(), []);

  // Auto-scroll as content (answer or thinking) grows.
  const fingerprint = messages
    .map(
      (m) =>
        `${m.key}:${m.content.length}:${m.thinking?.length ?? 0}:${m.error ? 1 : 0}`,
    )
    .join("|");
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [fingerprint]);

  // ── temporary-chat leave guard ────────────────────────────────────────────
  const shouldBlock = tempMode && messages.length > 0;
  const blocker = useBlocker(shouldBlock);
  useEffect(() => {
    if (!shouldBlock) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [shouldBlock]);

  // ── streaming ─────────────────────────────────────────────────────────────

  const patchAssistant = (key: string, fn: (m: UiMessage) => UiMessage) => {
    setMessages((prev) => prev.map((m) => (m.key === key ? fn(m) : m)));
  };

  const finalizeAssistant = (
    key: string,
    error: string | null,
    dropIfEmpty = false,
  ) => {
    setMessages((prev) => {
      const target = prev.find((m) => m.key === key);
      if (!target) return prev;
      if (dropIfEmpty && !target.content && !error) {
        return prev.filter((m) => m.key !== key);
      }
      return prev.map((m) =>
        m.key === key ? { ...m, streaming: false, error } : m,
      );
    });
  };

  /** Re-attach to a possibly in-flight server-side generation for `id`.
   *  Replays buffered frames then streams live; a lone `idle` frame means
   *  nothing is running (the stored history already on screen is complete).
   *  When the history ends on a user turn a generation is almost certainly in
   *  flight, so the pending bubble (and the composer lock) appears the moment
   *  the stream opens — `expectInFlight` — instead of waiting for the first
   *  replayed frame; an idle frame tears it down again. Every other path
   *  creates the bubble on the first frame of any kind (queued/status/
   *  thinking/answer). Detaching (abort on navigation/stop) never errors —
   *  the job keeps running server-side. */
  const reattach = async (id: string, expectInFlight: boolean) => {
    const controller = new AbortController();
    abortRef.current = controller;

    let resp: Response;
    try {
      resp = await conversationReattachStream(id, controller.signal);
    } catch {
      return; // couldn't probe — the stored history is already shown
    }
    if (!resp.body) return;
    if (abortRef.current !== controller) {
      // A send/switch superseded us while connecting — don't double-consume.
      void resp.body.cancel().catch(() => undefined);
      return;
    }

    let assistantKey: string | null = null;
    let sawIdle = false;
    const ensureBubble = (): string => {
      if (assistantKey) return assistantKey;
      const key = nextKey();
      assistantKey = key;
      // Our live bubble beats server snapshots until the job persists, and
      // the composer lock engages so a second send stays blocked meanwhile.
      holdRef.current = id;
      setStreaming(true);
      setMessages((prev) => [
        ...prev,
        {
          key,
          role: "assistant",
          content: "",
          attachments: [],
          streaming: true,
        },
      ]);
      return key;
    };
    const dropBubble = () => {
      if (!assistantKey) return;
      const key = assistantKey;
      assistantKey = null;
      setMessages((prev) => prev.filter((m) => m.key !== key));
      setStreaming(false);
      holdRef.current = null;
    };

    if (expectInFlight) ensureBubble();

    let streamError: string | null = null;
    try {
      await readChatStream(resp.body, {
        onStatus: (state, detail) => {
          if (state === "idle") {
            // Nothing running — the stored history is complete. Tear down the
            // eagerly-created bubble, if any.
            sawIdle = true;
            dropBubble();
            return;
          }
          const key = ensureBubble();
          patchAssistant(key, (m) => ({
            ...m,
            queued: state === "queued",
            stageDetail: detail ?? m.stageDetail,
          }));
        },
        onThinking: (delta) => {
          const key = ensureBubble();
          patchAssistant(key, (m) => ({
            ...m,
            thinking: (m.thinking ?? "") + delta,
            queued: false,
          }));
        },
        onDelta: (fragment) => {
          const key = ensureBubble();
          patchAssistant(key, (m) => ({
            ...m,
            content: m.content + fragment,
            queued: false,
          }));
        },
        onError: (message) => {
          streamError = message;
        },
      });
    } catch (err) {
      // AbortError = the viewer detached (navigation or stop); the server job
      // is untouched. Keep whatever streamed; do NOT surface an error. Only
      // touch shared state if a newer send/reattach hasn't superseded us.
      if (abortRef.current === controller) {
        if (assistantKey) finalizeAssistant(assistantKey, null, true);
        setStreaming(false);
        holdRef.current = null;
        if (!(err instanceof DOMException && err.name === "AbortError")) {
          // A genuine mid-reattach network glitch — the history is still valid.
          void queryClient.invalidateQueries({ queryKey: ["conversation", id] });
        }
      }
      return;
    }

    if (abortRef.current !== controller) return; // superseded — leave it be
    if (sawIdle || !assistantKey) return; // idle — stored history is complete
    finalizeAssistant(assistantKey, streamError, streamError == null);
    setStreaming(false);
    holdRef.current = null;
    // The job persisted its reply — refetch so the stored message replaces the
    // live bubble and the auto-title/updated_at land.
    void queryClient.invalidateQueries({ queryKey: ["conversation", id] });
    void queryClient.invalidateQueries({ queryKey: ["conversations"] });
  };

  // On open (mount or switched-to), once history has loaded, probe the stream
  // to reattach to any in-flight generation. Runs once per visit — switching
  // away resets reattachedForRef, so returning (A→B→A, repeatedly) re-probes
  // every time; skipped while we're already streaming our own send into this
  // conversation.
  useEffect(() => {
    const id = conversationId;
    if (id == null || streaming) return;
    if (reattachedForRef.current === id) return;
    const data = conversation.data;
    if (!data || data.id !== id) return;
    reattachedForRef.current = id;
    // History ending on a user turn = a reply is (very likely) generating —
    // show the pending state immediately rather than on the first frame.
    const turns = data.messages.filter((m) => m.role !== "system");
    const lastIsUser = turns[turns.length - 1]?.role === "user";
    void reattach(id, lastIsUser);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId, streaming, conversation.data]);

  const run = async (
    content: string,
    metas: AttachmentMeta[],
    reuseUserBubble: boolean,
  ) => {
    setSendError(null);
    setStreaming(true);
    const controller = new AbortController();
    abortRef.current = controller;

    // History for temporary mode — captured before appending the new turn.
    const historyBase = messagesRef.current
      .filter((m) => m.content)
      .map((m) => ({ role: m.role, content: m.content }));

    const assistantKey = nextKey();
    const userMsg: UiMessage = {
      key: nextKey(),
      role: "user",
      content,
      attachments: metas,
    };
    const assistantMsg: UiMessage = {
      key: assistantKey,
      role: "assistant",
      content: "",
      attachments: [],
      streaming: true,
    };
    setMessages((prev) =>
      reuseUserBubble ? [...prev, assistantMsg] : [...prev, userMsg, assistantMsg],
    );

    try {
      let resp: Response;
      if (tempMode) {
        resp = await temporaryChatStream(
          {
            messages: [...historyBase, { role: "user", content }],
            model_slug: newChatSlug,
            thinking,
            attachment_ids: metas.map((m) => m.id),
          },
          controller.signal,
        );
      } else {
        let id = conversationId ?? loadedForRef.current;
        if (!id) {
          const conv = await api.createConversation({
            model_slug: newChatSlug,
            thinking,
          });
          id = conv.id;
          loadedForRef.current = id;
          navigate(`/chats/${id}`, { replace: true });
          void queryClient.invalidateQueries({ queryKey: ["conversations"] });
        }
        holdRef.current = id;
        // We own this conversation's stream now — don't let the reattach
        // effect fire a redundant probe when this send finishes or is stopped.
        reattachedForRef.current = id;
        resp = await conversationMessageStream(
          id,
          { content, attachment_ids: metas.map((m) => m.id), thinking },
          controller.signal,
        );
      }

      if (!resp.body) {
        throw new ApiError(0, "Streaming is not supported by this browser");
      }
      let streamError: string | null = null;
      await readChatStream(resp.body, {
        onStatus: (state, detail) =>
          // A busy lane queues the job first (detail names the lane); the
          // always-sent status frame then carries the "prompt sent …" line.
          patchAssistant(assistantKey, (m) => ({
            ...m,
            queued: state === "queued",
            stageDetail: detail ?? m.stageDetail,
          })),
        onThinking: (delta) =>
          patchAssistant(assistantKey, (m) => ({
            ...m,
            thinking: (m.thinking ?? "") + delta,
            queued: false,
          })),
        onDelta: (fragment) =>
          patchAssistant(assistantKey, (m) => ({
            ...m,
            content: m.content + fragment,
            queued: false,
          })),
        onError: (message) => {
          streamError = message;
        },
      });
      finalizeAssistant(assistantKey, streamError, streamError == null);
      if (!tempMode) {
        // Pick up updated_at now and the auto-generated title a beat later.
        void queryClient.invalidateQueries({ queryKey: ["conversations"] });
        window.setTimeout(
          () =>
            void queryClient.invalidateQueries({ queryKey: ["conversations"] }),
          5000,
        );
      }
    } catch (err) {
      finalizeAssistant(assistantKey, null, true);
      if (err instanceof DOMException && err.name === "AbortError") {
        // User pressed stop — keep whatever partial reply streamed in.
      } else if (err instanceof ApiError && err.status === 409) {
        setSendError({ kind: "no-model", message: errorMessage(err) });
      } else {
        setSendError({ kind: "other", message: errorMessage(err) });
      }
    } finally {
      // Only clear shared state if a newer send/reattach hasn't taken over
      // (switching to an already-cached conversation can start one before this
      // aborted turn's cleanup runs).
      if (abortRef.current === controller) {
        abortRef.current = null;
        setStreaming(false);
      }
    }
  };

  /** Generate an image and record the exchange (unless in temporary mode).
   *  Generation can take minutes — a pending bubble holds the spot. */
  const runImage = async (
    prompt: string,
    provider: string,
    reuseUserBubble: boolean,
  ) => {
    setSendError(null);
    setGeneratingImage(true);

    const assistantKey = nextKey();
    const userMsg: UiMessage = {
      key: nextKey(),
      role: "user",
      content: prompt,
      attachments: [],
    };
    const assistantMsg: UiMessage = {
      key: assistantKey,
      role: "assistant",
      content: "",
      attachments: [],
      pendingImage: { prompt },
    };
    setMessages((prev) =>
      reuseUserBubble ? [...prev, assistantMsg] : [...prev, userMsg, assistantMsg],
    );

    try {
      let id: string | null = null;
      if (!tempMode) {
        id = conversationId ?? loadedForRef.current;
        if (!id) {
          const conv = await api.createConversation({
            model_slug: newChatSlug,
            thinking,
          });
          id = conv.id;
          loadedForRef.current = id;
          navigate(`/chats/${id}`, { replace: true });
          void queryClient.invalidateQueries({ queryKey: ["conversations"] });
        }
        holdRef.current = id;
      }

      const result = await api.generateImage({
        prompt,
        conversation_id: id,
        provider,
        size: IMAGE_SIZE,
        // Temporary mode stores nothing server-side — the image arrives
        // inline as a data URI and lives only in this tab.
        temporary: tempMode,
      });

      // Show the image right away; the placeholder content mirrors what the
      // backend records, so temporary-chat history stays coherent too.
      setMessages((prev) =>
        prev.map((m) =>
          m.key === assistantKey
            ? {
                ...m,
                pendingImage: undefined,
                content: `[Generated image: ${prompt}]`,
                attachments: result.upload ? [result.upload] : [],
                tempImage: result.image_data_uri
                  ? { dataUri: result.image_data_uri, prompt }
                  : undefined,
              }
            : m,
        ),
      );
      if (!tempMode && id) {
        // Refetch so the cached conversation carries the recorded exchange
        // (local state stays authoritative for this visit, as with streams).
        void queryClient.invalidateQueries({ queryKey: ["conversation", id] });
        void queryClient.invalidateQueries({ queryKey: ["conversations"] });
      }
    } catch (err) {
      const message = imageErrorMessage(err);
      setMessages((prev) =>
        prev.map((m) =>
          m.key === assistantKey
            ? { ...m, pendingImage: undefined, error: message }
            : m,
        ),
      );
    } finally {
      setGeneratingImage(false);
    }
  };

  const onSend = (content: string, uploads: UploadMeta[]) => {
    if (streaming || generatingImage) return;
    const metas: AttachmentMeta[] = uploads.map((u) => ({
      id: u.id,
      filename: u.filename,
      kind: u.kind,
      mime: u.mime,
      size_bytes: u.size_bytes,
    }));
    lastSendRef.current = { type: "text", content, metas };
    void run(content, metas, false);
  };

  const onGenerateImage = (prompt: string, provider: string) => {
    if (streaming || generatingImage) return;
    lastSendRef.current = { type: "image", prompt, provider };
    void runImage(prompt, provider, false);
  };

  /** Re-send the last user turn after a failure (its bubble stays). */
  const retry = () => {
    const last = lastSendRef.current;
    if (!last || streaming || generatingImage) return;
    setMessages((prev) => {
      const tail = prev[prev.length - 1];
      return tail?.role === "assistant" && (tail.error || !tail.content)
        ? prev.slice(0, -1)
        : prev;
    });
    if (last.type === "image") void runImage(last.prompt, last.provider, true);
    else void run(last.content, last.metas, true);
  };

  /** Stop generating: abort the local stream reader for instant feedback AND
   *  cancel the server-side job (fire-and-forget — 409 just means nothing was
   *  generating anymore). The partial text stays as the assistant turn; the
   *  job's stream delivers its final done frame after the cancel. Temporary
   *  chats have no server-side job — the abort alone stops them. */
  const stop = () => {
    abortRef.current?.abort();
    const id = conversationId ?? loadedForRef.current;
    if (!tempMode && id) {
      void api.cancelGeneration(id).catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 409) return;
        toast("error", `Couldn't cancel server-side: ${errorMessage(err)}`);
      });
    }
  };

  // ── model picking ─────────────────────────────────────────────────────────
  // Empty slug means Auto everywhere (a saved chat may store "" or "auto").
  const conversationSlug = conversation.data?.model_slug ?? "";
  const activeSlug = conversationId ? conversationSlug : newChatSlug;
  const isAuto = activeSlug === AUTO_SLUG || activeSlug === "";
  const activeModel = isAuto
    ? null
    : downloadedModels.find((m) => m.slug === activeSlug) ?? null;

  const pickModel = useMutation({
    mutationFn: (slug: string) =>
      api.patchConversation(conversationId as string, { model_slug: slug }),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["conversation", conversationId],
      });
      void queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
    onError: (err) => toast("error", errorMessage(err)),
  });

  const onPickModel = (slug: string) => {
    if (conversationId) pickModel.mutate(slug);
    else setNewChatSlug(slug);
  };

  // ── render ────────────────────────────────────────────────────────────────
  const title = conversationId
    ? conversation.data?.title ?? "…"
    : tempMode
      ? "Temporary chat"
      : "New chat";
  const canToggleTemp =
    conversationId == null && messages.length === 0 && !streaming;
  const lastMessage = messages[messages.length - 1];
  // Pending stage line: only until the first thinking/answer token — the
  // Thinking expander (then the streamed text) takes over from there.
  const waitingForFirstToken =
    streaming &&
    lastMessage?.role === "assistant" &&
    !lastMessage.content &&
    !lastMessage.thinking;

  return (
    <SandboxContext.Provider value={sandboxRunner}>
    <div className="flex min-h-dvh flex-col px-4 md:px-6">
      {/* Header */}
      <header className="sticky top-0 z-10 -mx-4 border-b border-border bg-bg/95 px-4 pt-safe backdrop-blur md:-mx-6 md:px-6">
        <div className="flex flex-wrap items-center gap-2 py-3">
          <Link
            to="/chats"
            aria-label="Back to chats"
            className="-ml-2 flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-muted hover:bg-raised hover:text-text md:hidden"
          >
            <IconChevronLeft size={20} />
          </Link>
          <div className="min-w-0 flex-1">
            <h1 className="truncate text-base font-bold text-text">{title}</h1>
            <div className="flex items-center gap-2 text-xs text-muted">
              {isAuto ? (
                <span className="truncate">{AUTO_LABEL}</span>
              ) : activeModel ? (
                <>
                  <span className="truncate">{activeModel.name}</span>
                  <LaneBadge engine={activeModel.engine} />
                  {!activeModel.loaded && (
                    <span className="hidden shrink-0 text-faint sm:inline">
                      loads on demand
                    </span>
                  )}
                </>
              ) : noModels ? (
                <span>No models yet</span>
              ) : (
                <span>{conversationId ? "Saved chat" : "Pick a model"}</span>
              )}
              {imageLease && (
                <span
                  className="inline-flex shrink-0 items-center gap-1 text-faint"
                  title={`Local image generation available — ${imageLease.model_name}`}
                >
                  <IconImage size={12} />
                  <span className="hidden sm:inline">image</span>
                </span>
              )}
            </div>
          </div>
          {downloadedModels.length > 0 && (
            <ModelPicker
              value={activeSlug}
              options={downloadedModels}
              autoAvailable={autoAvailable}
              disabled={streaming || generatingImage || pickModel.isPending}
              onChange={onPickModel}
            />
          )}
          {conversationId == null && (
            <button
              type="button"
              role="switch"
              aria-checked={tempMode}
              aria-label="Temporary chat — not saved, no memory"
              title={
                tempMode
                  ? "Temporary chat on — nothing is saved"
                  : "Start a temporary chat (not saved)"
              }
              disabled={!canToggleTemp}
              onClick={() => setTempMode((t) => !t)}
              className={cx(
                "flex h-11 w-11 shrink-0 cursor-pointer items-center justify-center rounded-lg transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-50",
                tempMode
                  ? "bg-accent/15 text-accent"
                  : "text-muted hover:bg-raised hover:text-text",
              )}
            >
              <IconGhost size={20} />
            </button>
          )}
        </div>
      </header>

      {/* Temporary banner */}
      {tempMode && (
        <p
          role="status"
          className="mt-3 flex items-center justify-center gap-2 rounded-md border border-border bg-raised/60 px-3 py-2 text-center text-xs font-medium text-muted"
        >
          <IconGhost size={14} className="shrink-0" />
          Temporary chat — not saved, no memory
        </p>
      )}

      {/* Message list */}
      <div className="flex-1 space-y-4 pt-4 pb-4">
        {conversationId != null && conversation.isLoading && (
          <div className="flex justify-center py-10" aria-busy="true" aria-label="Loading conversation">
            <Spinner size={20} />
          </div>
        )}
        {conversationId != null && conversation.isError && (
          <EmptyState
            icon="search"
            title="Couldn't load this chat"
            hint={errorMessage(conversation.error)}
            action={
              <Button onClick={() => void conversation.refetch()}>Retry</Button>
            }
          />
        )}

        {/* Empty state: starter prompts for a fresh chat. The !streaming
            guard keeps it from flashing mid-first-generation or while a
            reattach is restoring an in-flight reply. */}
        {messages.length === 0 &&
          !sendError &&
          !streaming &&
          !conversation.isLoading &&
          !conversation.isError &&
          (noModels ? (
            <EmptyState
              icon="box"
              title="No models yet"
              hint="Download a model to start chatting."
              action={
                <Link
                  to="/models"
                  className="text-sm font-medium text-accent underline-offset-2 hover:underline"
                >
                  Go to Models
                </Link>
              }
            />
          ) : tempMode ? (
            <EmptyState
              icon="spark"
              title="Off the record"
              hint="Nothing here is stored and memory stays untouched. Close the page and it's gone."
            />
          ) : (
            <StarterPanel
              onPick={(prompt) => composerRef.current?.prefill(prompt)}
              showTemporaryTip={conversationId == null}
              autoAvailable={autoAvailable}
            />
          ))}

        {messages.map((m, i) => {
          if (
            m.role === "assistant" &&
            !m.content &&
            !m.thinking &&
            !m.error &&
            !m.pendingImage &&
            m.attachments.length === 0
          ) {
            return null;
          }
          return (
            <MessageBubble
              key={m.key}
              message={m}
              onRetry={i === messages.length - 1 ? retry : undefined}
            />
          );
        })}

        {sendError && (
          <div
            role="alert"
            className="rounded-xl border border-danger/30 bg-danger/10 p-3.5"
          >
            <p className="text-sm font-medium text-danger">
              {sendError.kind === "no-model"
                ? "No model is serving this chat."
                : sendError.message}
            </p>
            <div className="mt-2 flex items-center gap-3">
              {sendError.kind === "no-model" && (
                <Link
                  to="/models"
                  className="text-sm text-info underline underline-offset-2"
                >
                  Go to Models
                </Link>
              )}
              <Button size="sm" onClick={retry} disabled={!lastSendRef.current}>
                Retry
              </Button>
            </div>
          </div>
        )}

        {waitingForFirstToken && lastMessage && (
          <PendingStageLine
            key={lastMessage.key}
            detail={lastMessage.stageDetail}
            queued={lastMessage.queued === true}
          />
        )}
        <div ref={bottomRef} />
      </div>

      {/* Composer */}
      <div className="sticky bottom-0 -mx-4 bg-bg px-4 md:-mx-6 md:px-6">
        {noModels && (
          <p
            role="status"
            className="mb-0 flex items-center justify-center gap-2 rounded-t-md border border-b-0 border-border bg-raised/60 px-3 py-2 text-center text-xs text-muted"
          >
            <IconCube size={14} className="shrink-0" />
            No models yet —{" "}
            <Link to="/models" className="text-info underline underline-offset-2">
              download one on Models
            </Link>
          </p>
        )}
        <div className="pb-safe">
          <Composer
            ref={composerRef}
            onSend={onSend}
            onStop={stop}
            onGenerateImage={onGenerateImage}
            imageProviders={imageProviders}
            streaming={streaming}
            generatingImage={generatingImage}
            disabled={noModels}
            thinking={thinking}
            onThinking={setThinking}
            placeholder={
              tempMode
                ? "Message (temporary — not saved)…"
                : isAuto
                  ? "Message — Auto picks the model…"
                  : activeModel
                    ? `Message ${activeModel.name}…`
                    : "Message…"
            }
          />
        </div>
      </div>

      {/* Leaving a temporary chat discards it — confirm first. */}
      {blocker.state === "blocked" && (
        <div className="fixed inset-0 z-40 flex items-end justify-center md:items-center">
          <div className="absolute inset-0 bg-black/60 backdrop-blur-[2px]" />
          <div
            role="alertdialog"
            aria-modal="true"
            aria-label="Discard temporary chat?"
            className="relative z-10 w-full animate-rise rounded-t-2xl border border-border bg-surface p-5 pb-safe shadow-2xl shadow-black/50 md:max-w-lg md:rounded-2xl md:pb-5"
          >
            <h2 className="mb-2 text-base font-semibold text-text">
              Discard temporary chat?
            </h2>
            <p className="mb-5 text-sm text-muted">
              Temporary chats aren't saved anywhere — leaving this page
              discards the conversation for good.
            </p>
            <div className="flex gap-3">
              <Button className="flex-1" onClick={() => blocker.reset()}>
                Stay
              </Button>
              <Button
                className="flex-1"
                variant="danger"
                onClick={() => {
                  // Discard for real: kill the stream and wipe the state so a
                  // return to /chats/new starts clean.
                  abortRef.current?.abort();
                  setMessages([]);
                  setTempMode(false);
                  setSendError(null);
                  lastSendRef.current = null;
                  blocker.proceed();
                }}
              >
                Discard
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
    </SandboxContext.Provider>
  );
}
