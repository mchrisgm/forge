import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, ApiError, errorMessage } from "../api/client";
import { IconChevronLeft } from "../components/icons";
import ChatTab from "../components/session/ChatTab";
import FilesTab from "../components/session/FilesTab";
import GitTab from "../components/session/GitTab";
import TasksTab from "../components/session/TasksTab";
import {
  Button,
  EmptyState,
  LaneBadge,
  SegmentedTabs,
  SessionStateChip,
  SkeletonList,
} from "../components/ui";

const TABS = [
  { id: "chat", label: "Chat" },
  { id: "files", label: "Files" },
  { id: "git", label: "Git" },
  { id: "tasks", label: "Tasks" },
] as const;

type TabId = (typeof TABS)[number]["id"];

export default function SessionDetail() {
  const { sessionId = "" } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const rawTab = searchParams.get("tab");
  const tab: TabId = TABS.some((t) => t.id === rawTab)
    ? (rawTab as TabId)
    : "chat";

  const session = useQuery({
    queryKey: ["session", sessionId],
    queryFn: () => api.getSession(sessionId),
    refetchInterval: (query) =>
      query.state.data?.state === "creating" ? 3000 : 15000,
    retry: (count, error) =>
      !(error instanceof ApiError && error.status === 404) && count < 2,
  });

  const models = useQuery({ queryKey: ["models"], queryFn: api.listModels });
  const model = useMemo(
    () =>
      models.data?.find((m) => m.id === session.data?.model_id) ?? null,
    [models.data, session.data?.model_id],
  );

  if (session.isLoading) {
    return (
      <div className="pt-6">
        <SkeletonList rows={4} />
      </div>
    );
  }

  if (session.isError || !session.data) {
    return (
      <div className="pt-6">
        <EmptyState
          icon="search"
          title="Session not found"
          hint={session.error ? errorMessage(session.error) : undefined}
          action={
            <Link to="/sessions">
              <Button>Back to sessions</Button>
            </Link>
          }
        />
      </div>
    );
  }

  const s = session.data;

  return (
    <div className="flex min-h-dvh flex-col">
      <header className="sticky top-0 z-10 -mx-4 border-b border-border bg-bg/95 px-4 pt-safe backdrop-blur md:mx-0 md:border-none md:bg-transparent md:backdrop-blur-none">
        <div className="flex items-center gap-2 py-3">
          <Link
            to="/sessions"
            aria-label="Back to sessions"
            className="-ml-2 flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-muted hover:bg-raised hover:text-text"
          >
            <IconChevronLeft size={20} />
          </Link>
          <div className="min-w-0 flex-1">
            <h1 className="truncate text-base font-bold text-text">{s.name}</h1>
            <div className="flex items-center gap-2 text-xs text-muted">
              {model && (
                <>
                  <span className="truncate">{model.display_name}</span>
                  <LaneBadge engine={model.engine} />
                </>
              )}
            </div>
          </div>
          <SessionStateChip state={s.state} />
        </div>
        <div className="pb-3">
          <SegmentedTabs
            tabs={TABS}
            value={tab}
            onChange={(next) =>
              setSearchParams({ tab: next }, { replace: true })
            }
          />
        </div>
      </header>

      <div className="flex-1 pt-4">
        {tab === "chat" && <ChatTab session={s} model={model} />}
        {tab === "files" && <FilesTab session={s} />}
        {tab === "git" && <GitTab session={s} />}
        {tab === "tasks" && <TasksTab session={s} />}
      </div>
    </div>
  );
}
