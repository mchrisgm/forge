// Chat composer: auto-growing textarea, attachment upload chips, thinking
// selector, image-generation mode (toggle or "/imagine <prompt>"), send/stop.
// Enter sends; Shift+Enter adds a newline. On mobile (< sm) the tool buttons
// move to a row above the input so the textarea + send button get the full
// width; on sm+ everything sits on one row (flex order classes, no duplicate
// controls in the DOM).

import {
  forwardRef,
  useImperativeHandle,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import { Link } from "react-router-dom";
import { api, errorMessage, fileUrl } from "../../api/client";
import type { ThinkingLevel, UploadMeta } from "../../api/types";
import { useToast } from "../../hooks/toast";
import { cx, formatBytes } from "../../lib/utils";
import {
  IconAlert,
  IconFile,
  IconGlobe,
  IconImage,
  IconPaperclip,
  IconSend,
  IconStop,
  IconX,
} from "../icons";
import { ThinkingSelect } from "../ThinkingSelect";
import { Button, Spinner } from "../ui";

const MAX_ATTACHMENTS = 8; // mirrors the backend's per-message cap

/** "/imagine <prompt>" routes the turn to image generation. */
const IMAGINE_RE = /^\/imagine(\s|$)/;

/** Hostname for a fetched-page chip label; falls back to the raw string. */
function hostOf(url: string): string {
  try {
    return new URL(url).host || url;
  } catch {
    return url;
  }
}

/** An available image-generation backend: "local" or a connector kind. */
export interface ImageProvider {
  id: string;
  label: string;
}

interface PendingAttachment {
  localId: number;
  filename: string;
  uploading: boolean;
  meta?: UploadMeta;
  /** Set for a "read this page" attachment — drives the fetched-page chip. */
  page?: { host: string; mode_used: string; truncated: boolean };
}

function AttachmentChip({
  attachment,
  onRemove,
}: {
  attachment: PendingAttachment;
  onRemove: () => void;
}) {
  const { meta, page } = attachment;
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
            {page ? `Reading ${attachment.filename}…` : attachment.filename}
          </span>
        </>
      ) : isImage && meta ? (
        <img
          src={fileUrl(meta.id)}
          alt={meta.filename}
          className="h-12 w-12 rounded-md object-cover"
        />
      ) : page && meta ? (
        <>
          <IconGlobe size={13} className="shrink-0 text-info" />
          <span className="max-w-40 truncate text-text">{page.host}</span>
          <span className="rounded bg-overlay px-1 text-[10px] text-faint">
            {page.mode_used}
          </span>
          {page.truncated && (
            <span
              title="Page was truncated at the ~150 KB cap"
              className="inline-flex text-warn"
            >
              <IconAlert size={12} />
            </span>
          )}
        </>
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

/** Imperative surface for the chat view (starter-prompt cards). */
export interface ComposerHandle {
  /** Replace the draft with `text` and focus the input — never auto-sends. */
  prefill: (text: string) => void;
}

interface ComposerProps {
  onSend: (content: string, uploads: UploadMeta[]) => void;
  onStop: () => void;
  /** An image turn — from the image toggle or "/imagine <prompt>". */
  onGenerateImage: (prompt: string, provider: string) => void;
  /** Image backends on offer; empty = generation isn't set up yet. */
  imageProviders: ImageProvider[];
  streaming: boolean;
  /** An image request is in flight — sends stay disabled meanwhile. */
  generatingImage?: boolean;
  /** Nothing is serving — typing allowed, sending is not. */
  disabled?: boolean;
  thinking: ThinkingLevel;
  onThinking: (next: ThinkingLevel) => void;
  placeholder?: string;
}

export const Composer = forwardRef<ComposerHandle, ComposerProps>(
  function Composer(
    {
      onSend,
      onStop,
      onGenerateImage,
      imageProviders,
      streaming,
      generatingImage = false,
      disabled = false,
      thinking,
      onThinking,
      placeholder = "Message…",
    },
    ref,
  ) {
  const { toast } = useToast();
  const [draft, setDraft] = useState("");
  const [attachments, setAttachments] = useState<PendingAttachment[]>([]);
  const [imageMode, setImageMode] = useState(false);
  const [readMode, setReadMode] = useState(false);
  const [pageUrl, setPageUrl] = useState("");
  const [readingPage, setReadingPage] = useState(false);
  const [pickedProvider, setPickedProvider] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const nextLocalId = useRef(1);

  const uploading = attachments.some((a) => a.uploading);
  const ready = attachments.filter((a) => a.meta);

  // "local" first, then connectors; a stale pick falls back to the first.
  const provider =
    imageProviders.find((p) => p.id === pickedProvider) ??
    imageProviders[0] ??
    null;

  const trimmed = draft.trim();
  const isImagine = IMAGINE_RE.test(trimmed);
  const imageIntent = imageMode || isImagine;
  const imagePrompt = isImagine
    ? trimmed.replace(IMAGINE_RE, "").trim()
    : imageMode
      ? trimmed
      : "";

  const canSend =
    !streaming &&
    !generatingImage &&
    !uploading &&
    (imageIntent
      ? imagePrompt.length > 0 && provider != null
      : !disabled && (trimmed.length > 0 || ready.length > 0));

  const doSend = () => {
    if (!canSend) return;
    if (imageIntent) {
      if (!provider) return;
      onGenerateImage(imagePrompt, provider.id);
    } else {
      const uploads = ready.map((a) => a.meta as UploadMeta);
      onSend(trimmed, uploads);
      setAttachments([]);
    }
    setDraft("");
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

  useImperativeHandle(ref, () => ({
    prefill: (text: string) => {
      setDraft(text);
      // Grow + focus after the new value has rendered into the textarea.
      requestAnimationFrame(() => {
        autoGrow();
        textareaRef.current?.focus();
      });
    },
  }));

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

  // "Read this page": fetch a URL as a text attachment (Scrapling). The result
  // rides the normal attachment plumbing, so it inlines into the next message.
  const submitReadPage = () => {
    const url = pageUrl.trim();
    if (!url || readingPage) return;
    if (attachments.length >= MAX_ATTACHMENTS) {
      toast("info", `Up to ${MAX_ATTACHMENTS} attachments per message`);
      return;
    }
    const localId = nextLocalId.current++;
    const host = hostOf(url);
    // A pending chip holds the spot — the stealth fetch can take 5-30s.
    setAttachments((prev) => [
      ...prev,
      {
        localId,
        filename: host,
        uploading: true,
        page: { host, mode_used: "", truncated: false },
      },
    ]);
    setReadingPage(true);
    void api
      .readPage(url)
      .then((res) => {
        setAttachments((prev) =>
          prev.map((a) =>
            a.localId === localId
              ? {
                  ...a,
                  uploading: false,
                  meta: {
                    id: res.upload.id,
                    filename: res.upload.filename,
                    mime: res.upload.mime,
                    kind: res.upload.kind,
                    size_bytes: res.upload.size_bytes,
                    created_at: new Date().toISOString(),
                  },
                  page: {
                    host,
                    mode_used: res.mode_used,
                    truncated: res.truncated,
                  },
                }
              : a,
          ),
        );
        setPageUrl("");
        setReadMode(false);
      })
      .catch((err: unknown) => {
        setAttachments((prev) => prev.filter((a) => a.localId !== localId));
        toast("error", `Couldn't read page: ${errorMessage(err)}`);
      })
      .finally(() => setReadingPage(false));
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

      {/* Image strip (toggle on, or "/imagine" typed): provider pick, or how
          to set generation up when nothing can generate yet */}
      {imageIntent && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 pb-2.5">
          <span className="inline-flex items-center gap-1.5 text-xs font-medium text-accent">
            <IconImage size={13} className="shrink-0" />
            Image generation
          </span>
          {imageProviders.length > 1 && (
            <div
              role="radiogroup"
              aria-label="Image provider"
              className="flex flex-wrap gap-1.5"
            >
              {imageProviders.map((p) => {
                const active = provider?.id === p.id;
                return (
                  <button
                    key={p.id}
                    type="button"
                    role="radio"
                    aria-checked={active}
                    onClick={() => setPickedProvider(p.id)}
                    className={cx(
                      "inline-flex min-h-7 cursor-pointer items-center rounded-full border px-2.5 text-xs font-medium transition-colors duration-150",
                      active
                        ? "border-accent/50 bg-accent/15 text-accent"
                        : "border-border bg-raised text-muted hover:text-text",
                    )}
                  >
                    {p.label}
                  </button>
                );
              })}
            </div>
          )}
          {imageProviders.length === 1 && (
            <span className="text-xs text-muted">via {provider?.label}</span>
          )}
          {imageProviders.length === 0 && (
            <span className="text-xs text-muted">
              Not set up —{" "}
              <Link
                to="/models"
                className="text-info underline underline-offset-2"
              >
                load an image model
              </Link>{" "}
              or{" "}
              <Link
                to="/connectors"
                className="text-info underline underline-offset-2"
              >
                enable an image connector
              </Link>{" "}
              like Higgsfield.
            </span>
          )}
        </div>
      )}

      {/* Read-a-page strip: paste a URL, fetch it as a text attachment */}
      {readMode && !imageMode && (
        <div className="flex flex-wrap items-center gap-2 pb-2.5">
          <span className="inline-flex items-center gap-1.5 text-xs font-medium text-info">
            <IconGlobe size={13} className="shrink-0" />
            Read a page
          </span>
          <input
            type="url"
            value={pageUrl}
            onChange={(e) => setPageUrl(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                submitReadPage();
              }
            }}
            placeholder="https://example.com/article"
            disabled={readingPage}
            aria-label="Page URL"
            className="min-h-9 min-w-0 flex-1 basis-48 rounded-lg border border-edge bg-raised px-3 text-sm text-text placeholder:text-faint focus:border-accent focus:outline-none disabled:opacity-60"
          />
          <Button
            size="sm"
            onClick={submitReadPage}
            loading={readingPage}
            disabled={!pageUrl.trim()}
          >
            {readingPage ? "Reading…" : "Read"}
          </Button>
        </div>
      )}

      {/* < sm: tool buttons (order-1) wrap onto a top row, a full-width break
          (order-2) pushes the textarea (order-3) + send (order-4) onto their
          own row. sm+: order-none everywhere restores the single-row layout. */}
      <div className="flex flex-wrap items-end gap-2 pb-3">
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
          disabled={
            streaming || imageMode || attachments.length >= MAX_ATTACHMENTS
          }
          className="order-1 h-11 w-11 shrink-0 rounded-xl p-0 sm:order-none"
        >
          <IconPaperclip size={17} />
        </Button>
        <button
          type="button"
          aria-label={
            imageMode ? "Switch back to text" : "Generate an image"
          }
          aria-pressed={imageMode}
          title="Generate an image (or type /imagine …)"
          disabled={streaming || generatingImage}
          onClick={() => {
            setImageMode((m) => !m);
            setReadMode(false);
          }}
          className={cx(
            "order-1 flex h-11 w-11 shrink-0 cursor-pointer items-center justify-center rounded-xl border text-sm transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-50 sm:order-none",
            imageMode
              ? "border-accent/50 bg-accent/15 text-accent"
              : "border-edge bg-raised text-text hover:bg-overlay",
          )}
        >
          <IconImage size={17} />
        </button>
        <button
          type="button"
          aria-label={readMode ? "Cancel reading a page" : "Read a web page"}
          aria-pressed={readMode}
          title="Read a web page into the chat"
          disabled={
            streaming || imageMode || attachments.length >= MAX_ATTACHMENTS
          }
          onClick={() => setReadMode((m) => !m)}
          className={cx(
            "order-1 flex h-11 w-11 shrink-0 cursor-pointer items-center justify-center rounded-xl border text-sm transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-50 sm:order-none",
            readMode
              ? "border-info/50 bg-info/15 text-info"
              : "border-edge bg-raised text-text hover:bg-overlay",
          )}
        >
          <IconGlobe size={17} />
        </button>
        {/* Mobile-only flex line break between the tool row and the input. */}
        <div aria-hidden className="order-2 basis-full sm:hidden" />
        <label htmlFor="chat-composer" className="sr-only">
          Message
        </label>
        <textarea
          id="chat-composer"
          ref={textareaRef}
          rows={1}
          value={draft}
          placeholder={imageMode ? "Describe the image to generate…" : placeholder}
          onChange={(e) => {
            setDraft(e.target.value);
            autoGrow();
          }}
          onKeyDown={onKeyDown}
          className="order-3 max-h-40 min-h-11 min-w-0 flex-1 resize-none rounded-xl border border-edge bg-raised px-3.5 py-2.5 text-sm text-text placeholder:text-faint focus:border-accent focus:outline-none sm:order-none"
        />
        {/* DOM order keeps thinking between textarea and send on sm+; order-1
            lifts it onto the mobile tool row (last slot, hugging the right). */}
        <div className="order-1 ml-auto shrink-0 sm:order-none sm:ml-0">
          <ThinkingSelect value={thinking} onChange={onThinking} />
        </div>
        {streaming ? (
          <Button
            variant="danger"
            aria-label="Stop generating"
            onClick={onStop}
            className="order-4 h-11 w-11 shrink-0 rounded-xl p-0 sm:order-none"
          >
            <IconStop size={18} />
          </Button>
        ) : (
          <Button
            variant="primary"
            aria-label={imageIntent ? "Generate image" : "Send message"}
            onClick={doSend}
            disabled={!canSend}
            className="order-4 h-11 w-11 shrink-0 rounded-xl p-0 sm:order-none"
          >
            {generatingImage ? <Spinner size={18} /> : <IconSend size={18} />}
          </Button>
        )}
      </div>
    </div>
  );
  },
);
