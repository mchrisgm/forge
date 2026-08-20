// Connector catalog — GET /api/connectors returns grouped catalog entries
// (core / productivity / developer / design / business) plus user-defined
// custom MCP servers. Cards expose an enable toggle, a dynamic credential
// form driven by auth_fields, and (for custom rows) delete.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState, type ComponentType } from "react";
import { Link } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import type {
  Connector,
  ConnectorAuthField,
  ConnectorCategory,
  McpType,
  OAuthDeviceStart,
} from "../api/types";
import {
  IconActivity,
  IconBrowser,
  IconCheck,
  IconChevronDown,
  IconChevronRight,
  IconCopy,
  IconEdit,
  IconExternal,
  IconGitHub,
  IconGlobe,
  IconPlus,
  IconSearch,
  IconSparkles,
  IconTerminal,
  IconTrash,
  IconWrench,
  IconX,
} from "../components/icons";
import { PageHeader } from "../components/layout";
import {
  Button,
  Chip,
  ConfirmDialog,
  EmptyState,
  Field,
  SegmentedTabs,
  Sheet,
  SkeletonList,
  Spinner,
  TextInput,
  Toggle,
} from "../components/ui";
import { useToast } from "../hooks/toast";
import { rememberPendingOAuth } from "../lib/oauth";
import { cx } from "../lib/utils";

const MASK = "••••••";

const CATEGORY_ORDER: ConnectorCategory[] = [
  "core",
  "productivity",
  "developer",
  "design",
  "business",
  "custom",
];

const CATEGORY_LABELS: Record<ConnectorCategory, string> = {
  core: "Core",
  productivity: "Productivity",
  developer: "Developer",
  design: "Design",
  business: "Business",
  custom: "Custom",
};

type IconComponent = ComponentType<{ size?: number; className?: string }>;

const KIND_ICONS: Record<string, IconComponent> = {
  github: IconGitHub,
  searxng: IconSearch,
  fetch: IconGlobe,
  playwright: IconBrowser,
  skills: IconSparkles,
};

const CATEGORY_ICONS: Record<ConnectorCategory, IconComponent> = {
  core: IconGlobe,
  productivity: IconSparkles,
  developer: IconTerminal,
  design: IconEdit,
  business: IconActivity,
  custom: IconWrench,
};

function connectorIcon(connector: Connector): IconComponent {
  return (
    KIND_ICONS[connector.kind] ??
    CATEGORY_ICONS[connector.category] ??
    IconWrench
  );
}

function McpTypeBadge({ type }: { type: McpType }) {
  return (
    <span
      className={cx(
        "inline-flex items-center rounded border px-1.5 py-0.5 font-mono text-[11px] font-medium",
        type === "remote"
          ? "border-info/35 bg-info/10 text-info"
          : "border-lane-airllm/35 bg-lane-airllm/10 text-lane-airllm",
      )}
    >
      {type}
    </span>
  );
}

// ── Credential / config form (driven by auth_fields) ────────────────────────

function ConfigForm({ connector }: { connector: Connector }) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const initial = useMemo(
    () =>
      Object.fromEntries(connector.auth_fields.map((f) => [f.key, f.value])),
    [connector.auth_fields],
  );
  const [values, setValues] = useState<Record<string, string>>(initial);
  const dirty = connector.auth_fields.some(
    (f) => (values[f.key] ?? "") !== (initial[f.key] ?? ""),
  );

  const invalidate = () =>
    void queryClient.invalidateQueries({ queryKey: ["connectors"] });

  const save = useMutation({
    mutationFn: () => api.patchConnector(connector.kind, { config: values }),
    onSuccess: (updated) => {
      toast("success", `${connector.name} settings saved`);
      setValues(
        Object.fromEntries(updated.auth_fields.map((f) => [f.key, f.value])),
      );
      invalidate();
    },
    onError: (err) => toast("error", errorMessage(err)),
  });

  // Per-field clear: sending "" removes the stored value.
  const clear = useMutation({
    mutationFn: (key: string) =>
      api.patchConnector(connector.kind, { config: { [key]: "" } }),
    onSuccess: (_res, key) => {
      toast("success", "Value cleared");
      setValues((v) => ({ ...v, [key]: "" }));
      invalidate();
    },
    onError: (err) => toast("error", errorMessage(err)),
  });

  const fieldRow = (f: ConnectorAuthField) => (
    <Field
      key={f.key}
      label={f.label}
      helper={
        f.secret && f.configured ? "Configured — leave the mask to keep it." : undefined
      }
    >
      {(id) => (
        <div className="flex gap-2">
          <TextInput
            id={id}
            type={f.secret ? "password" : "text"}
            autoComplete="off"
            value={values[f.key] ?? ""}
            onChange={(e) =>
              setValues((v) => ({ ...v, [f.key]: e.target.value }))
            }
            placeholder={f.placeholder || (f.secret ? MASK : "")}
          />
          {f.secret && f.configured && (
            <Button
              className="shrink-0"
              variant="ghost"
              aria-label={`Clear ${f.label}`}
              loading={clear.isPending && clear.variables === f.key}
              onClick={() => clear.mutate(f.key)}
            >
              Clear
            </Button>
          )}
        </div>
      )}
    </Field>
  );

  return (
    <div className="space-y-4 pt-3">
      {connector.oauth.connected && (
        <p className="text-xs text-faint">
          Signed in as {connector.oauth.account || "an OAuth account"} — pasting
          a token here replaces that sign-in.
        </p>
      )}
      {connector.auth_fields.map(fieldRow)}
      {connector.auth_note && (
        <p className="text-xs text-faint">{connector.auth_note}</p>
      )}
      <div className="flex items-center justify-between gap-3">
        {connector.docs_url ? (
          <a
            href={connector.docs_url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex min-h-9 items-center gap-1 text-xs text-info underline underline-offset-2"
          >
            Docs
            <IconExternal size={12} />
          </a>
        ) : (
          <span />
        )}
        {connector.auth_fields.length > 0 && (
          <Button
            size="sm"
            variant="primary"
            disabled={!dirty}
            loading={save.isPending}
            onClick={() => save.mutate()}
          >
            Save
          </Button>
        )}
      </div>
    </div>
  );
}

// ── OAuth sign-in (per-user; device flow modal + code flow redirect) ────────

type DevicePhase =
  | { phase: "starting" }
  | { phase: "waiting"; start: OAuthDeviceStart }
  | { phase: "connected"; account: string }
  | { phase: "error"; message: string };

/** GitHub device flow: show the one-time code, poll until approved. */
function DeviceFlowSheet({
  connector,
  open,
  onClose,
}: {
  connector: Connector;
  open: boolean;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [state, setState] = useState<DevicePhase>({ phase: "starting" });
  const [attempt, setAttempt] = useState(0);
  const [copied, setCopied] = useState(false);

  // Kick off (or retry) the flow whenever the sheet opens.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setState({ phase: "starting" });
    setCopied(false);
    api.oauthStart(connector.kind).then(
      (start) => {
        if (cancelled) return;
        if (start.flow === "device") setState({ phase: "waiting", start });
        else setState({ phase: "error", message: "Unexpected flow type" });
      },
      (err: unknown) => {
        if (!cancelled) setState({ phase: "error", message: errorMessage(err) });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [open, attempt, connector.kind]);

  // Poll every `interval` seconds while waiting (respecting slow_down bumps).
  useEffect(() => {
    if (!open || state.phase !== "waiting") return;
    let cancelled = false;
    let interval = Math.max(1, state.start.interval || 5);
    let timer: number;
    const tick = () => {
      api.oauthPoll(connector.kind, state.start.flow_id).then(
        (res) => {
          if (cancelled) return;
          if (res.status === "connected") {
            setState({ phase: "connected", account: res.account ?? "" });
            void queryClient.invalidateQueries({ queryKey: ["connectors"] });
            void queryClient.invalidateQueries({ queryKey: ["github-repos"] });
            return;
          }
          if (res.interval) interval = Math.max(1, res.interval);
          timer = window.setTimeout(tick, interval * 1000);
        },
        (err: unknown) => {
          // 410 (expired) and 403 (declined) arrive with readable details.
          if (!cancelled) {
            setState({ phase: "error", message: errorMessage(err) });
          }
        },
      );
    };
    timer = window.setTimeout(tick, interval * 1000);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [open, state, connector.kind, queryClient]);

  const copyCode = (code: string) => {
    void navigator.clipboard
      ?.writeText(code)
      .then(() => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1500);
      })
      .catch(() => {
        /* clipboard unavailable */
      });
  };

  return (
    <Sheet
      open={open}
      onClose={onClose}
      title={`Sign in with ${connector.name}`}
    >
      {state.phase === "starting" && (
        <div className="flex items-center gap-3 py-6 text-sm text-muted">
          <Spinner size={18} />
          Contacting {connector.name}…
        </div>
      )}

      {state.phase === "waiting" && (
        <div className="space-y-4">
          <p className="text-sm text-muted">
            Enter this one-time code on the {connector.name} device page to
            connect your account:
          </p>
          <div className="flex items-center justify-center gap-2 rounded-xl border border-border bg-raised px-4 py-5">
            <span className="font-mono text-2xl font-bold tracking-[0.2em] text-text select-all">
              {state.start.user_code}
            </span>
            <button
              type="button"
              aria-label="Copy code"
              onClick={() => copyCode(state.start.user_code)}
              className="ml-1 flex h-9 w-9 cursor-pointer items-center justify-center rounded-md text-muted hover:bg-overlay hover:text-text"
            >
              {copied ? (
                <IconCheck size={16} className="text-ok" />
              ) : (
                <IconCopy size={16} />
              )}
            </button>
          </div>
          <a
            href={state.start.verification_uri}
            target="_blank"
            rel="noreferrer"
            className="flex min-h-11 w-full cursor-pointer items-center justify-center gap-1.5 rounded-md bg-accent px-4 text-sm font-semibold text-on-accent transition-colors duration-150 hover:bg-accent-hi"
          >
            Open {state.start.verification_uri.replace(/^https?:\/\//, "")}
            <IconExternal size={14} />
          </a>
          <p className="flex items-center justify-center gap-2 text-sm text-muted">
            <Spinner size={14} />
            Waiting for approval…
          </p>
        </div>
      )}

      {state.phase === "connected" && (
        <div className="space-y-4">
          <div className="flex items-center gap-3 rounded-xl border border-ok/30 bg-ok/10 px-4 py-3">
            <IconCheck size={18} className="shrink-0 text-ok" />
            <p className="text-sm text-text">
              Connected{state.account ? ` as ${state.account}` : ""} — sessions
              can now use your {connector.name} account.
            </p>
          </div>
          <Button variant="primary" className="w-full" onClick={onClose}>
            Done
          </Button>
        </div>
      )}

      {state.phase === "error" && (
        <div className="space-y-4">
          <p className="rounded-md bg-danger/10 px-3 py-2 text-sm break-words text-danger">
            {state.message}
          </p>
          <div className="flex gap-3">
            <Button className="flex-1" onClick={onClose}>
              Cancel
            </Button>
            <Button
              className="flex-1"
              variant="primary"
              onClick={() => setAttempt((a) => a + 1)}
            >
              Try again
            </Button>
          </div>
        </div>
      )}
    </Sheet>
  );
}

/** Sign-in / connected-account block on cards whose kind supports OAuth. */
function OAuthBlock({ connector }: { connector: Connector }) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const oauth = connector.oauth;
  const [deviceOpen, setDeviceOpen] = useState(false);

  const disconnect = useMutation({
    mutationFn: () => api.oauthDisconnect(connector.kind),
    onSuccess: () => {
      toast("success", `${connector.name} account disconnected`);
      void queryClient.invalidateQueries({ queryKey: ["connectors"] });
      void queryClient.invalidateQueries({ queryKey: ["github-repos"] });
    },
    onError: (err) => toast("error", errorMessage(err)),
  });

  // Code flow (Hugging Face): stash the pending kind + state, then leave the
  // SPA for the provider's consent page. /oauth/callback finishes the job.
  const startCode = useMutation({
    mutationFn: async () => {
      const res = await api.oauthStart(
        connector.kind,
        `${window.location.origin}/oauth/callback`,
      );
      if (res.flow !== "code") throw new Error("Unexpected flow type");
      rememberPendingOAuth(connector.kind, res.flow_id);
      window.location.assign(res.authorize_url);
    },
    onError: (err) => toast("error", errorMessage(err)),
  });

  let content;
  if (oauth.connected) {
    content = (
      <div className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-ok/25 bg-ok/5 px-3 py-2">
        <Chip color="text-ok">
          Connected as {oauth.account || "your account"}
        </Chip>
        <Button
          size="sm"
          variant="ghost"
          loading={disconnect.isPending}
          onClick={() => disconnect.mutate()}
        >
          Disconnect
        </Button>
      </div>
    );
  } else if (!oauth.ready) {
    content = (
      <div className="mt-3 rounded-lg border border-border bg-raised/50 px-3 py-2.5">
        <p className="text-xs text-faint">
          {oauth.setup_note ||
            `Sign-in isn't configured yet — an admin needs to add a ${connector.name} OAuth app.`}
        </p>
        <p className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1">
          <Link
            to="/settings"
            className="inline-flex min-h-6 items-center text-xs text-info underline underline-offset-2"
          >
            Open Settings
          </Link>
          {oauth.setup_url && (
            <a
              href={oauth.setup_url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex min-h-6 items-center gap-1 text-xs text-info underline underline-offset-2"
            >
              {connector.name} developer settings
              <IconExternal size={11} />
            </a>
          )}
        </p>
      </div>
    );
  } else {
    content = (
      <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <Button
          size="sm"
          variant="primary"
          loading={startCode.isPending}
          onClick={() =>
            oauth.method === "device" ? setDeviceOpen(true) : startCode.mutate()
          }
        >
          {connector.kind === "github" && <IconGitHub size={15} />}
          Sign in with {connector.name}
        </Button>
        <span className="text-xs text-faint">
          or paste a token under Configure
        </span>
      </div>
    );
  }

  return (
    <>
      {content}
      {/* Mounted outside the branches so the success state stays visible
          while the connectors refetch flips the card to "connected". */}
      {oauth.method === "device" && (
        <DeviceFlowSheet
          connector={connector}
          open={deviceOpen}
          onClose={() => setDeviceOpen(false)}
        />
      )}
    </>
  );
}

// ── Connector card ──────────────────────────────────────────────────────────

function ConnectorCard({ connector }: { connector: Connector }) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [expanded, setExpanded] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const Icon = connectorIcon(connector);

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
      if (ctx?.previous) queryClient.setQueryData(["connectors"], ctx.previous);
      toast("error", errorMessage(err));
    },
    onSettled: () =>
      void queryClient.invalidateQueries({ queryKey: ["connectors"] }),
  });

  const remove = useMutation({
    mutationFn: () => api.deleteConnector(connector.kind),
    onSuccess: () => {
      setConfirmDelete(false);
      toast("success", `${connector.name} removed`);
      void queryClient.invalidateQueries({ queryKey: ["connectors"] });
    },
    onError: (err) => {
      setConfirmDelete(false);
      toast("error", errorMessage(err));
    },
  });

  const configuredCount = connector.auth_fields.filter(
    (f) => f.configured,
  ).length;
  const hasConfigArea =
    connector.auth_fields.length > 0 ||
    Boolean(connector.auth_note) ||
    Boolean(connector.docs_url);

  return (
    <div className="rounded-xl border border-border bg-surface p-4">
      <div className="flex items-start gap-3">
        <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-raised text-muted">
          <Icon size={18} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-sm font-semibold text-text">{connector.name}</p>
            <McpTypeBadge type={connector.mcp_type} />
          </div>
          <p className="mt-0.5 text-sm break-words text-muted">
            {connector.description}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {connector.is_custom && (
            <button
              type="button"
              aria-label={`Delete ${connector.name} connector`}
              onClick={() => setConfirmDelete(true)}
              className="flex h-11 w-9 cursor-pointer items-center justify-center rounded-md text-danger/70 hover:bg-danger/10 hover:text-danger"
            >
              <IconTrash size={15} />
            </button>
          )}
          <Toggle
            checked={connector.enabled}
            onChange={(next) => toggle.mutate(next)}
            label={`${connector.enabled ? "Disable" : "Enable"} ${connector.name} connector`}
          />
        </div>
      </div>

      {connector.oauth.supported && <OAuthBlock connector={connector} />}

      {hasConfigArea && (
        <div className="mt-3 border-t border-border">
          <button
            type="button"
            aria-expanded={expanded}
            onClick={() => setExpanded((e) => !e)}
            className="flex min-h-10 w-full cursor-pointer items-center gap-2 pt-1 text-sm text-muted hover:text-text"
          >
            {expanded ? (
              <IconChevronDown size={15} className="shrink-0" />
            ) : (
              <IconChevronRight size={15} className="shrink-0" />
            )}
            Configure
            {connector.auth_fields.length > 0 && (
              <span
                className={cx(
                  "text-xs",
                  configuredCount > 0 ? "text-ok" : "text-faint",
                )}
              >
                {configuredCount}/{connector.auth_fields.length} set
              </span>
            )}
          </button>
          {expanded && <ConfigForm connector={connector} />}
        </div>
      )}

      <ConfirmDialog
        open={confirmDelete}
        title={`Delete ${connector.name}?`}
        body="Removes this custom connector. New session containers will no longer get it."
        busy={remove.isPending}
        onCancel={() => setConfirmDelete(false)}
        onConfirm={() => remove.mutate()}
      />
    </div>
  );
}

// ── Add custom connector ────────────────────────────────────────────────────

interface KvRow {
  key: string;
  value: string;
}

function rowsToRecord(rows: KvRow[]): Record<string, string> {
  const record: Record<string, string> = {};
  for (const row of rows) {
    const key = row.key.trim();
    if (key) record[key] = row.value;
  }
  return record;
}

function KeyValueRows({
  label,
  rows,
  onChange,
  keyPlaceholder,
  valuePlaceholder,
  addLabel,
  note,
}: {
  label: string;
  rows: KvRow[];
  onChange: (rows: KvRow[]) => void;
  keyPlaceholder: string;
  valuePlaceholder: string;
  addLabel: string;
  note?: string;
}) {
  return (
    <div className="space-y-1.5">
      <span className="block text-sm font-medium text-muted">{label}</span>
      {rows.map((row, i) => (
        <div key={i} className="flex gap-2">
          <TextInput
            aria-label={`${label} name ${i + 1}`}
            value={row.key}
            autoComplete="off"
            placeholder={keyPlaceholder}
            onChange={(e) =>
              onChange(
                rows.map((r, j) =>
                  j === i ? { ...r, key: e.target.value } : r,
                ),
              )
            }
          />
          <TextInput
            aria-label={`${label} value ${i + 1}`}
            value={row.value}
            autoComplete="off"
            placeholder={valuePlaceholder}
            onChange={(e) =>
              onChange(
                rows.map((r, j) =>
                  j === i ? { ...r, value: e.target.value } : r,
                ),
              )
            }
          />
          <button
            type="button"
            aria-label={`Remove ${label.toLowerCase()} row ${i + 1}`}
            onClick={() => onChange(rows.filter((_, j) => j !== i))}
            className="flex min-h-11 w-9 shrink-0 cursor-pointer items-center justify-center rounded-md text-muted hover:bg-raised hover:text-text"
          >
            <IconX size={15} />
          </button>
        </div>
      ))}
      <Button
        size="sm"
        variant="ghost"
        onClick={() => onChange([...rows, { key: "", value: "" }])}
      >
        <IconPlus size={14} />
        {addLabel}
      </Button>
      {note && <p className="text-xs text-faint">{note}</p>}
    </div>
  );
}

const CUSTOM_TABS = [
  { id: "remote", label: "Remote (URL)" },
  { id: "local", label: "Local (command)" },
] as const;

function AddCustomSheet({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<McpType>("remote");
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [headers, setHeaders] = useState<KvRow[]>([]);
  const [command, setCommand] = useState("");
  const [envRows, setEnvRows] = useState<KvRow[]>([]);

  const reset = () => {
    setMode("remote");
    setName("");
    setUrl("");
    setHeaders([]);
    setCommand("");
    setEnvRows([]);
  };

  const add = useMutation({
    mutationFn: () => {
      const headerRecord = rowsToRecord(headers);
      const envRecord = rowsToRecord(envRows);
      return api.addCustomConnector({
        name: name.trim(),
        mcp_type: mode,
        ...(mode === "remote"
          ? {
              url: url.trim(),
              ...(Object.keys(headerRecord).length > 0
                ? { headers: headerRecord }
                : {}),
            }
          : {
              command: command.trim().split(/\s+/),
              ...(Object.keys(envRecord).length > 0
                ? { environment: envRecord }
                : {}),
            }),
      });
    },
    onSuccess: (created) => {
      toast("success", `${created.name} added`);
      void queryClient.invalidateQueries({ queryKey: ["connectors"] });
      reset();
      onClose();
    },
    onError: (err) => toast("error", errorMessage(err)),
  });

  const valid =
    name.trim().length > 0 &&
    (mode === "remote"
      ? /^https?:\/\//.test(url.trim())
      : command.trim().length > 0);

  return (
    <Sheet open={open} onClose={onClose} title="Add custom connector">
      <div className="space-y-4">
        <SegmentedTabs
          tabs={CUSTOM_TABS}
          value={mode}
          onChange={(next) => setMode(next)}
        />

        <Field label="Name">
          {(id) => (
            <TextInput
              id={id}
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="My MCP server"
              required
            />
          )}
        </Field>

        {mode === "remote" ? (
          <>
            <Field label="Server URL" helper="An http(s) MCP endpoint.">
              {(id) => (
                <TextInput
                  id={id}
                  type="url"
                  inputMode="url"
                  autoComplete="off"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  placeholder="https://mcp.example.com/mcp"
                  required
                />
              )}
            </Field>
            <KeyValueRows
              label="Headers (optional)"
              rows={headers}
              onChange={setHeaders}
              keyPlaceholder="Authorization"
              valuePlaceholder="Bearer …"
              addLabel="Add header"
              note="Headers are stored as-is in each session's config"
            />
          </>
        ) : (
          <>
            <Field
              label="Command"
              helper="Split on spaces, e.g. npx -y @some/mcp-server."
            >
              {(id) => (
                <TextInput
                  id={id}
                  autoComplete="off"
                  value={command}
                  onChange={(e) => setCommand(e.target.value)}
                  placeholder="npx -y @some/mcp-server"
                  required
                />
              )}
            </Field>
            <KeyValueRows
              label="Environment (optional)"
              rows={envRows}
              onChange={setEnvRows}
              keyPlaceholder="API_KEY"
              valuePlaceholder="value"
              addLabel="Add variable"
            />
          </>
        )}

        <Button
          variant="primary"
          className="w-full"
          disabled={!valid}
          loading={add.isPending}
          onClick={() => add.mutate()}
        >
          <IconPlus size={15} />
          Add connector
        </Button>
      </div>
    </Sheet>
  );
}

// ── Page ────────────────────────────────────────────────────────────────────

export default function Connectors() {
  const [addOpen, setAddOpen] = useState(false);
  const connectors = useQuery({
    queryKey: ["connectors"],
    queryFn: api.listConnectors,
  });

  const groups = useMemo(() => {
    const data = connectors.data ?? [];
    return CATEGORY_ORDER.map((category) => ({
      category,
      items: data.filter((c) => c.category === category),
    })).filter((g) => g.items.length > 0 || g.category === "custom");
  }, [connectors.data]);

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

      {connectors.data &&
        groups.map(({ category, items }) => (
          <section key={category} className="mb-7">
            <h2 className="mb-2.5 flex items-center gap-2 text-sm font-semibold text-muted">
              {CATEGORY_LABELS[category]}
              {items.length > 0 && (
                <span className="text-xs font-normal text-faint">
                  {items.filter((c) => c.enabled).length}/{items.length} on
                </span>
              )}
            </h2>
            <ul className="space-y-3">
              {items.map((c) => (
                <li key={c.kind}>
                  <ConnectorCard connector={c} />
                </li>
              ))}
              {category === "custom" && (
                <li>
                  <button
                    type="button"
                    onClick={() => setAddOpen(true)}
                    className="flex min-h-14 w-full cursor-pointer items-center justify-center gap-2 rounded-xl border border-dashed border-edge px-4 py-4 text-sm font-medium text-muted transition-colors duration-150 hover:border-accent/50 hover:text-text"
                  >
                    <IconPlus size={16} />
                    Add custom connector
                  </button>
                </li>
              )}
            </ul>
          </section>
        ))}

      {connectors.data && (
        <p className="mt-4 text-center text-xs text-faint">
          Connector changes apply when a session container is (re)created.
        </p>
      )}

      <AddCustomSheet open={addOpen} onClose={() => setAddOpen(false)} />
    </div>
  );
}
