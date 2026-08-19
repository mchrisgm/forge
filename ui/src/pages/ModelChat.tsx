// Scratch chat straight to the loaded inference engine via
// POST /api/engines/chat (OpenAI chat-completions, SSE streaming).
// This is the only surface for AirLLM-lane models (PLAN §6.2): they are
// blocked from sessions, so the chat page must exist for them to be usable.
// Conversation state is in-memory only — nothing is persisted.

import { useQuery } from "@tanstack/react-query";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import { Link } from "react-router-dom";
import {
  api,
  ApiError,
  engineChatStream,
  errorMessage,
  type ChatCompletionMessage,
} from "../api/client";
import {
  IconAlert,
  IconChevronLeft,
  IconSend,
  IconStop,
  IconTrash,
} from "../components/icons";
import { Markdown } from "../components/lazy-markdown";
import { Button, EmptyState, LaneBadge, Spinner } from "../components/ui";
import { cx } from "../lib/utils";

interface ChatError {
  kind: "no-engine" | "other";
  message: string;
}

/**
 * Consume an OpenAI-style SSE body: `data: {...chunk...}` frames ending with
 * `data: [DONE]`. Calls onDelta for every non-empty content fragment.
 */
async function readSseStream(
  body: ReadableStream<Uint8Array>,
  onDelta: (fragment: string) => void,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const handleLine = (line: string): boolean => {
    const trimmed = line.trim();
    if (!trimmed.startsWith("data:")) return false;
    const data = trimmed.slice(5).trim();
    if (data === "[DONE]") return true;
    try {
      const chunk = JSON.parse(data) as {
        choices?: { delta?: { content?: unknown } }[];
      };
      const fragment = chunk.choices?.[0]?.delta?.content;
      if (typeof fragment === "string" && fragment) onDelta(fragment);
    } catch {
      // partial/non-JSON frame — skip it, never crash the chat
    }
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

export default function ModelChat() {
  const [messages, setMessages] = useState<ChatCompletionMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<ChatError | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const engines = useQuery({
    queryKey: ["engines"],
    queryFn: api.enginesStatus,
    refetchInterval: 15000,
  });
  const lease = engines.data?.lease ?? null;
  const leaseReady = lease?.state === "ready";

  // Abort any in-flight generation when leaving the page.
  useEffect(() => () => abortRef.current?.abort(), []);

  // Auto-scroll as content grows.
  const fingerprint = messages
    .map((m) => `${m.role}:${m.content.length}`)
    .join("|");
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [fingerprint]);

  const appendToLast = (fragment: string) => {
    setMessages((prev) => {
      const last = prev[prev.length - 1];
      if (!last || last.role !== "assistant") return prev;
      return [
        ...prev.slice(0, -1),
        { ...last, content: last.content + fragment },
      ];
    });
  };

  /** Drop a trailing assistant bubble that never received any content. */
  const dropEmptyReply = () => {
    setMessages((prev) => {
      const last = prev[prev.length - 1];
      return last && last.role === "assistant" && !last.content
        ? prev.slice(0, -1)
        : prev;
    });
  };

  const doSend = useCallback(() => {
    const text = draft.trim();
    if (!text || streaming) return;
    setDraft("");
    if (textareaRef.current) textareaRef.current.style.height = "auto";
    setError(null);

    const history: ChatCompletionMessage[] = [
      ...messages,
      { role: "user", content: text },
    ];
    setMessages([...history, { role: "assistant", content: "" }]);
    setStreaming(true);
    const controller = new AbortController();
    abortRef.current = controller;

    void (async () => {
      try {
        const resp = await engineChatStream(history, controller.signal);
        const contentType = resp.headers.get("content-type") ?? "";
        if (contentType.includes("application/json")) {
          // Engine ignored stream:true — take the full completion at once.
          const payload = (await resp.json()) as {
            choices?: { message?: { content?: unknown } }[];
          };
          const content = payload.choices?.[0]?.message?.content;
          if (typeof content === "string") appendToLast(content);
        } else {
          if (!resp.body) {
            throw new ApiError(0, "Streaming is not supported by this browser");
          }
          await readSseStream(resp.body, appendToLast);
        }
        dropEmptyReply();
      } catch (err) {
        dropEmptyReply();
        if (err instanceof DOMException && err.name === "AbortError") {
          // User pressed stop — keep whatever partial reply streamed in.
        } else if (err instanceof ApiError && err.status === 409) {
          setError({ kind: "no-engine", message: errorMessage(err) });
        } else {
          setError({ kind: "other", message: errorMessage(err) });
        }
      } finally {
        abortRef.current = null;
        setStreaming(false);
      }
    })();
  }, [draft, streaming, messages]);

  const stop = () => abortRef.current?.abort();

  const clear = () => {
    abortRef.current?.abort();
    setMessages([]);
    setError(null);
  };

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


  return (
    <div className="flex min-h-dvh flex-col">
      {/* Header */}
      <header className="sticky top-0 z-10 -mx-4 border-b border-border bg-bg/95 px-4 pt-safe backdrop-blur md:mx-0 md:border-none md:bg-transparent md:backdrop-blur-none">
        <div className="flex items-center gap-2 py-3">
          <Link
            to="/models"
            aria-label="Back to models"
            className="-ml-2 flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-muted hover:bg-raised hover:text-text"
          >
            <IconChevronLeft size={20} />
          </Link>
          <div className="min-w-0 flex-1">
            <h1 className="truncate text-base font-bold text-text">
              Model chat
            </h1>
            <div className="flex items-center gap-2 text-xs text-muted">
              {lease ? (
                <>
                  <span className="truncate">{lease.model_name}</span>
                  <LaneBadge engine={lease.engine} />
                </>
              ) : (
                <span>No model loaded</span>
              )}
            </div>
          </div>
          <Button
            size="sm"
            variant="ghost"
            disabled={messages.length === 0}
            onClick={clear}
          >
            <IconTrash size={15} />
            Clear
          </Button>
        </div>
      </header>

      {/* Lane / lease warnings */}
      {lease?.engine === "airllm" && (
        <p
          role="status"
          className="mt-3 flex items-center justify-center gap-2 rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-center text-xs font-medium text-warn"
        >
          <IconAlert size={14} className="shrink-0" />
          Slow lane — replies can take minutes to hours
        </p>
      )}
      {engines.data && !leaseReady && (
        <p
          role="status"
          className="mt-3 rounded-md border border-border bg-raised/60 px-3 py-2 text-center text-xs text-muted"
        >
          {lease?.state === "starting"
            ? "The engine is still loading — messages will fail until it is ready."
            : "No engine is serving right now."}{" "}
          <Link
            to="/models"
            className="text-info underline underline-offset-2"
          >
            Manage models
          </Link>
        </p>
      )}

      {/* Message list */}
      <div className="flex-1 space-y-4 pt-4 pb-4">
        {messages.length === 0 && !error && (
          <EmptyState
            icon="spark"
            title="Talk to the loaded model"
            hint="A scratch chat straight to the inference engine — nothing here is saved, and clearing wipes it for good."
          />
        )}

        {messages.map((m, i) => {
          const isUser = m.role === "user";
          if (!isUser && !m.content) return null;
          return (
            <div
              key={i}
              className={cx("flex", isUser ? "justify-end" : "justify-start")}
            >
              <div
                className={cx(
                  "max-w-[92%] md:max-w-[85%]",
                  isUser
                    ? "rounded-2xl rounded-br-md bg-accent/15 px-3.5 py-2.5 text-text"
                    : "w-full",
                )}
              >
                {isUser ? (
                  <p className="text-sm break-words whitespace-pre-wrap">
                    {m.content}
                  </p>
                ) : (
                  <Markdown text={m.content} />
                )}
              </div>
            </div>
          );
        })}

        {error && (
          <div
            role="alert"
            className="rounded-xl border border-danger/30 bg-danger/10 p-3.5"
          >
            <p className="text-sm font-medium text-danger">
              {error.kind === "no-engine"
                ? "No engine loaded — load a model first."
                : error.message}
            </p>
            {error.kind === "no-engine" && (
              <Link
                to="/models"
                className="mt-1 inline-block text-sm text-info underline underline-offset-2"
              >
                Go to Models
              </Link>
            )}
          </div>
        )}

        {streaming && (
          <div className="flex items-center gap-2 text-xs text-muted">
            <Spinner size={14} />
            {lease?.engine === "airllm"
              ? "Generating — the slow lane can take a very long time…"
              : "Generating…"}
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Composer */}
      <div className="sticky bottom-0 -mx-4 border-t border-border bg-bg/95 px-4 pt-3 pb-safe backdrop-blur md:mx-0 md:rounded-t-xl">
        <div className="flex items-end gap-2 pb-3">
          <label htmlFor="model-chat-composer" className="sr-only">
            Message the model
          </label>
          <textarea
            id="model-chat-composer"
            ref={textareaRef}
            rows={1}
            value={draft}
            placeholder={
              lease ? `Message ${lease.model_name}…` : "Message the model…"
            }
            onChange={(e) => {
              setDraft(e.target.value);
              autoGrow();
            }}
            onKeyDown={onComposerKeyDown}
            className="max-h-40 min-h-11 flex-1 resize-none rounded-xl border border-edge bg-raised px-3.5 py-2.5 text-sm text-text placeholder:text-faint focus:border-accent focus:outline-none"
          />
          {streaming ? (
            <Button
              variant="danger"
              aria-label="Stop generating"
              onClick={stop}
              className="h-11 w-11 shrink-0 rounded-xl p-0"
            >
              <IconStop size={18} />
            </Button>
          ) : (
            <Button
              variant="primary"
              aria-label="Send message"
              onClick={doSend}
              disabled={!draft.trim()}
              className="h-11 w-11 shrink-0 rounded-xl p-0"
            >
              <IconSend size={18} />
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
