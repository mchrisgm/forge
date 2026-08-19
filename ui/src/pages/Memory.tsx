// The user's memory store: what Forge has learned from their chats,
// grouped by kind — inspect, edit, pin, prune, or wipe it all.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, errorMessage } from "../api/client";
import type { MemoryEntry, MemoryKind } from "../api/types";
import {
  IconEdit,
  IconPlus,
  IconStar,
  IconTrash,
} from "../components/icons";
import { PageHeader } from "../components/layout";
import {
  Button,
  ConfirmDialog,
  EmptyState,
  Select,
  SkeletonList,
  TextArea,
} from "../components/ui";
import { useToast } from "../hooks/toast";
import { cx } from "../lib/utils";

const KINDS: readonly {
  id: MemoryKind;
  label: string;
  plural: string;
  badge: string;
}[] = [
  { id: "fact", label: "Fact", plural: "Facts", badge: "text-info border-info/35 bg-info/10" },
  { id: "preference", label: "Preference", plural: "Preferences", badge: "text-ok border-ok/35 bg-ok/10" },
  { id: "project", label: "Project", plural: "Projects", badge: "text-warn border-warn/35 bg-warn/10" },
  { id: "episode", label: "Episode", plural: "Episodes", badge: "text-lane-airllm border-lane-airllm/35 bg-lane-airllm/10" },
];

function ImportanceDots({ value }: { value: number }) {
  const filled = Math.max(1, Math.round((Math.min(2, Math.max(0, value)) / 2) * 5));
  return (
    <span
      className="inline-flex items-center gap-0.5"
      title={`Importance ${value.toFixed(1)} of 2.0`}
      aria-label={`Importance ${value.toFixed(1)} of 2`}
      role="img"
    >
      {Array.from({ length: 5 }, (_, i) => (
        <span
          key={i}
          aria-hidden
          className={cx(
            "h-1.5 w-1.5 rounded-full",
            i < filled ? "bg-accent" : "bg-overlay",
          )}
        />
      ))}
    </span>
  );
}

function MemoryRow({ entry }: { entry: MemoryEntry }) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(entry.content);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const editRef = useRef<HTMLTextAreaElement>(null);

  const invalidate = () =>
    void queryClient.invalidateQueries({ queryKey: ["memory"] });

  const patch = useMutation({
    mutationFn: (body: { content?: string; pinned?: boolean }) =>
      api.patchMemory(entry.id, body),
    onSuccess: invalidate,
    onError: (err) => toast("error", errorMessage(err)),
  });
  const remove = useMutation({
    mutationFn: () => api.deleteMemory(entry.id),
    onSuccess: () => {
      invalidate();
      setConfirmDelete(false);
    },
    onError: (err) => toast("error", errorMessage(err)),
  });

  useEffect(() => {
    if (editing) {
      setDraft(entry.content);
      editRef.current?.focus();
    }
  }, [editing, entry.content]);

  const kindCfg = KINDS.find((k) => k.id === entry.kind) ?? KINDS[0];

  const commit = () => {
    setEditing(false);
    const next = draft.trim();
    if (next && next !== entry.content) patch.mutate({ content: next });
  };

  return (
    <li className="rounded-xl border border-border bg-surface p-3.5">
      {editing ? (
        <div className="space-y-2">
          <TextArea
            ref={editRef}
            aria-label="Memory content"
            rows={2}
            maxLength={500}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                commit();
              }
              if (e.key === "Escape") setEditing(false);
            }}
          />
          <div className="flex gap-2">
            <Button size="sm" variant="primary" onClick={commit}>
              Save
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <p className="text-sm break-words text-text">{entry.content}</p>
      )}

      <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <span
          className={cx(
            "inline-flex items-center rounded border px-1.5 py-0.5 font-mono text-[10px] font-medium",
            kindCfg.badge,
          )}
        >
          {kindCfg.label.toLowerCase()}
        </span>
        <ImportanceDots value={entry.importance} />
        {entry.use_count > 0 && (
          <span className="text-[11px] text-faint">
            used {entry.use_count}×
          </span>
        )}
        <span className="flex-1" />
        <button
          type="button"
          aria-label={entry.pinned ? "Unpin memory" : "Pin memory"}
          aria-pressed={entry.pinned}
          title={entry.pinned ? "Pinned — always injected" : "Pin"}
          disabled={patch.isPending}
          onClick={() => patch.mutate({ pinned: !entry.pinned })}
          className={cx(
            "flex h-9 w-9 cursor-pointer items-center justify-center rounded-md transition-colors duration-150",
            entry.pinned
              ? "text-accent hover:bg-accent/10"
              : "text-faint hover:bg-raised hover:text-text",
          )}
        >
          <IconStar
            size={16}
            fill={entry.pinned ? "currentColor" : "none"}
          />
        </button>
        {!editing && (
          <button
            type="button"
            aria-label="Edit memory"
            onClick={() => setEditing(true)}
            className="flex h-9 w-9 cursor-pointer items-center justify-center rounded-md text-faint hover:bg-raised hover:text-text"
          >
            <IconEdit size={15} />
          </button>
        )}
        <button
          type="button"
          aria-label="Delete memory"
          onClick={() => setConfirmDelete(true)}
          className="flex h-9 w-9 cursor-pointer items-center justify-center rounded-md text-danger/70 hover:bg-danger/10 hover:text-danger"
        >
          <IconTrash size={15} />
        </button>
      </div>

      <ConfirmDialog
        open={confirmDelete}
        title="Forget this memory?"
        body={entry.content.slice(0, 140)}
        confirmLabel="Forget"
        busy={remove.isPending}
        onCancel={() => setConfirmDelete(false)}
        onConfirm={() => remove.mutate()}
      />
    </li>
  );
}

function AddMemoryForm() {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [content, setContent] = useState("");
  const [kind, setKind] = useState<MemoryKind>("fact");
  const [pinned, setPinned] = useState(false);

  const add = useMutation({
    mutationFn: () => api.addMemory({ content: content.trim(), kind, pinned }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["memory"] });
      setContent("");
      setPinned(false);
      toast("success", "Memory added");
    },
    onError: (err) => toast("error", errorMessage(err)),
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (content.trim()) add.mutate();
  };

  return (
    <form
      onSubmit={submit}
      className="space-y-3 rounded-xl border border-border bg-surface p-4"
    >
      <h2 className="text-sm font-semibold text-text">Add a memory</h2>
      <label htmlFor="memory-content" className="sr-only">
        Memory content
      </label>
      <TextArea
        id="memory-content"
        rows={2}
        maxLength={500}
        value={content}
        onChange={(e) => setContent(e.target.value)}
        placeholder="e.g. I deploy everything with Docker Compose on a single box."
      />
      <div className="flex flex-wrap items-center gap-3">
        <label htmlFor="memory-kind" className="sr-only">
          Kind
        </label>
        <Select
          id="memory-kind"
          value={kind}
          onChange={(e) => setKind(e.target.value as MemoryKind)}
          className="w-40"
        >
          {KINDS.map((k) => (
            <option key={k.id} value={k.id}>
              {k.label}
            </option>
          ))}
        </Select>
        <label className="flex cursor-pointer items-center gap-2 text-sm text-muted">
          <input
            type="checkbox"
            checked={pinned}
            onChange={(e) => setPinned(e.target.checked)}
            className="h-4 w-4 accent-(--color-accent)"
          />
          Pin it
        </label>
        <span className="flex-1" />
        <Button
          type="submit"
          variant="primary"
          size="sm"
          loading={add.isPending}
          disabled={!content.trim()}
        >
          <IconPlus size={15} />
          Add
        </Button>
      </div>
    </form>
  );
}

export default function Memory() {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [wipeStage, setWipeStage] = useState<0 | 1 | 2>(0);

  const memories = useQuery({ queryKey: ["memory"], queryFn: api.listMemories });

  const wipe = useMutation({
    mutationFn: api.clearMemories,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["memory"] });
      setWipeStage(0);
      toast("success", "All memories forgotten");
    },
    onError: (err) => toast("error", errorMessage(err)),
  });

  const entries = memories.data ?? [];
  const groups = KINDS.map((k) => ({
    ...k,
    entries: entries.filter((e) => e.kind === k.id),
  })).filter((g) => g.entries.length > 0);

  return (
    <div>
      <PageHeader
        title="Memory"
        subtitle="What Forge remembers about you"
      />

      <p className="mb-5 rounded-xl border border-border bg-surface p-4 text-sm leading-relaxed text-muted">
        As you chat, Forge quietly saves durable facts, preferences and project
        context — and injects the relevant ones into future conversations.
        Importance decays with age and grows with use; pinned memories are
        always included and never auto-pruned. Temporary chats read and write
        nothing here.
      </p>

      <div className="space-y-5">
        <AddMemoryForm />

        {memories.isLoading && <SkeletonList rows={3} />}
        {memories.isError && (
          <EmptyState
            icon="search"
            title="Couldn't load memories"
            hint={errorMessage(memories.error)}
            action={
              <Button onClick={() => void memories.refetch()}>Retry</Button>
            }
          />
        )}

        {memories.data && entries.length === 0 && (
          <EmptyState
            icon="spark"
            title="Nothing remembered yet"
            hint="Chat a while — or add something above — and it will show up here."
          />
        )}

        {groups.map((group) => (
          <section key={group.id} aria-label={group.plural}>
            <h2 className="mb-2 px-1 text-xs font-semibold tracking-wider text-faint uppercase">
              {group.plural} ({group.entries.length})
            </h2>
            <ul className="space-y-2">
              {group.entries.map((entry) => (
                <MemoryRow key={entry.id} entry={entry} />
              ))}
            </ul>
          </section>
        ))}

        {entries.length > 0 && (
          <div className="border-t border-border pt-5">
            <Button
              variant="danger"
              className="w-full"
              onClick={() => setWipeStage(1)}
            >
              <IconTrash size={16} />
              Forget everything
            </Button>
          </div>
        )}
      </div>

      <ConfirmDialog
        open={wipeStage === 1}
        title="Forget everything?"
        body={`All ${entries.length} memories — pinned ones included — will be deleted.`}
        confirmLabel="Continue"
        onCancel={() => setWipeStage(0)}
        onConfirm={() => setWipeStage(2)}
      />
      <ConfirmDialog
        open={wipeStage === 2}
        title="Really forget everything?"
        body="There is no undo. Forge starts over knowing nothing about you."
        confirmLabel="Delete it all"
        busy={wipe.isPending}
        onCancel={() => setWipeStage(0)}
        onConfirm={() => wipe.mutate()}
      />
    </div>
  );
}
