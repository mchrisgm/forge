// Chat message bubbles: user bubbles with attachment chips/thumbnails,
// assistant markdown with inline images (generated ones captioned by their
// prompt), image-generation placeholders, inline stream errors with retry.

import { useEffect, useRef, useState } from "react";
import { fileUrl } from "../../api/client";
import type { AttachmentMeta } from "../../api/types";
import { cx, formatBytes } from "../../lib/utils";
import {
  IconChevronDown,
  IconChevronRight,
  IconDownload,
  IconFile,
  IconRefresh,
} from "../icons";
import { Markdown } from "../lazy-markdown";
import { Button, Spinner } from "../ui";
import { ImageLightbox } from "./ImageLightbox";

/** A message as the chat surface renders it (server or in-flight). */
export interface UiMessage {
  key: string;
  role: "user" | "assistant";
  content: string;
  attachments: AttachmentMeta[];
  /** True while tokens are still streaming into this message. */
  streaming?: boolean;
  /** The engine lane is busy — this turn is waiting for a free slot. Cleared
   *  when generation starts (forge:running) or the first token arrives. */
  queued?: boolean;
  /** Accumulated reasoning trace (reasoning_content deltas + `<think>` spans),
   *  rendered in the collapsed "Thinking" expander above the answer. */
  thinking?: string;
  /** Latest pre-token stage line from the stream (queued/status `detail`),
   *  shown verbatim while no thinking/answer tokens have arrived yet. */
  stageDetail?: string;
  /** An image generation is in flight — renders a placeholder bubble. */
  pendingImage?: { prompt: string };
  /** Temporary-mode generation: the image exists only in this tab (data
   *  URI) — nothing was stored server-side. */
  tempImage?: { dataUri: string; prompt: string };
  /** In-stream error frame attached to this assistant turn. */
  error?: string | null;
}

/** The backend records generated-image turns as "[Generated image: …]". */
const GENERATED_PLACEHOLDER_RE = /^\[Generated image:[\s\S]*\]$/;

/**
 * Split a PERSISTED assistant message into its reasoning trace and visible
 * answer: every `<think>…</think>` block (plus a trailing unclosed one) is
 * extracted into `thinking` and stripped from `answer`, so the markdown
 * renderer never sees the think text. Pure — for stored history only; live
 * streams are split token-by-token by createThinkSplitter instead.
 */
export function splitStoredThinking(content: string): {
  thinking: string;
  answer: string;
} {
  if (!content.includes("<think>")) return { thinking: "", answer: content };
  const blocks: string[] = [];
  const answer = content.replace(
    /<think>([\s\S]*?)(?:<\/think>|$)/g,
    (_match, inner: string) => {
      if (inner.trim()) blocks.push(inner.trim());
      return "";
    },
  );
  return { thinking: blocks.join("\n\n"), answer: answer.trim() };
}

/**
 * Collapsed-by-default reasoning expander above an assistant answer. While
 * the model is still thinking the header stays alive — char count and elapsed
 * seconds tick with a pulsing dot — so progress is visible without expanding;
 * expanded, the trace streams into a muted scrollable block that follows the
 * tail. After completion it stays available with the final size.
 */
function ThinkingExpander({
  text,
  live,
}: {
  text: string;
  /** True while thinking tokens are still expected (streaming, no answer yet). */
  live: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const startedAtRef = useRef<number | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Tick the header clock once per second while the model is thinking.
  useEffect(() => {
    if (!live) return;
    startedAtRef.current ??= Date.now();
    const timer = window.setInterval(() => {
      const started = startedAtRef.current ?? Date.now();
      setElapsed(Math.round((Date.now() - started) / 1000));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [live]);

  // Follow the tail while streaming with the trace expanded.
  useEffect(() => {
    const el = scrollRef.current;
    if (live && open && el) el.scrollTop = el.scrollHeight;
  }, [text, live, open]);

  return (
    <div>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="flex min-h-8 cursor-pointer items-center gap-1.5 rounded-md text-xs font-medium text-muted transition-colors duration-150 hover:text-text"
      >
        {open ? (
          <IconChevronDown size={14} className="shrink-0" />
        ) : (
          <IconChevronRight size={14} className="shrink-0" />
        )}
        <span>
          Thinking · {text.length.toLocaleString()} chars
          {live && ` · ${elapsed}s`}
        </span>
        {live && (
          <span
            aria-hidden
            className="animate-pulse-dot h-1.5 w-1.5 shrink-0 rounded-full bg-info"
          />
        )}
      </button>
      {open && (
        <div
          ref={scrollRef}
          className="mt-1 max-h-56 overflow-y-auto rounded-lg border border-border bg-raised/50 px-3 py-2.5"
        >
          <p className="text-xs leading-relaxed break-words whitespace-pre-wrap text-muted">
            {text}
          </p>
        </div>
      )}
    </div>
  );
}

/** Inline image that opens the built-in lightbox viewer (zoom/pan/export). */
function ImageAttachment({
  attachment,
  large = false,
}: {
  attachment: AttachmentMeta;
  large?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const alt =
    attachment.generated && attachment.prompt
      ? attachment.prompt
      : attachment.filename;
  return (
    <>
      <button
        type="button"
        title="View full size"
        onClick={() => setOpen(true)}
        className="block cursor-zoom-in"
      >
        <img
          src={fileUrl(attachment.id)}
          alt={alt}
          loading="lazy"
          className={cx(
            "rounded-lg border border-border object-cover",
            large ? "w-full" : "h-24 max-w-40",
          )}
        />
      </button>
      {open && (
        <ImageLightbox
          src={fileUrl(attachment.id)}
          filename={attachment.filename}
          caption={
            attachment.generated && attachment.prompt
              ? attachment.prompt
              : undefined
          }
          onClose={() => setOpen(false)}
        />
      )}
    </>
  );
}

/** File chip that downloads/opens the attachment. */
function FileChip({ attachment }: { attachment: AttachmentMeta }) {
  return (
    <a
      href={fileUrl(attachment.id)}
      target="_blank"
      rel="noreferrer noopener"
      download={attachment.filename}
      title={`Download ${attachment.filename}`}
      className="inline-flex max-w-56 items-center gap-1.5 rounded-lg border border-border bg-raised px-2.5 py-1.5 text-xs text-muted transition-colors duration-150 hover:bg-overlay hover:text-text"
    >
      <IconFile size={13} className="shrink-0" />
      <span className="truncate text-text">{attachment.filename}</span>
      <span className="shrink-0 text-faint">
        {formatBytes(attachment.size_bytes)}
      </span>
      <IconDownload size={13} className="shrink-0" />
    </a>
  );
}

export function AttachmentChips({
  attachments,
}: {
  attachments: AttachmentMeta[];
}) {
  if (attachments.length === 0) return null;
  const images = attachments.filter((a) => a.kind === "image");
  const files = attachments.filter((a) => a.kind !== "image");
  return (
    <div className="mb-1.5 flex flex-wrap justify-end gap-1.5">
      {images.map((a) => (
        <ImageAttachment key={a.id} attachment={a} />
      ))}
      {files.map((a) => (
        <FileChip key={a.id} attachment={a} />
      ))}
    </div>
  );
}

/** Assistant-side attachments: images render inline (generated ones with the
 *  prompt as caption), everything else keeps the chip + download. */
function AssistantAttachments({
  attachments,
}: {
  attachments: AttachmentMeta[];
}) {
  if (attachments.length === 0) return null;
  const images = attachments.filter((a) => a.kind === "image");
  const files = attachments.filter((a) => a.kind !== "image");
  return (
    <div className="space-y-2">
      {images.map((a) => (
        <figure key={a.id} className="m-0 max-w-sm">
          <ImageAttachment attachment={a} large />
          {a.generated && a.prompt && (
            <figcaption
              className="mt-1.5 line-clamp-2 text-xs text-faint"
              title={a.prompt}
            >
              {a.prompt}
            </figcaption>
          )}
        </figure>
      ))}
      {files.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {files.map((a) => (
            <FileChip key={a.id} attachment={a} />
          ))}
        </div>
      )}
    </div>
  );
}

/** Temporary-mode generation (data URI, nothing stored server-side) — the
 *  lightbox still works, it just reads the inline data. */
function TempImageFigure({
  image,
}: {
  image: { dataUri: string; prompt: string };
}) {
  const [open, setOpen] = useState(false);
  return (
    <figure className="m-0 max-w-sm">
      <button
        type="button"
        title="View full size"
        onClick={() => setOpen(true)}
        className="block w-full cursor-zoom-in"
      >
        <img
          src={image.dataUri}
          alt={image.prompt}
          className="w-full rounded-lg border border-border object-cover"
        />
      </button>
      <figcaption
        className="mt-1.5 line-clamp-2 text-xs text-faint"
        title={image.prompt}
      >
        {image.prompt} · temporary — not saved
      </figcaption>
      {open && (
        <ImageLightbox
          src={image.dataUri}
          filename="generated-image.png"
          caption={image.prompt}
          onClose={() => setOpen(false)}
        />
      )}
    </figure>
  );
}

/** Placeholder while an image generation runs (can take minutes). */
function PendingImageBubble({ prompt }: { prompt: string }) {
  return (
    <div
      role="status"
      aria-label="Generating image"
      className="max-w-sm rounded-xl border border-border bg-surface p-3.5"
    >
      <div aria-hidden className="skeleton aspect-square w-full rounded-lg" />
      <p className="mt-2.5 flex items-center gap-2 text-xs font-medium text-muted">
        <Spinner size={13} />
        Generating image — this can take a few minutes
      </p>
      <p className="mt-1 line-clamp-2 text-xs text-faint" title={prompt}>
        {prompt}
      </p>
    </div>
  );
}

export function MessageBubble({
  message,
  onRetry,
}: {
  message: UiMessage;
  /** Offered on failed assistant turns; retry re-sends the last user turn. */
  onRetry?: () => void;
}) {
  const isUser = message.role === "user";

  if (isUser) {
    return (
      <div className="flex flex-col items-end">
        <AttachmentChips attachments={message.attachments} />
        {message.content && (
          <div className="max-w-[92%] rounded-2xl rounded-br-md bg-accent/15 px-3.5 py-2.5 md:max-w-[85%]">
            <p className="text-sm break-words whitespace-pre-wrap text-text">
              {message.content}
            </p>
          </div>
        )}
      </div>
    );
  }

  if (message.pendingImage) {
    return (
      <div className="w-full max-w-full">
        <PendingImageBubble prompt={message.pendingImage.prompt} />
      </div>
    );
  }

  // Generated-image turns carry a "[Generated image: …]" placeholder as their
  // text — the rendered image + caption say the same thing, so hide it.
  const hasGeneratedImage =
    message.attachments.some((a) => a.kind === "image" && a.generated) ||
    Boolean(message.tempImage);
  const placeholderOnly =
    hasGeneratedImage && GENERATED_PLACEHOLDER_RE.test(message.content.trim());

  // Assistant turn: optional reasoning expander, attachments, markdown, and
  // (on failure) the error with whatever partial text streamed in.
  return (
    <div className="w-full max-w-full space-y-2">
      {message.thinking && (
        <ThinkingExpander
          text={message.thinking}
          live={Boolean(message.streaming) && !message.content}
        />
      )}
      {message.tempImage && <TempImageFigure image={message.tempImage} />}
      <AssistantAttachments attachments={message.attachments} />
      {message.content && !placeholderOnly && <Markdown text={message.content} />}
      {message.error && (
        <div
          role="alert"
          className="rounded-xl border border-danger/30 bg-danger/10 p-3.5"
        >
          <p className="text-sm break-words text-danger">{message.error}</p>
          {onRetry && (
            <Button size="sm" className="mt-2.5" onClick={onRetry}>
              <IconRefresh size={14} />
              Retry
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
