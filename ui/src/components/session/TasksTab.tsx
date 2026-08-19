import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, errorMessage } from "../../api/client";
import type { Session, Task, ThinkingLevel } from "../../api/types";
import { useToast } from "../../hooks/toast";
import { formatDuration, relativeTime } from "../../lib/utils";
import { IconSend } from "../icons";
import { ThinkingSelect } from "../ThinkingSelect";
import {
  Button,
  Collapsible,
  EmptyState,
  SkeletonList,
  TaskStateChip,
  TextArea,
} from "../ui";

function TaskCard({ task }: { task: Task }) {
  return (
    <div className="rounded-xl border border-border bg-surface p-4">
      <div className="flex items-start justify-between gap-3">
        <p className="min-w-0 flex-1 text-sm break-words text-text">
          {task.prompt}
        </p>
        <TaskStateChip state={task.state} />
      </div>
      <div className="mt-2 flex items-center gap-3 text-xs text-faint">
        <span>#{task.id}</span>
        <span>{relativeTime(task.created_at)}</span>
        {(task.state === "done" || task.state === "failed") && (
          <span>took {formatDuration(task.created_at, task.finished_at)}</span>
        )}
        {task.state === "running" && (
          <span>running {formatDuration(task.created_at, null)}</span>
        )}
      </div>
      {task.result && (
        <div className="mt-2 border-t border-border pt-2">
          <Collapsible summary={task.state === "failed" ? "Error details" : "Result"}>
            <pre className="max-h-64 overflow-auto rounded-md border border-border bg-bg px-3 py-2 font-mono text-xs leading-relaxed whitespace-pre-wrap text-muted">
              {task.result}
            </pre>
          </Collapsible>
        </div>
      )}
    </div>
  );
}

export default function TasksTab({ session }: { session: Session }) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [prompt, setPrompt] = useState("");
  const [thinking, setThinking] = useState<ThinkingLevel>("auto");

  const tasks = useQuery({
    queryKey: ["tasks", session.id],
    queryFn: () => api.listSessionTasks(session.id),
    refetchInterval: 10000,
  });

  const create = useMutation({
    mutationFn: () => api.createTask(session.id, prompt.trim(), thinking),
    onSuccess: () => {
      setPrompt("");
      toast("success", "Task queued — it runs in the background");
      void queryClient.invalidateQueries({ queryKey: ["tasks", session.id] });
    },
    onError: (err) => toast("error", errorMessage(err)),
  });

  return (
    <div className="space-y-4">
      {/* Composer */}
      <div className="rounded-xl border border-border bg-surface p-4">
        <label
          htmlFor="task-prompt"
          className="mb-2 block text-sm font-medium text-muted"
        >
          Fire-and-forget task
        </label>
        <TextArea
          id="task-prompt"
          rows={2}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="e.g. Add unit tests for the parser module and run them"
        />
        <div className="mt-2.5 flex items-center justify-between gap-3">
          <p className="min-w-0 flex-1 text-xs text-faint">
            Runs in its own agent turn; watch progress here.
          </p>
          <div className="flex shrink-0 items-center gap-2">
            <ThinkingSelect
              value={thinking}
              onChange={setThinking}
              direction="down"
            />
            <Button
              variant="primary"
              size="sm"
              disabled={!prompt.trim()}
              loading={create.isPending}
              onClick={() => create.mutate()}
            >
              <IconSend size={14} />
              Run task
            </Button>
          </div>
        </div>
      </div>

      {tasks.isLoading && <SkeletonList rows={3} />}

      {tasks.isError && (
        <EmptyState
          icon="search"
          title="Couldn't load tasks"
          hint={errorMessage(tasks.error)}
          action={<Button onClick={() => void tasks.refetch()}>Retry</Button>}
        />
      )}

      {tasks.data && tasks.data.length === 0 && (
        <EmptyState
          icon="spark"
          title="No tasks yet"
          hint="Queue a prompt above and the agent will work on it while you do something else."
        />
      )}

      {tasks.data && tasks.data.length > 0 && (
        <ul className="space-y-3">
          {tasks.data.map((task) => (
            <li key={task.id}>
              <TaskCard task={task} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
