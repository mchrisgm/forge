// Chat composer: auto-growing textarea, attachment upload chips, thinking
// selector, send/stop. Enter sends; Shift+Enter adds a newline.

import { useRef, useState, type KeyboardEvent } from "react";
import { api, errorMessage, fileUrl } from "../../api/client";
import type { ThinkingLevel, UploadMeta } from "../../api/types";
import { useToast } from "../../hooks/toast";
import { cx, formatBytes } from "../../lib/utils";
import { IconFile, IconPaperclip, IconSend, IconStop, IconX } from "../icons";
import { ThinkingSelect } from "../ThinkingSelect";
import { Button, Spinner } from "../ui";

const MAX_ATTACHMENTS = 8; // mirrors the backend's per-message cap

interface PendingAttachment {
  localId: number;
  filename: string;
  uploading: boolean;
  meta?: UploadMeta;
}

function AttachmentChip({
  attachment,
  onRemove,
}: {
  attachment: PendingAttachment;
  onRemove: () => void;
}) {
  const { meta } = attachment;
  const isImage = meta?.kind === "image";
  return (
    <span
      className={cx(
        "relative inline-flex items-center gap-1.5 rounded-lg border border-border bg-raised text-xs",
        isImage ? "p-1" : "py-1.5 pr-1 pl-2.5",
      )}
    >
      {attachment.uploading ? (
        <>
          <Spinner size={13} />
          <span className="max-w-32 truncate text-muted">
            {attachment.filename}
          </span>
        </>
      ) : isImage && meta ? (
        <img
          src={fileUrl(meta.id)}
          alt={meta.filename}
          className="h-12 w-12 rounded-md object-cover"
        />
      ) : (
        <>
          <IconFile size={13} className="shrink-0 text-muted" />
          <span className="max-w-32 truncate text-text">
            {attachment.filename}
          </span>
          {meta && (
            <span className="text-faint">{formatBytes(meta.size_bytes)}</span>
          )}
        </>
      )}
      <button
        type="button"
        aria-label={`Remove ${attachment.filename}`}
        onClick={onRemove}
        disabled={attachment.uploading}
        className={cx(
          "cursor-pointer rounded-md p-1 text-faint hover:text-text disabled:opacity-40",
          isImage &&
            "absolute -top-1.5 -right-1.5 border border-border bg-overlay p-0.5",
        )}
      >
        <IconX size={12} />
      </button>
    </span>
  );
}

export function Composer({
  onSend,
  onStop,
  streaming,
  disabled = false,
  thinking,
  onThinking,
  placeholder = "Message…",
}: {
  onSend: (content: string, uploads: UploadMeta[]) => void;
  onStop: () => void;
  streaming: boolean;
  /** Nothing is serving — typing allowed, sending is not. */
  disabled?: boolean;
  thinking: ThinkingLevel;
  onThinking: (next: ThinkingLevel) => void;
  placeholder?: string;
}) {
  const { toast } = useToast();
  const [draft, setDraft] = useState("");
  const [attachments, setAttachments] = useState<PendingAttachment[]>([]);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const nextLocalId = useRef(1);

  const uploading = attachments.some((a) => a.uploading);
  const ready = attachments.filter((a) => a.meta);
  const canSend =
    !disabled &&
    !streaming &&
    !uploading &&
    (draft.trim().length > 0 || ready.length > 0);

  const doSend = () => {
    if (!canSend) return;
    const uploads = ready.map((a) => a.meta as UploadMeta);
    onSend(draft.trim(), uploads);
    setDraft("");
    setAttachments([]);
    if (textareaRef.current) textareaRef.current.style.height = "auto";
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
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

  const pickFiles = (files: FileList | null) => {
    if (!files) return;
    const room = MAX_ATTACHMENTS - attachments.length;
    const chosen = Array.from(files).slice(0, Math.max(0, room));
    if (files.length > chosen.length) {
      toast("info", `Up to ${MAX_ATTACHMENTS} attachments per message`);
    }
    for (const file of chosen) {
      const localId = nextLocalId.current++;
      setAttachments((prev) => [
        ...prev,
        { localId, filename: file.name, uploading: true },
      ]);
      void api
        .uploadFile(file)
        .then((meta) => {
          setAttachments((prev) =>
            prev.map((a) =>
              a.localId === localId ? { ...a, uploading: false, meta } : a,
            ),
          );
        })
        .catch((err: unknown) => {
          setAttachments((prev) => prev.filter((a) => a.localId !== localId));
          toast("error", `Upload failed: ${errorMessage(err)}`);
        });
    }
  };

  const removeAttachment = (a: PendingAttachment) => {
    setAttachments((prev) => prev.filter((x) => x.localId !== a.localId));
    if (a.meta) void api.deleteFile(a.meta.id).catch(() => undefined);
  };

  return (
    <div className="border-t border-border bg-bg/95 pt-3 backdrop-blur">
      {attachments.length > 0 && (
        <div className="flex flex-wrap gap-2 pb-2.5">
          {attachments.map((a) => (
            <AttachmentChip
              key={a.localId}
              attachment={a}
              onRemove={() => removeAttachment(a)}
            />
          ))}
        </div>
      )}
      <div className="flex items-end gap-2 pb-3">
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="sr-only"
          aria-hidden
          tabIndex={-1}
          onChange={(e) => {
            pickFiles(e.target.files);
            e.target.value = "";
          }}
        />
        <Button
          aria-label="Attach files"
          title="Attach files"
          onClick={() => fileInputRef.current?.click()}
          disabled={streaming || attachments.length >= MAX_ATTACHMENTS}
          className="h-11 w-11 shrink-0 rounded-xl p-0"
        >
          <IconPaperclip size={17} />
        </Button>
        <label htmlFor="chat-composer" className="sr-only">
          Message
        </label>
        <textarea
          id="chat-composer"
          ref={textareaRef}
          rows={1}
          value={draft}
          placeholder={placeholder}
          onChange={(e) => {
            setDraft(e.target.value);
            autoGrow();
          }}
          onKeyDown={onKeyDown}
          className="max-h-40 min-h-11 flex-1 resize-none rounded-xl border border-edge bg-raised px-3.5 py-2.5 text-sm text-text placeholder:text-faint focus:border-accent focus:outline-none"
        />
        <ThinkingSelect value={thinking} onChange={onThinking} />
        {streaming ? (
          <Button
            variant="danger"
            aria-label="Stop generating"
            onClick={onStop}
            className="h-11 w-11 shrink-0 rounded-xl p-0"
          >
            <IconStop size={18} />
          </Button>
        ) : (
          <Button
            variant="primary"
            aria-label="Send message"
            onClick={doSend}
            disabled={!canSend}
            className="h-11 w-11 shrink-0 rounded-xl p-0"
          >
            <IconSend size={18} />
          </Button>
        )}
      </div>
    </div>
  );
}
