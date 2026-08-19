import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type ComponentType } from "react";
import { api, errorMessage } from "../api/client";
import type { Connector, ConnectorKind } from "../api/types";
import {
  IconBrowser,
  IconGitHub,
  IconGlobe,
  IconSearch,
  IconSparkles,
} from "../components/icons";
import { PageHeader } from "../components/layout";
import {
  Button,
  EmptyState,
  SkeletonList,
  TextInput,
  Toggle,
} from "../components/ui";
import { useToast } from "../hooks/toast";

const CONNECTOR_META: Record<
  ConnectorKind,
  { title: string; description: string; icon: ComponentType<{ size?: number }> }
> = {
  github: {
    title: "GitHub",
    description:
      "Lets sessions read repos, issues and PRs through the GitHub MCP server. Needs a personal access token.",
    icon: IconGitHub,
  },
  fetch: {
    title: "Fetch",
    description:
      "Plain HTTP fetching for the agent — read documentation pages, APIs and raw files.",
    icon: IconGlobe,
  },
  searxng: {
    title: "Web search",
    description:
      "Self-hosted SearXNG metasearch. The agent can research libraries, errors and current events without third-party keys.",
    icon: IconSearch,
  },
  playwright: {
    title: "Browser",
    description:
      "Headless Playwright automation — open pages, click, screenshot. Useful for end-to-end checks.",
    icon: IconBrowser,
  },
  skills: {
    title: "Skills",
    description:
      "Exposes installed skills to sessions via list_skills / load_skill (progressive disclosure).",
    icon: IconSparkles,
  },
};

const KIND_ORDER: ConnectorKind[] = [
  "github",
  "searxng",
  "fetch",
  "playwright",
  "skills",
];

function GithubTokenForm({ connector }: { connector: Connector }) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [token, setTokenValue] = useState("");

  const save = useMutation({
    mutationFn: (value: string) =>
      api.patchConnector("github", { config: { token: value } }),
    onSuccess: (_res, value) => {
      toast("success", value ? "GitHub token saved" : "GitHub token cleared");
      setTokenValue("");
      void queryClient.invalidateQueries({ queryKey: ["connectors"] });
    },
    onError: (err) => toast("error", errorMessage(err)),
  });

  return (
    <div className="mt-3 border-t border-border pt-3">
      <label
        htmlFor="github-pat"
        className="mb-1.5 block text-sm font-medium text-muted"
      >
        Personal access token
      </label>
      <div className="flex gap-2">
        <TextInput
          id="github-pat"
          type="password"
          autoComplete="off"
          value={token}
          onChange={(e) => setTokenValue(e.target.value)}
          placeholder={connector.has_token ? "••••••••••••" : "ghp_…"}
        />
        <Button
          className="shrink-0"
          variant="primary"
          disabled={!token}
          loading={save.isPending && save.variables !== ""}
          onClick={() => save.mutate(token)}
        >
          Save
        </Button>
        {connector.has_token && (
          <Button
            className="shrink-0"
            variant="ghost"
            loading={save.isPending && save.variables === ""}
            onClick={() => save.mutate("")}
          >
            Clear
          </Button>
        )}
      </div>
      <p className="mt-1.5 text-xs text-faint">
        Applies to NEW sessions only — running containers keep the token they
        started with.
      </p>
    </div>
  );
}

function ConnectorCard({ connector }: { connector: Connector }) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const meta = CONNECTOR_META[connector.kind];
  const Icon = meta.icon;

  const toggle = useMutation({
    mutationFn: (enabled: boolean) =>
      api.patchConnector(connector.kind, { enabled }),
    onMutate: async (enabled) => {
      await queryClient.cancelQueries({ queryKey: ["connectors"] });
      const previous = queryClient.getQueryData<Connector[]>(["connectors"]);
      queryClient.setQueryData<Connector[]>(["connectors"], (old) =>
        (old ?? []).map((c) =>
          c.kind === connector.kind ? { ...c, enabled } : c,
        ),
      );
      return { previous };
    },
    onError: (err, _vars, ctx) => {
      if (ctx?.previous)
        queryClient.setQueryData(["connectors"], ctx.previous);
      toast("error", errorMessage(err));
    },
    onSettled: () =>
      void queryClient.invalidateQueries({ queryKey: ["connectors"] }),
  });

  return (
    <div className="rounded-xl border border-border bg-surface p-4">
      <div className="flex items-start gap-3">
        <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-raised text-muted">
          <Icon size={18} />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-text">{meta.title}</p>
          <p className="mt-0.5 text-sm text-muted">{meta.description}</p>
        </div>
        <Toggle
          checked={connector.enabled}
          onChange={(next) => toggle.mutate(next)}
          label={`${connector.enabled ? "Disable" : "Enable"} ${meta.title} connector`}
        />
      </div>
      {connector.kind === "github" && <GithubTokenForm connector={connector} />}
    </div>
  );
}

export default function Connectors() {
  const connectors = useQuery({
    queryKey: ["connectors"],
    queryFn: api.listConnectors,
  });

  const ordered = (connectors.data ?? [])
    .slice()
    .sort(
      (a, b) => KIND_ORDER.indexOf(a.kind) - KIND_ORDER.indexOf(b.kind),
    );

  return (
    <div>
      <PageHeader
        title="Connectors"
        subtitle="MCP tools injected into every new session"
      />
      {connectors.isLoading && <SkeletonList rows={4} />}
      {connectors.isError && (
        <EmptyState
          icon="search"
          title="Couldn't load connectors"
          hint={errorMessage(connectors.error)}
          action={
            <Button onClick={() => void connectors.refetch()}>Retry</Button>
          }
        />
      )}
      {connectors.data && (
        <ul className="space-y-3">
          {ordered.map((c) => (
            <li key={c.kind}>
              <ConnectorCard connector={c} />
            </li>
          ))}
        </ul>
      )}
      {connectors.data && (
        <p className="mt-4 text-center text-xs text-faint">
          Connector changes apply when a session container is (re)created.
        </p>
      )}
    </div>
  );
}
