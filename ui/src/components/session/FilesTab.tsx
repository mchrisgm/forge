import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import { api, ApiError, errorMessage } from "../../api/client";
import type { Session } from "../../api/types";
import { useToast } from "../../hooks/toast";
import { cx, formatBytes } from "../../lib/utils";
import {
  IconChevronRight,
  IconEdit,
  IconFile,
  IconFolder,
  IconX,
} from "../icons";
import { Button, EmptyState, SkeletonList, TextArea } from "../ui";

function Breadcrumbs({
  path,
  onNavigate,
}: {
  path: string;
  onNavigate: (next: string) => void;
}) {
  const segments = path.split("/").filter(Boolean);
  return (
    <nav aria-label="Breadcrumb" className="mb-3 overflow-x-auto">
      <ol className="flex items-center gap-1 text-sm whitespace-nowrap">
        <li>
          <button
            type="button"
            onClick={() => onNavigate("")}
            className={cx(
              "min-h-9 cursor-pointer rounded px-1.5 font-mono",
              segments.length ? "text-info hover:underline" : "text-text",
            )}
          >
            workspace
          </button>
        </li>
        {segments.map((segment, i) => {
          const target = segments.slice(0, i + 1).join("/");
          const last = i === segments.length - 1;
          return (
            <Fragment key={target}>
              <IconChevronRight size={13} className="shrink-0 text-faint" />
              <li>
                {last ? (
                  <span className="px-1.5 font-mono text-text">{segment}</span>
                ) : (
                  <button
                    type="button"
                    onClick={() => onNavigate(target)}
                    className="min-h-9 cursor-pointer rounded px-1.5 font-mono text-info hover:underline"
                  >
                    {segment}
                  </button>
                )}
              </li>
            </Fragment>
          );
        })}
      </ol>
    </nav>
  );
}

function FileViewer({
  session,
  path,
  onClose,
}: {
  session: Session;
  path: string;
  onClose: () => void;
}) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");

  const file = useQuery({
    queryKey: ["file", session.id, path],
    queryFn: () => api.readFile(session.id, path),
    retry: false,
  });

  const save = useMutation({
    mutationFn: () => api.writeFile(session.id, path, draft),
    onSuccess: () => {
      toast("success", "File saved");
      setEditing(false);
      void queryClient.invalidateQueries({
        queryKey: ["file", session.id, path],
      });
      void queryClient.invalidateQueries({ queryKey: ["files", session.id] });
    },
    onError: (err) => toast("error", errorMessage(err)),
  });

  const tooLarge = file.error instanceof ApiError && file.error.status === 413;
  const lines = file.data ? file.data.content.split("\n") : [];

  return (
    <div className="rounded-xl border border-border bg-surface">
      <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-2">
        <p className="min-w-0 truncate font-mono text-xs text-muted">{path}</p>
        <div className="flex shrink-0 items-center gap-1">
          {file.data && !editing && (
            <Button
              size="sm"
              variant="ghost"
              aria-label="Edit file"
              onClick={() => {
                setDraft(file.data.content);
                setEditing(true);
              }}
            >
              <IconEdit size={14} />
              Edit
            </Button>
          )}
          {editing && (
            <>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setEditing(false)}
                disabled={save.isPending}
              >
                Cancel
              </Button>
              <Button
                size="sm"
                variant="primary"
                loading={save.isPending}
                onClick={() => save.mutate()}
              >
                Save
              </Button>
            </>
          )}
          <Button size="sm" variant="ghost" aria-label="Close file" onClick={onClose}>
            <IconX size={15} />
          </Button>
        </div>
      </div>

      {file.isLoading && (
        <div className="p-3">
          <SkeletonList rows={2} />
        </div>
      )}

      {tooLarge && (
        <p className="px-4 py-6 text-center text-sm text-muted">
          This file is too large to view here (&gt;2 MB). Open it inside the
          session instead.
        </p>
      )}

      {file.isError && !tooLarge && (
        <p className="px-4 py-6 text-center text-sm text-danger">
          {errorMessage(file.error)}
        </p>
      )}

      {file.data && editing && (
        <div className="p-2">
          <TextArea
            aria-label={`Edit ${path}`}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={Math.min(Math.max(draft.split("\n").length + 1, 8), 28)}
            spellCheck={false}
            className="font-mono text-xs leading-relaxed"
          />
        </div>
      )}

      {file.data && !editing && (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse font-mono text-xs leading-relaxed">
            <tbody>
              {lines.map((line, i) => (
                <tr key={i}>
                  <td
                    aria-hidden
                    className="w-10 min-w-10 border-r border-border px-2 py-0 text-right align-top text-faint select-none"
                  >
                    {i + 1}
                  </td>
                  <td className="px-3 py-0 whitespace-pre text-text/90">
                    {line || " "}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default function FilesTab({ session }: { session: Session }) {
  const [path, setPath] = useState("");
  const [openFile, setOpenFile] = useState<string | null>(null);

  const listing = useQuery({
    queryKey: ["files", session.id, path],
    queryFn: () => api.listFiles(session.id, path),
    enabled: session.state === "running",
    retry: false,
  });

  if (session.state !== "running") {
    return (
      <EmptyState
        icon="box"
        title="Session is not running"
        hint="Files are read from inside the container — start the session to browse them."
      />
    );
  }

  return (
    <div>
      <Breadcrumbs
        path={path}
        onNavigate={(next) => {
          setPath(next);
          setOpenFile(null);
        }}
      />

      {openFile && (
        <div className="mb-4">
          <FileViewer
            session={session}
            path={openFile}
            onClose={() => setOpenFile(null)}
          />
        </div>
      )}

      {listing.isLoading && <SkeletonList rows={4} />}

      {listing.isError && (
        <EmptyState
          icon="search"
          title="Couldn't list this directory"
          hint={errorMessage(listing.error)}
          action={<Button onClick={() => void listing.refetch()}>Retry</Button>}
        />
      )}

      {listing.data && listing.data.entries.length === 0 && (
        <EmptyState
          icon="box"
          title="Empty directory"
          hint="Nothing here yet — ask the agent to create something."
        />
      )}

      {listing.data && listing.data.entries.length > 0 && (
        <ul className="divide-y divide-border overflow-hidden rounded-xl border border-border bg-surface">
          {listing.data.entries.map((entry) => {
            const target = path ? `${path}/${entry.name}` : entry.name;
            const isDir = entry.type === "dir";
            return (
              <li key={entry.name}>
                <button
                  type="button"
                  onClick={() =>
                    isDir ? setPath(target) : setOpenFile(target)
                  }
                  className="flex min-h-11 w-full cursor-pointer items-center gap-2.5 px-3.5 text-left hover:bg-raised"
                >
                  {isDir ? (
                    <IconFolder size={16} className="shrink-0 text-accent/80" />
                  ) : (
                    <IconFile size={16} className="shrink-0 text-muted" />
                  )}
                  <span className="min-w-0 flex-1 truncate font-mono text-sm text-text">
                    {entry.name}
                    {isDir && "/"}
                  </span>
                  {!isDir && (
                    <span className="shrink-0 text-xs text-faint">
                      {formatBytes(entry.size)}
                    </span>
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
