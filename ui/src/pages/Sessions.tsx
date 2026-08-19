import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import type { ModelEntry, Session } from "../api/types";
import {
  IconBranch,
  IconCube,
  IconPlay,
  IconPlus,
  IconStop,
  IconTrash,
} from "../components/icons";
import { PageHeader } from "../components/layout";
import {
  Button,
  ConfirmDialog,
  EmptyState,
  Field,
  LaneBadge,
  PullToRefresh,
  Select,
  SessionStateChip,
  Sheet,
  SkeletonList,
  TextInput,
} from "../components/ui";
import { useToast } from "../hooks/toast";
import { relativeTime } from "../lib/utils";

function NewSessionSheet({
  open,
  onClose,
  models,
}: {
  open: boolean;
  onClose: () => void;
  models: ModelEntry[];
}) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [modelId, setModelId] = useState("");
  const [repoUrl, setRepoUrl] = useState("");

  // PLAN §2: AirLLM lane is chat-only — never offered for sessions.
  const eligible = useMemo(
    () => models.filter((m) => m.status === "ready" && m.engine !== "airllm"),
    [models],
  );
  const hasAirllmReady = useMemo(
    () => models.some((m) => m.status === "ready" && m.engine === "airllm"),
    [models],
  );

  const create = useMutation({
    mutationFn: () =>
      api.createSession({
        name: name.trim(),
        model_id: Number(modelId),
        repo_url: repoUrl.trim() || undefined,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["sessions"] });
      toast("success", "Session is being created");
      setName("");
      setRepoUrl("");
      onClose();
    },
    onError: (err) => toast("error", errorMessage(err)),
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (name.trim() && modelId) create.mutate();
  };

  return (
    <Sheet open={open} onClose={onClose} title="New session">
      <form onSubmit={submit} className="space-y-4">
        <Field label="Name">
          {(id) => (
            <TextInput
              id={id}
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="fix-the-parser"
              autoFocus
              required
            />
          )}
        </Field>
        <Field
          label="Model"
          helper={
            hasAirllmReady
              ? "AirLLM models are chat-only (seconds per token) and can't power coding sessions."
              : "Only downloaded, ready models can power a session."
          }
        >
          {(id) => (
            <Select
              id={id}
              value={modelId}
              onChange={(e) => setModelId(e.target.value)}
              required
            >
              <option value="" disabled>
                {eligible.length ? "Choose a model…" : "No ready models"}
              </option>
              {eligible.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.display_name} · {m.engine}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Repository URL (optional)" helper="Cloned into the workspace on first boot.">
          {(id) => (
            <TextInput
              id={id}
              type="url"
              value={repoUrl}
              onChange={(e) => setRepoUrl(e.target.value)}
              placeholder="https://github.com/you/project.git"
            />
          )}
        </Field>
        <Button
          type="submit"
          variant="primary"
          className="w-full"
          loading={create.isPending}
          disabled={!name.trim() || !modelId}
        >
          Create session
        </Button>
      </form>
    </Sheet>
  );
}

function SessionCard({
  session,
  modelName,
  engine,
}: {
  session: Session;
  modelName: string | null;
  engine: string | null;
}) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [confirmDelete, setConfirmDelete] = useState(false);

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["sessions"] });

  const stop = useMutation({
    mutationFn: () => api.stopSession(session.id),
    onSuccess: () => void invalidate(),
    onError: (err) => toast("error", errorMessage(err)),
  });
  const start = useMutation({
    mutationFn: () => api.startSession(session.id),
    onSuccess: () => void invalidate(),
    onError: (err) => toast("error", errorMessage(err)),
  });
  const remove = useMutation({
    mutationFn: () => api.deleteSession(session.id),
    onSuccess: () => {
      void invalidate();
      toast("success", `Deleted "${session.name}"`);
      setConfirmDelete(false);
    },
    onError: (err) => toast("error", errorMessage(err)),
  });

  const canStop =
    session.state === "running" || session.state === "creating";
  const canStart = session.state === "stopped" || session.state === "idle";

  return (
    <div className="rounded-xl border border-border bg-surface p-4 transition-colors hover:border-edge">
      <Link
        to={`/sessions/${session.id}`}
        className="block"
        aria-label={`Open session ${session.name}`}
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="truncate text-[15px] font-semibold text-text">
              {session.name}
            </p>
            <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
              {modelName && (
                <span className="inline-flex items-center gap-1">
                  <IconCube size={13} />
                  {modelName}
                </span>
              )}
              {engine && <LaneBadge engine={engine} />}
              {session.repo_url && (
                <span className="inline-flex max-w-48 items-center gap-1 truncate">
                  <IconBranch size={13} />
                  {session.repo_url.replace(/^https?:\/\/(www\.)?/, "")}
                </span>
              )}
            </div>
          </div>
          <SessionStateChip state={session.state} />
        </div>
      </Link>
      {session.state === "error" && session.last_error && (
        <p className="mt-2 rounded-md bg-danger/10 px-2.5 py-1.5 font-mono text-xs break-words text-danger">
          {session.last_error.slice(0, 220)}
        </p>
      )}
      <div className="mt-3 flex items-center justify-between border-t border-border pt-2.5">
        <span className="text-xs text-faint">
          active {relativeTime(session.last_active_at)}
        </span>
        <div className="flex items-center gap-1">
          {canStop && (
            <Button
              size="sm"
              variant="ghost"
              aria-label={`Stop ${session.name}`}
              loading={stop.isPending}
              onClick={() => stop.mutate()}
            >
              <IconStop size={15} />
              Stop
            </Button>
          )}
          {canStart && (
            <Button
              size="sm"
              variant="ghost"
              aria-label={`Start ${session.name}`}
              loading={start.isPending}
              onClick={() => start.mutate()}
            >
              <IconPlay size={15} />
              Start
            </Button>
          )}
          <Button
            size="sm"
            variant="ghost"
            aria-label={`Delete ${session.name}`}
            className="text-danger/80 hover:bg-danger/10 hover:text-danger"
            onClick={() => setConfirmDelete(true)}
          >
            <IconTrash size={15} />
          </Button>
        </div>
      </div>
      <ConfirmDialog
        open={confirmDelete}
        title={`Delete "${session.name}"?`}
        body="The session container and its workspace are removed permanently."
        busy={remove.isPending}
        onCancel={() => setConfirmDelete(false)}
        onConfirm={() => remove.mutate()}
      />
    </div>
  );
}

export default function Sessions() {
  const [sheetOpen, setSheetOpen] = useState(false);

  const sessions = useQuery({
    queryKey: ["sessions"],
    queryFn: api.listSessions,
    refetchInterval: 15000,
  });
  const models = useQuery({ queryKey: ["models"], queryFn: api.listModels });

  const modelById = useMemo(() => {
    const map = new Map<number, ModelEntry>();
    for (const m of models.data ?? []) map.set(m.id, m);
    return map;
  }, [models.data]);

  return (
    <PullToRefresh
      onRefresh={() => void sessions.refetch()}
      refreshing={sessions.isRefetching && !sessions.isLoading}
    >
      <PageHeader
        title="Sessions"
        subtitle="Sandboxed agent coding containers"
      />

      {sessions.isLoading && <SkeletonList rows={3} />}

      {sessions.isError && (
        <EmptyState
          icon="search"
          title="Couldn't load sessions"
          hint={errorMessage(sessions.error)}
          action={<Button onClick={() => void sessions.refetch()}>Retry</Button>}
        />
      )}

      {sessions.data && sessions.data.length === 0 && (
        <EmptyState
          icon="box"
          title="No sessions yet"
          hint="Spin up a sandboxed coding agent against a local model."
          action={
            <Button variant="primary" onClick={() => setSheetOpen(true)}>
              <IconPlus size={16} />
              New session
            </Button>
          }
        />
      )}

      {sessions.data && sessions.data.length > 0 && (
        <ul className="space-y-3">
          {sessions.data.map((s) => {
            const model = s.model_id != null ? modelById.get(s.model_id) : undefined;
            return (
              <li key={s.id}>
                <SessionCard
                  session={s}
                  modelName={model?.display_name ?? null}
                  engine={model?.engine ?? null}
                />
              </li>
            );
          })}
        </ul>
      )}

      {/* Floating action button */}
      <button
        type="button"
        aria-label="New session"
        onClick={() => setSheetOpen(true)}
        className="fixed right-5 bottom-24 z-20 flex h-14 w-14 cursor-pointer items-center justify-center rounded-full bg-accent text-on-accent shadow-lg shadow-accent/25 transition-transform hover:scale-105 active:scale-95 md:right-10 md:bottom-10"
        style={{ marginBottom: "env(safe-area-inset-bottom, 0px)" }}
      >
        <IconPlus size={24} />
      </button>

      <NewSessionSheet
        open={sheetOpen}
        onClose={() => setSheetOpen(false)}
        models={models.data ?? []}
      />
    </PullToRefresh>
  );
}
