// Conversation history: rename inline, archive, delete, archived section.
// Rendered as the left pane on md+ and as the full /chats screen on mobile.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, errorMessage } from "../../api/client";
import type { Conversation } from "../../api/types";
import { useToast } from "../../hooks/toast";
import { cx, relativeTime } from "../../lib/utils";
import {
  IconArchive,
  IconDots,
  IconEdit,
  IconPlus,
  IconTrash,
} from "../icons";
import { Button, Collapsible, ConfirmDialog, EmptyState, SkeletonBlock } from "../ui";

function RowMenu({
  conversation,
  active,
  onRename,
}: {
  conversation: Conversation;
  active: boolean;
  onRename: () => void;
}) {
  const { toast } = useToast();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const invalidate = () =>
    void queryClient.invalidateQueries({ queryKey: ["conversations"] });

  const archive = useMutation({
    mutationFn: () =>
      api.patchConversation(conversation.id, {
        archived: !conversation.archived,
      }),
    onSuccess: invalidate,
    onError: (err) => toast("error", errorMessage(err)),
  });

  const remove = useMutation({
    mutationFn: () => api.deleteConversation(conversation.id),
    onSuccess: () => {
      invalidate();
      setConfirmDelete(false);
      toast("success", "Chat deleted");
      if (active) navigate("/chats", { replace: true });
    },
    onError: (err) => toast("error", errorMessage(err)),
  });

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <div className="relative shrink-0">
      <button
        type="button"
        aria-label={`Options for ${conversation.title}`}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="flex h-8 w-8 cursor-pointer items-center justify-center rounded-md text-faint hover:bg-overlay hover:text-text"
      >
        <IconDots size={16} />
      </button>
      {open && (
        <>
          <button
            type="button"
            aria-label="Close menu"
            tabIndex={-1}
            onClick={() => setOpen(false)}
            className="fixed inset-0 z-20 cursor-default"
          />
          <div
            role="menu"
            className="absolute top-full right-0 z-30 w-44 animate-rise rounded-xl border border-border bg-surface p-1 shadow-xl shadow-black/50"
          >
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                onRename();
              }}
              className="flex min-h-10 w-full cursor-pointer items-center gap-2.5 rounded-lg px-3 text-left text-sm text-muted hover:bg-raised hover:text-text"
            >
              <IconEdit size={15} />
              Rename
            </button>
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                archive.mutate();
              }}
              className="flex min-h-10 w-full cursor-pointer items-center gap-2.5 rounded-lg px-3 text-left text-sm text-muted hover:bg-raised hover:text-text"
            >
              <IconArchive size={15} />
              {conversation.archived ? "Unarchive" : "Archive"}
            </button>
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                setConfirmDelete(true);
              }}
              className="flex min-h-10 w-full cursor-pointer items-center gap-2.5 rounded-lg px-3 text-left text-sm text-danger hover:bg-danger/10"
            >
              <IconTrash size={15} />
              Delete
            </button>
          </div>
        </>
      )}
      <ConfirmDialog
        open={confirmDelete}
        title={`Delete "${conversation.title}"?`}
        body="The conversation and all its messages are removed permanently."
        busy={remove.isPending}
        onCancel={() => setConfirmDelete(false)}
        onConfirm={() => remove.mutate()}
      />
    </div>
  );
}

function ConversationRow({
  conversation,
  active,
  generating = false,
}: {
  conversation: Conversation;
  active: boolean;
  /** A server-side generation is in flight for this conversation right now. */
  generating?: boolean;
}) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [renaming, setRenaming] = useState(false);
  const [title, setTitle] = useState(conversation.title);
  const inputRef = useRef<HTMLInputElement>(null);

  const rename = useMutation({
    mutationFn: (next: string) =>
      api.patchConversation(conversation.id, { title: next }),
    onSuccess: () =>
      void queryClient.invalidateQueries({ queryKey: ["conversations"] }),
    onError: (err) => toast("error", errorMessage(err)),
  });

  useEffect(() => {
    if (renaming) {
      setTitle(conversation.title);
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [renaming, conversation.title]);

  const commitRename = () => {
    setRenaming(false);
    const next = title.trim();
    if (next && next !== conversation.title) rename.mutate(next);
  };

  return (
    <div
      className={cx(
        "group relative flex items-center gap-1 rounded-lg pr-1 transition-colors duration-150",
        active ? "bg-accent/10" : "hover:bg-raised",
      )}
    >
      {renaming ? (
        <input
          ref={inputRef}
          value={title}
          aria-label="Conversation title"
          onChange={(e) => setTitle(e.target.value)}
          onBlur={commitRename}
          onKeyDown={(e) => {
            if (e.key === "Enter") commitRename();
            if (e.key === "Escape") setRenaming(false);
          }}
          className="mx-1 my-1.5 w-full min-w-0 flex-1 rounded-md border border-accent bg-raised px-2 py-1.5 text-sm text-text focus:outline-none"
        />
      ) : (
        <Link
          to={`/chats/${conversation.id}`}
          className="min-w-0 flex-1 px-3 py-2.5"
          aria-current={active ? "page" : undefined}
        >
          <span
            className={cx(
              "flex items-center gap-1.5 text-sm font-medium",
              active ? "text-accent" : "text-text",
            )}
          >
            {generating && (
              <span
                aria-hidden
                title="Generating…"
                className="h-1.5 w-1.5 shrink-0 rounded-full bg-accent animate-pulse-dot"
              />
            )}
            <span className="truncate">{conversation.title}</span>
            {generating && <span className="sr-only">(generating)</span>}
          </span>
          <span className="mt-0.5 flex items-center gap-2 text-[11px] text-faint">
            {relativeTime(conversation.updated_at)}
            {conversation.model_slug && (
              <span className="inline-flex max-w-28 items-center truncate rounded border border-border bg-raised px-1 py-px font-mono text-[10px] text-muted">
                {conversation.model_slug}
              </span>
            )}
          </span>
        </Link>
      )}
      {!renaming && (
        <RowMenu
          conversation={conversation}
          active={active}
          onRename={() => setRenaming(true)}
        />
      )}
    </div>
  );
}

export function ConversationList({ activeId }: { activeId: string | null }) {
  const navigate = useNavigate();
  const conversations = useQuery({
    queryKey: ["conversations", false],
    queryFn: () => api.listConversations(false),
    refetchInterval: 30_000,
  });
  const archived = useQuery({
    queryKey: ["conversations", true],
    queryFn: () => api.listConversations(true),
  });
  // Poll which conversations are generating right now — server-side jobs stay
  // visible whether or not the chat is open, so this badges the list live.
  const active = useQuery({
    queryKey: ["chat-active"],
    queryFn: api.activeGenerations,
    refetchInterval: 3000,
  });
  const activeIds = new Set((active.data ?? []).map((a) => a.conversation_id));

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center justify-between gap-2 px-4 pt-6 pb-3 md:px-3">
        <h1 className="text-xl font-bold tracking-tight text-text md:text-sm md:font-semibold md:tracking-normal">
          Chats
        </h1>
        <Button
          size="sm"
          variant="primary"
          onClick={() => navigate("/chats/new")}
        >
          <IconPlus size={15} />
          New chat
        </Button>
      </div>

      <div className="min-h-0 flex-1 space-y-0.5 overflow-y-auto px-2 pb-tabbar md:pb-4">
        {conversations.isLoading && (
          <div className="space-y-2 px-1 pt-1" aria-busy="true" aria-label="Loading chats">
            {Array.from({ length: 4 }, (_, i) => (
              <SkeletonBlock key={i} className="h-12 w-full" />
            ))}
          </div>
        )}

        {conversations.isError && (
          <EmptyState
            icon="search"
            title="Couldn't load chats"
            hint={errorMessage(conversations.error)}
            action={
              <Button size="sm" onClick={() => void conversations.refetch()}>
                Retry
              </Button>
            }
          />
        )}

        {conversations.data && conversations.data.length === 0 && (
          <EmptyState
            icon="spark"
            title="No chats yet"
            hint="Start a conversation with your local model — it remembers what matters."
          />
        )}

        {conversations.data?.map((c) => (
          <ConversationRow
            key={c.id}
            conversation={c}
            active={c.id === activeId}
            generating={activeIds.has(c.id)}
          />
        ))}

        {archived.data && archived.data.length > 0 && (
          <div className="px-1 pt-4">
            <Collapsible
              summary={
                <span className="text-xs font-semibold tracking-wider text-faint uppercase">
                  Archived ({archived.data.length})
                </span>
              }
            >
              <div className="-ml-6 space-y-0.5 pt-1">
                {archived.data.map((c) => (
                  <ConversationRow
                    key={c.id}
                    conversation={c}
                    active={c.id === activeId}
                    generating={activeIds.has(c.id)}
                  />
                ))}
              </div>
            </Collapsible>
          </div>
        )}
      </div>
    </div>
  );
}
