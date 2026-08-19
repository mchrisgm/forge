import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, errorMessage } from "../../api/client";
import type { Session } from "../../api/types";
import { useToast } from "../../hooks/toast";
import { cx, relativeTime } from "../../lib/utils";
import { IconBranch } from "../icons";
import { Button, Chip, EmptyState, SkeletonList, TextInput } from "../ui";

const STATUS_COLORS: Record<string, string> = {
  M: "text-warn",
  A: "text-ok",
  D: "text-danger",
  R: "text-info",
  "??": "text-faint",
};

function diffLineClass(line: string): string {
  if (line.startsWith("+++") || line.startsWith("---")) return "diff-line-meta";
  if (line.startsWith("@@")) return "diff-line-hunk";
  if (line.startsWith("+")) return "diff-line-add";
  if (line.startsWith("-")) return "diff-line-del";
  if (line.startsWith("diff ") || line.startsWith("index "))
    return "diff-line-meta";
  return "";
}

export default function GitTab({ session }: { session: Session }) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [message, setMessage] = useState("");
  const running = session.state === "running";

  const status = useQuery({
    queryKey: ["git-status", session.id],
    queryFn: () => api.gitStatus(session.id),
    enabled: running,
    retry: false,
  });
  const diff = useQuery({
    queryKey: ["git-diff", session.id],
    queryFn: () => api.gitDiff(session.id),
    enabled: running,
    retry: false,
  });
  const log = useQuery({
    queryKey: ["git-log", session.id],
    queryFn: () => api.gitLog(session.id),
    enabled: running,
    retry: false,
  });

  const refreshAll = () => {
    void queryClient.invalidateQueries({ queryKey: ["git-status", session.id] });
    void queryClient.invalidateQueries({ queryKey: ["git-diff", session.id] });
    void queryClient.invalidateQueries({ queryKey: ["git-log", session.id] });
  };

  const commit = useMutation({
    mutationFn: () => api.gitCommit(session.id, message.trim()),
    onSuccess: (res) => {
      toast("success", res.output.split("\n")[0] || "Committed");
      setMessage("");
      refreshAll();
    },
    onError: (err) => toast("error", errorMessage(err)),
  });

  const push = useMutation({
    mutationFn: () => api.gitPush(session.id),
    onSuccess: (res) =>
      toast("success", res.output.split("\n")[0] || "Pushed to remote"),
    onError: (err) => toast("error", errorMessage(err)),
  });

  if (!running) {
    return (
      <EmptyState
        icon="box"
        title="Session is not running"
        hint="Git commands run inside the container — start the session first."
      />
    );
  }

  if (status.isLoading) return <SkeletonList rows={3} />;

  if (status.isError) {
    return (
      <EmptyState
        icon="search"
        title="Not a git repository"
        hint={errorMessage(status.error)}
        action={<Button onClick={() => void status.refetch()}>Retry</Button>}
      />
    );
  }

  const changes = status.data?.changes ?? [];

  return (
    <div className="space-y-5">
      {/* Branch + changes */}
      <section className="rounded-xl border border-border bg-surface p-4">
        <div className="flex items-center justify-between gap-2">
          <p className="flex min-w-0 items-center gap-2 font-mono text-sm text-text">
            <IconBranch size={15} className="shrink-0 text-accent/80" />
            <span className="truncate">{status.data?.branch || "(no branch)"}</span>
          </p>
          <Button
            size="sm"
            variant="secondary"
            loading={push.isPending}
            onClick={() => push.mutate()}
          >
            Push
          </Button>
        </div>

        {changes.length === 0 ? (
          <p className="mt-3 text-sm text-muted">Working tree clean.</p>
        ) : (
          <ul className="mt-3 space-y-1.5">
            {changes.map((c) => (
              <li
                key={`${c.status}-${c.path}`}
                className="flex items-center gap-2 font-mono text-xs"
              >
                <Chip
                  color={STATUS_COLORS[c.status.charAt(0)] ?? STATUS_COLORS[c.status] ?? "text-faint"}
                >
                  {c.status}
                </Chip>
                <span className="min-w-0 truncate text-text/90">{c.path}</span>
              </li>
            ))}
          </ul>
        )}

        {/* Commit box */}
        <div className="mt-4 flex gap-2">
          <label htmlFor="commit-msg" className="sr-only">
            Commit message
          </label>
          <TextInput
            id="commit-msg"
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            placeholder="Commit message"
            onKeyDown={(e) => {
              if (e.key === "Enter" && message.trim()) commit.mutate();
            }}
          />
          <Button
            variant="primary"
            className="shrink-0"
            disabled={!message.trim() || changes.length === 0}
            loading={commit.isPending}
            onClick={() => commit.mutate()}
          >
            Commit
          </Button>
        </div>
      </section>

      {/* Diff */}
      <section>
        <h2 className="mb-2 text-sm font-semibold text-muted">Diff</h2>
        {diff.isLoading && <SkeletonList rows={2} />}
        {diff.isError && (
          <p className="text-sm text-danger">{errorMessage(diff.error)}</p>
        )}
        {diff.data &&
          (diff.data.diff.trim() ? (
            <div className="overflow-x-auto rounded-xl border border-border bg-[#0d1017]">
              <pre className="min-w-max px-0 py-2 font-mono text-xs leading-relaxed">
                {diff.data.diff.split("\n").map((line, i) => (
                  <div key={i} className={cx("px-3", diffLineClass(line))}>
                    {line || " "}
                  </div>
                ))}
              </pre>
            </div>
          ) : (
            <p className="text-sm text-muted">No unstaged changes.</p>
          ))}
      </section>

      {/* Log */}
      <section>
        <h2 className="mb-2 text-sm font-semibold text-muted">History</h2>
        {log.isLoading && <SkeletonList rows={2} />}
        {log.data && log.data.length === 0 && (
          <p className="text-sm text-muted">No commits yet.</p>
        )}
        {log.data && log.data.length > 0 && (
          <ul className="divide-y divide-border overflow-hidden rounded-xl border border-border bg-surface">
            {log.data.map((c) => (
              <li key={c.hash} className="flex items-center gap-3 px-3.5 py-2.5">
                <code className="shrink-0 rounded bg-overlay px-1.5 py-0.5 font-mono text-[11px] text-accent/90">
                  {c.hash.slice(0, 7)}
                </code>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm text-text">{c.subject}</p>
                  <p className="text-xs text-faint">
                    {c.author} · {relativeTime(c.date)}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
