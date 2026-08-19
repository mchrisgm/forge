// Connector catalog — GET /api/connectors returns grouped catalog entries
// (core / productivity / developer / design / business) plus user-defined
// custom MCP servers. Cards expose an enable toggle, a dynamic credential
// form driven by auth_fields, and (for custom rows) delete.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState, type ComponentType } from "react";
import { api, errorMessage } from "../api/client";
import type {
  Connector,
  ConnectorAuthField,
  ConnectorCategory,
  McpType,
} from "../api/types";
import {
  IconActivity,
  IconBrowser,
  IconChevronDown,
  IconChevronRight,
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
  ConfirmDialog,
  EmptyState,
  Field,
  SegmentedTabs,
  Sheet,
  SkeletonList,
  TextInput,
  Toggle,
} from "../components/ui";
import { useToast } from "../hooks/toast";
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
