// Chat message bubbles: user bubbles with attachment chips/thumbnails,
// assistant markdown, inline stream errors with retry.

import { fileUrl } from "../../api/client";
import type { AttachmentMeta } from "../../api/types";
import { cx, formatBytes } from "../../lib/utils";
import { IconFile, IconRefresh } from "../icons";
import { Markdown } from "../lazy-markdown";
import { Button } from "../ui";

/** A message as the chat surface renders it (server or in-flight). */
export interface UiMessage {
  key: string;
  role: "user" | "assistant";
  content: string;
  attachments: AttachmentMeta[];
  /** True while tokens are still streaming into this message. */
  streaming?: boolean;
  /** In-stream error frame attached to this assistant turn. */
  error?: string | null;
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
        <img
          key={a.id}
          src={fileUrl(a.id)}
          alt={a.filename}
          loading="lazy"
          className="h-24 max-w-40 rounded-lg border border-border object-cover"
        />
      ))}
      {files.map((a) => (
        <span
          key={a.id}
          className="inline-flex max-w-56 items-center gap-1.5 rounded-lg border border-border bg-raised px-2.5 py-1.5 text-xs text-muted"
          title={a.filename}
        >
          <IconFile size={13} className="shrink-0" />
          <span className="truncate text-text">{a.filename}</span>
          <span className="shrink-0 text-faint">{formatBytes(a.size_bytes)}</span>
        </span>
      ))}
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

  // Assistant turn that errored — the partial text (if any) plus the error.
  return (
    <div className="w-full max-w-full">
      {message.content && <Markdown text={message.content} />}
      {message.error && (
        <div
          role="alert"
          className={cx(
            "rounded-xl border border-danger/30 bg-danger/10 p-3.5",
            message.content && "mt-2",
          )}
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
