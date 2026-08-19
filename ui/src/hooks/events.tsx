// Global SSE feed: a single EventSource on /api/events/stream that
// invalidates react-query caches per event kind, tracks download progress,
// and surfaces notable transitions as toasts.

import { useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import type { DownloadProgress, ForgeEvent } from "../api/types";
import { getToken } from "../lib/auth";
import { useToast } from "./toast";

interface EventsContextValue {
  connected: boolean;
  downloads: Record<number, DownloadProgress>;
}

const EventsContext = createContext<EventsContextValue>({
  connected: true,
  downloads: {},
});

export function useGlobalEvents(): EventsContextValue {
  return useContext(EventsContext);
}

const MAX_BACKOFF_MS = 15000;

export function EventsProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [connected, setConnected] = useState(true);
  const [downloads, setDownloads] = useState<Record<number, DownloadProgress>>(
    {},
  );
  const engineStateRef = useRef<string | null>(null);

  useEffect(() => {
    let source: EventSource | null = null;
    let retryTimer: number | undefined;
    let backoff = 1000;
    let disposed = false;

    const invalidate = (...keys: string[]) => {
      for (const key of keys) {
        void queryClient.invalidateQueries({ queryKey: [key] });
      }
    };

    const handle = (event: ForgeEvent) => {
      switch (event.kind) {
        case "engine.state": {
          invalidate("engines", "system");
          const lease = event.lease;
          const state = lease?.state ?? "released";
          const stamp = `${lease?.model_id ?? "none"}:${state}`;
          if (engineStateRef.current !== stamp) {
            engineStateRef.current = stamp;
            if (state === "ready" && lease) {
              toast("success", `${lease.model_name} is loaded and ready`);
            } else if (state === "failed" && lease) {
              toast("error", `Engine load failed: ${lease.model_name}`);
            }
          }
          break;
        }
        case "download.started":
          invalidate("models");
          break;
        case "download.progress":
          if (typeof event.model_id === "number") {
            const progress: DownloadProgress = {
              model_id: event.model_id,
              downloaded_gb: event.downloaded_gb ?? 0,
              total_gb: event.total_gb ?? null,
              pct: event.pct ?? null,
            };
            setDownloads((prev) => ({ ...prev, [progress.model_id]: progress }));
          }
          break;
        case "download.done":
          if (typeof event.model_id === "number") {
            setDownloads((prev) => {
              const next = { ...prev };
              delete next[event.model_id as number];
              return next;
            });
          }
          invalidate("models");
          toast("success", "Model download complete");
          break;
        case "download.failed":
          if (typeof event.model_id === "number") {
            setDownloads((prev) => {
              const next = { ...prev };
              delete next[event.model_id as number];
              return next;
            });
          }
          invalidate("models");
          toast(
            "error",
            `Download failed${event.error ? `: ${String(event.error).slice(0, 120)}` : ""}`,
          );
          break;
        case "session.state":
          invalidate("sessions", "session", "system");
          break;
        case "session.deleted":
          invalidate("sessions", "system");
          break;
        case "task.state":
          invalidate("tasks");
          if (event.state === "done") toast("success", "Task finished");
          else if (event.state === "failed") toast("error", "Task failed");
          break;
        case "skill.installed":
          invalidate("skills");
          break;
        case "skill.removed":
          invalidate("skills");
          break;
        case "registry.scan_done":
          invalidate("suggestions");
          if ((event.new_suggestions ?? 0) > 0) {
            toast("info", `${event.new_suggestions} new model suggestion(s)`);
          }
          break;
        case "model.deleted":
          invalidate("models");
          break;
        case "suggestion.approved":
          invalidate("suggestions", "models");
          break;
        default:
          break;
      }
    };

    const connect = () => {
      if (disposed) return;
      const token = getToken();
      if (!token) return;
      source = new EventSource(
        `/api/events/stream?token=${encodeURIComponent(token)}`,
      );
      source.onopen = () => {
        backoff = 1000;
        setConnected(true);
      };
      source.onmessage = (msg) => {
        try {
          handle(JSON.parse(msg.data) as ForgeEvent);
        } catch {
          // malformed frame — ignore
        }
      };
      source.onerror = () => {
        source?.close();
        source = null;
        setConnected(false);
        retryTimer = window.setTimeout(() => {
          backoff = Math.min(backoff * 2, MAX_BACKOFF_MS);
          connect();
        }, backoff);
      };
    };

    connect();
    return () => {
      disposed = true;
      source?.close();
      if (retryTimer) window.clearTimeout(retryTimer);
    };
  }, [queryClient, toast]);

  const value = useMemo(
    () => ({ connected, downloads }),
    [connected, downloads],
  );
  return (
    <EventsContext.Provider value={value}>{children}</EventsContext.Provider>
  );
}
