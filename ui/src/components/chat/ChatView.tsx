// The conversation surface: history, token-by-token streaming over SSE
// (fetch + ReadableStream — EventSource cannot POST), abort keeping partial
// text, inline error frames with retry, and a temporary (incognito) mode
// that keeps everything in component state and stores nothing.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useBlocker, useNavigate } from "react-router-dom";
import {
  api,
  ApiError,
  conversationMessageStream,
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
import { cx } from "../../lib/utils";
import { IconChevronLeft, IconCube, IconGhost, IconImage } from "../icons";
import {
  loadStoredThinking,
  storeThinking,
} from "../ThinkingSelect";
import { Button, EmptyState, LaneBadge, Spinner } from "../ui";
import { Composer, type ImageProvider } from "./Composer";
import { MessageBubble, type UiMessage } from "./messages";
import { SandboxContext, type SandboxRunner } from "./sandbox-context";

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
  const messagesRef = useRef<UiMessage[]>(messages);
  /** Which conversation the current `messages` state belongs to. */
  const loadedForRef = useRef<string | null>(null);
  /** Conversation we streamed into — its local state beats server snapshots
   *  (an aborted stream keeps partial text the server may have dropped). */
  const holdRef = useRef<string | null>(null);
  /** Last server snapshot applied, to avoid redundant reloads. */
  const lastLoadedDataRef = useRef<ConversationDetail | null>(null);
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
  const nothingServing = status.data != null && serving.length === 0;
  const imageLease = status.data?.image ?? null;

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

  // A slug-less request only resolves when exactly one model serves, so with
  // several serving a fresh chat needs an explicit pick — default to the
  // first, and drop a pick whose lease has gone away.
  useEffect(() => {
    if (serving.length > 1 && !newChatSlug) {
      setNewChatSlug(serving[0].model_slug);
    } else if (
      newChatSlug &&
      status.data != null &&
      !serving.some((l) => l.model_slug === newChatSlug)
    ) {
      setNewChatSlug(serving.length > 1 ? serving[0].model_slug : "");
    }
  }, [serving, newChatSlug, status.data]);

  // ── conversation history ──────────────────────────────────────────────────
  const conversation = useQuery({
    queryKey: ["conversation", conversationId],
    queryFn: () => api.getConversation(conversationId as string),
    enabled: conversationId != null,
  });

  // Reset state when the surface switches conversations (but not right after
  // this surface created the conversation itself — loadedForRef already
  // points at it then, and the stream must keep running).
  useEffect(() => {
    if (conversationId === loadedForRef.current) return;
    abortRef.current?.abort();
    setSendError(null);
    setMessages([]);
    lastSendRef.current = null;
    holdRef.current = null;
    lastLoadedDataRef.current = null;
    loadedForRef.current = null;
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
        .map((m) => ({
          key: `srv-${m.id}`,
          role: m.role as "user" | "assistant",
          content: m.content,
          attachments: m.attachments,
        })),
    );
  }, [conversation.data, streaming]);

  // Abort any in-flight generation when leaving the surface entirely.
  useEffect(() => () => abortRef.current?.abort(), []);

  // Auto-scroll as content grows.
  const fingerprint = messages
    .map((m) => `${m.key}:${m.content.length}:${m.error ? 1 : 0}`)
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
        onDelta: (fragment) =>
          patchAssistant(assistantKey, (m) => ({
            ...m,
            content: m.content + fragment,
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
      abortRef.current = null;
      setStreaming(false);
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

  const stop = () => abortRef.current?.abort();

  // ── model picking ─────────────────────────────────────────────────────────
  const conversationSlug = conversation.data?.model_slug ?? "";
  const activeSlug = conversationId ? conversationSlug : newChatSlug;
  const singleLease = serving.length === 1 ? serving[0] : null;
  const activeLease =
    serving.find((l) => l.model_slug === activeSlug) ?? singleLease;

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
  const waitingForFirstToken =
    streaming && lastMessage?.role === "assistant" && !lastMessage.content;

  return (
    <SandboxContext.Provider value={sandboxRunner}>
    <div className="flex min-h-dvh flex-col px-4 md:px-6">
      {/* Header */}
      <header className="sticky top-0 z-10 -mx-4 border-b border-border bg-bg/95 px-4 pt-safe backdrop-blur md:-mx-6 md:px-6">
        <div className="flex items-center gap-2 py-3">
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
              {activeLease ? (
                <>
                  <span className="truncate">{activeLease.model_name}</span>
                  <LaneBadge engine={activeLease.engine} />
                </>
              ) : nothingServing ? (
                <span>No model loaded</span>
              ) : (
                <span>{conversationId ? "Saved chat" : "Pick a model below"}</span>
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

      {/* Model picker — several models serving at once */}
      {serving.length > 1 && (
        <div
          role="radiogroup"
          aria-label="Model for this chat"
          className="mt-3 flex flex-wrap items-center gap-1.5"
        >
          <span className="mr-1 text-xs font-medium text-faint">Model</span>
          {serving.map((l) => {
            const active = activeSlug === l.model_slug;
            return (
              <button
                key={l.model_slug}
                type="button"
                role="radio"
                aria-checked={active}
                disabled={streaming || pickModel.isPending}
                onClick={() => onPickModel(l.model_slug)}
                className={cx(
                  "inline-flex min-h-9 cursor-pointer items-center gap-1.5 rounded-full border px-3 font-mono text-xs font-medium transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-60",
                  active
                    ? "border-accent/50 bg-accent/15 text-accent"
                    : "border-border bg-raised text-muted hover:text-text",
                )}
              >
                <span
                  aria-hidden
                  className={cx(
                    "h-1.5 w-1.5 rounded-full",
                    active ? "bg-accent" : "bg-ok",
                  )}
                />
                {l.model_slug}
              </button>
            );
          })}
        </div>
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

        {messages.length === 0 &&
          !sendError &&
          !conversation.isLoading &&
          !conversation.isError &&
          (nothingServing ? (
            <EmptyState
              icon="box"
              title="No model loaded"
              hint="A model has to be serving before anyone can chat."
              action={
                <Link
                  to="/models"
                  className="text-sm font-medium text-accent underline-offset-2 hover:underline"
                >
                  Go to Models
                </Link>
              }
            />
          ) : (
            <EmptyState
              icon="spark"
              title={tempMode ? "Off the record" : "Start the conversation"}
              hint={
                tempMode
                  ? "Nothing here is stored and memory stays untouched. Close the page and it's gone."
                  : "Chat with your local model. Conversations are saved to your profile and can teach its memory."
              }
            />
          ))}

        {messages.map((m, i) => {
          if (
            m.role === "assistant" &&
            !m.content &&
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

        {waitingForFirstToken && (
          <div className="flex items-center gap-2 text-xs text-muted">
            <Spinner size={14} />
            Generating…
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Composer */}
      <div className="sticky bottom-0 -mx-4 bg-bg px-4 md:-mx-6 md:px-6">
        {nothingServing && (
          <p
            role="status"
            className="mb-0 flex items-center justify-center gap-2 rounded-t-md border border-b-0 border-border bg-raised/60 px-3 py-2 text-center text-xs text-muted"
          >
            <IconCube size={14} className="shrink-0" />
            No model loaded —{" "}
            <Link to="/models" className="text-info underline underline-offset-2">
              load one on Models
            </Link>
          </p>
        )}
        <div className="pb-safe">
          <Composer
            onSend={onSend}
            onStop={stop}
            onGenerateImage={onGenerateImage}
            imageProviders={imageProviders}
            streaming={streaming}
            generatingImage={generatingImage}
            disabled={nothingServing}
            thinking={thinking}
            onThinking={setThinking}
            placeholder={
              tempMode
                ? "Message (temporary — not saved)…"
                : activeLease
                  ? `Message ${activeLease.model_name}…`
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
