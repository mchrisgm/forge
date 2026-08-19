import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, ApiError, errorMessage } from "../api/client";
import type {
  EngineKind,
  Lease,
  ModelEntry,
  Suggestion,
  ToolCallFormat,
} from "../api/types";
import {
  IconChat,
  IconChevronDown,
  IconChevronRight,
  IconDownload,
  IconPlay,
  IconRefresh,
  IconStop,
  IconTrash,
} from "../components/icons";
import { PageHeader } from "../components/layout";
import {
  Button,
  Collapsible,
  ConfirmDialog,
  EmptyState,
  Field,
  LaneBadge,
  ModelStatusChip,
  ProgressBar,
  Select,
  SkeletonList,
  Spinner,
  TextInput,
  Toggle,
} from "../components/ui";
import { useGlobalEvents } from "../hooks/events";
import { useToast } from "../hooks/toast";
import { cx, formatGb } from "../lib/utils";

// ── GPU lease banner ────────────────────────────────────────────────────────

function LeaseBanner() {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const engines = useQuery({
    queryKey: ["engines"],
    queryFn: api.enginesStatus,
  });
  const lease = engines.data?.lease ?? null;
  const stats = useQuery({
    queryKey: ["system"],
    queryFn: api.systemStats,
    enabled: lease?.state === "ready",
    refetchInterval: 10000,
  });

  const unload = useMutation({
    mutationFn: api.unloadEngine,
    onSuccess: () => {
      toast("success", "Engine unloaded — GPU released");
      void queryClient.invalidateQueries({ queryKey: ["engines"] });
    },
    onError: (err) => toast("error", errorMessage(err)),
  });

  if (!lease) return null;

  const gpu = stats.data?.gpu ?? null;
  const budget = stats.data?.budgets.vram_gb ?? null;

  return (
    <div
      className={cx(
        "sticky top-2 z-10 mb-5 rounded-xl border p-4 backdrop-blur",
        lease.state === "failed"
          ? "border-danger/40 bg-danger/10"
          : lease.state === "ready"
            ? "border-ok/30 bg-surface/95"
            : "border-warn/40 bg-surface/95",
      )}
    >
      <div className="flex items-center gap-3">
        {lease.state === "starting" && <Spinner size={18} />}
        {lease.state === "ready" && (
          <span aria-hidden className="h-2.5 w-2.5 animate-pulse-dot rounded-full bg-ok text-ok" />
        )}
        {lease.state === "failed" && (
          <span aria-hidden className="h-2.5 w-2.5 rounded-full bg-danger" />
        )}
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold text-text">
            {lease.model_name}
            <span className="ml-2 align-middle">
              <LaneBadge engine={lease.engine} />
            </span>
          </p>
          <p className="text-xs text-muted">
            {lease.state === "starting" && "Loading onto the GPU — this can take minutes"}
            {lease.state === "ready" && "Serving on the GPU"}
            {lease.state === "failed" && "Engine failed to load"}
          </p>
        </div>
        {lease.state === "ready" && (
          <Link
            to="/chat"
            aria-label={`Chat with ${lease.model_name}`}
            className="shrink-0"
          >
            <Button size="sm" variant="primary">
              <IconChat size={14} />
              <span className="sm:hidden">Chat</span>
              <span className="hidden sm:inline">Chat with model</span>
            </Button>
          </Link>
        )}
        <Button
          size="sm"
          variant="secondary"
          loading={unload.isPending}
          onClick={() => unload.mutate()}
        >
          <IconStop size={14} />
          Unload
        </Button>
      </div>

      {lease.state === "ready" && gpu && (
        <div className="mt-3">
          <div className="mb-1 flex justify-between text-xs text-muted">
            <span>VRAM</span>
            <span>
              {formatGb(gpu.vram_used_gb)} / {formatGb(gpu.vram_total_gb)}
              {budget != null && ` · budget ${budget} GB`}
            </span>
          </div>
          <div className="relative">
            <ProgressBar
              pct={(gpu.vram_used_gb / gpu.vram_total_gb) * 100}
              tone={gpu.vram_used_gb > (budget ?? Infinity) ? "accent" : "info"}
            />
            {budget != null && budget <= gpu.vram_total_gb && (
              <span
                aria-hidden
                className="absolute top-1/2 h-3 w-0.5 -translate-y-1/2 rounded bg-warn"
                style={{ left: `${(budget / gpu.vram_total_gb) * 100}%` }}
                title={`VRAM budget ${budget} GB`}
              />
            )}
          </div>
        </div>
      )}

      {lease.state === "failed" && lease.error && (
        <div className="mt-2">
          <Collapsible summary="Error log tail">
            <pre className="max-h-48 overflow-auto rounded-md border border-danger/30 bg-bg px-3 py-2 font-mono text-[11px] leading-relaxed whitespace-pre-wrap text-danger/90">
              {lease.error}
            </pre>
          </Collapsible>
        </div>
      )}
    </div>
  );
}

// ── Suggestions inbox ───────────────────────────────────────────────────────

const SCORE_SEGMENTS = [
  { key: "trend", label: "Trend", weight: 0.35, color: "bg-info" },
  { key: "recency", label: "Recency", weight: 0.25, color: "bg-lane-airllm" },
  { key: "coding_signal", label: "Coding", weight: 0.25, color: "bg-ok" },
  { key: "fit", label: "Fit", weight: 0.15, color: "bg-accent" },
] as const;

function ScoreBar({ suggestion }: { suggestion: Suggestion }) {
  const reason = suggestion.reason;
  const contributions = SCORE_SEGMENTS.map((seg) => ({
    ...seg,
    value: seg.weight * (Number(reason[seg.key]) || 0),
  }));
  const score = Number(reason.score) || 0;
  return (
    <div>
      <div className="mb-1 flex items-center justify-between">
        <span className="text-xs text-muted">Score breakdown</span>
        <span className="font-mono text-xs font-semibold text-text">
          {score.toFixed(2)}
        </span>
      </div>
      <div
        className="flex h-2 w-full overflow-hidden rounded-full bg-overlay"
        role="img"
        aria-label={contributions
          .map((c) => `${c.label} ${c.value.toFixed(2)}`)
          .join(", ")}
      >
        {contributions.map((c) => (
          <div
            key={c.key}
            className={cx("h-full", c.color)}
            style={{ width: `${c.value * 100}%` }}
            title={`${c.label}: ${c.value.toFixed(2)} of max ${c.weight}`}
          />
        ))}
      </div>
      <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5">
        {contributions.map((c) => (
          <span key={c.key} className="inline-flex items-center gap-1 text-[10px] text-faint">
            <span aria-hidden className={cx("h-1.5 w-1.5 rounded-full", c.color)} />
            {c.label} {(Number(reason[c.key]) || 0).toFixed(2)}
          </span>
        ))}
      </div>
    </div>
  );
}

function SuggestionCard({ suggestion }: { suggestion: Suggestion }) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const reason = suggestion.reason;

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["suggestions"] });
    void queryClient.invalidateQueries({ queryKey: ["models"] });
  };

  const approve = useMutation({
    mutationFn: () => api.approveSuggestion(suggestion.id),
    onSuccess: (entry) => {
      toast("success", `${entry.display_name} approved — download started`);
      invalidate();
    },
    onError: (err) => toast("error", errorMessage(err)),
  });
  const dismiss = useMutation({
    mutationFn: () => api.dismissSuggestion(suggestion.id),
    onSuccess: () => invalidate(),
    onError: (err) => toast("error", errorMessage(err)),
  });

  return (
    <div className="rounded-xl border border-border bg-surface p-4">
      <div className="flex items-start justify-between gap-3">
        <p className="min-w-0 flex-1 font-mono text-sm font-semibold break-all text-text">
          {suggestion.hf_repo}
        </p>
        {reason.lane && <LaneBadge engine={reason.lane} detailed />}
      </div>

      <div className="mt-3">
        <ScoreBar suggestion={suggestion} />
      </div>

      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
        {reason.params_b != null && reason.params_b > 0 && (
          <span>{reason.params_b}B params{reason.is_moe ? " · MoE" : ""}</span>
        )}
        {reason.gguf_file && (
          <span className="max-w-full truncate font-mono">{reason.gguf_file}</span>
        )}
        {reason.gguf_size_gb != null && reason.gguf_size_gb > 0 && (
          <span>{formatGb(reason.gguf_size_gb)}</span>
        )}
        {reason.has_awq && <span>AWQ available</span>}
      </div>

      <div className="mt-3 flex gap-2 border-t border-border pt-3">
        <Button
          size="sm"
          variant="primary"
          loading={approve.isPending}
          onClick={() => approve.mutate()}
        >
          Approve & download
        </Button>
        <Button
          size="sm"
          variant="ghost"
          loading={dismiss.isPending}
          onClick={() => dismiss.mutate()}
        >
          Dismiss
        </Button>
      </div>
    </div>
  );
}

// ── Catalog card ────────────────────────────────────────────────────────────

function ModelCard({
  model,
  lease,
}: {
  model: ModelEntry;
  lease: Lease | null;
}) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const { downloads } = useGlobalEvents();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [busyHolder, setBusyHolder] = useState<Lease | null>(null);

  const progress = downloads[model.id];
  const isHolder = lease?.model_id === model.id && lease.state !== "failed";

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["models"] });
    void queryClient.invalidateQueries({ queryKey: ["engines"] });
  };

  const load = useMutation({
    mutationFn: (force: boolean) => api.loadEngine(model.id, force),
    onSuccess: () => {
      setBusyHolder(null);
      toast("info", `Loading ${model.display_name} onto the GPU…`);
      invalidate();
    },
    onError: (err) => {
      if (err instanceof ApiError && err.status === 409) {
        const detail = err.detail as { holder?: Lease } | null;
        if (detail?.holder) {
          setBusyHolder(detail.holder);
          return;
        }
      }
      toast("error", errorMessage(err));
    },
  });

  const unload = useMutation({
    mutationFn: api.unloadEngine,
    onSuccess: () => invalidate(),
    onError: (err) => toast("error", errorMessage(err)),
  });

  const download = useMutation({
    mutationFn: () => api.downloadModel(model.id),
    onSuccess: () => invalidate(),
    onError: (err) => toast("error", errorMessage(err)),
  });

  const remove = useMutation({
    mutationFn: () => api.deleteModel(model.id),
    onSuccess: () => {
      setConfirmDelete(false);
      toast("success", `Deleted ${model.display_name}`);
      invalidate();
    },
    onError: (err) => {
      setConfirmDelete(false);
      toast("error", errorMessage(err));
    },
  });

  return (
    <div className="rounded-xl border border-border bg-surface p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-[15px] font-semibold text-text">
            {model.display_name}
          </p>
          <p className="truncate font-mono text-xs text-faint">{model.hf_repo}</p>
        </div>
        <ModelStatusChip status={model.status} />
      </div>

      <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs text-muted">
        <LaneBadge engine={model.engine} detailed />
        <span className="font-mono">{model.quant}</span>
        {model.params_b > 0 && <span>{model.params_b}B</span>}
        {model.size_gb > 0 && <span>{formatGb(model.size_gb)}</span>}
        <span>ctx {model.ctx_max.toLocaleString()}</span>
        <span>tools: {model.tool_call_format}</span>
      </div>

      {model.status === "downloading" && (
        <div className="mt-3">
          <div className="mb-1 flex justify-between text-xs text-muted">
            <span>Downloading…</span>
            <span className="font-mono">
              {progress
                ? `${formatGb(progress.downloaded_gb)}${
                    progress.total_gb ? ` / ${formatGb(progress.total_gb)}` : ""
                  }${progress.pct != null ? ` · ${progress.pct.toFixed(0)}%` : ""}`
                : "starting…"}
            </span>
          </div>
          <ProgressBar
            pct={progress?.pct ?? null}
            indeterminate={!progress || progress.pct == null}
            tone="info"
          />
        </div>
      )}

      {model.status === "failed" && model.note && (
        <p className="mt-2 rounded-md bg-danger/10 px-2.5 py-1.5 font-mono text-xs break-words text-danger">
          {model.note.slice(0, 200)}
        </p>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-border pt-3">
        {(model.status === "approved" || model.status === "failed") && (
          <Button
            size="sm"
            loading={download.isPending}
            onClick={() => download.mutate()}
          >
            <IconDownload size={14} />
            Download
          </Button>
        )}
        {model.status === "ready" && !isHolder && (
          <Button
            size="sm"
            variant="primary"
            loading={load.isPending}
            onClick={() => load.mutate(false)}
          >
            <IconPlay size={14} />
            Load
          </Button>
        )}
        {isHolder && (
          <Button
            size="sm"
            variant="secondary"
            loading={unload.isPending}
            onClick={() => unload.mutate()}
          >
            <IconStop size={14} />
            Unload
          </Button>
        )}
        <Button
          size="sm"
          variant="ghost"
          aria-label={`Delete ${model.display_name}`}
          className="ml-auto text-danger/80 hover:bg-danger/10 hover:text-danger"
          onClick={() => setConfirmDelete(true)}
        >
          <IconTrash size={15} />
        </Button>
      </div>

      <ConfirmDialog
        open={confirmDelete}
        title={`Delete ${model.display_name}?`}
        body="Removes the catalog entry and its downloaded weights from disk."
        busy={remove.isPending}
        onCancel={() => setConfirmDelete(false)}
        onConfirm={() => remove.mutate()}
      />

      <ConfirmDialog
        open={busyHolder != null}
        title="GPU is busy"
        body={
          busyHolder
            ? `The GPU is serving ${busyHolder.model_name} (${busyHolder.engine}). Switch to ${model.display_name}? The current engine stops first.`
            : ""
        }
        confirmLabel="Switch model"
        danger={false}
        busy={load.isPending}
        onCancel={() => setBusyHolder(null)}
        onConfirm={() => load.mutate(true)}
      />
    </div>
  );
}

// ── Manual add form ─────────────────────────────────────────────────────────

const TOOL_FORMATS: ToolCallFormat[] = ["hermes", "qwen", "llama3", "none"];

function ManualAddForm() {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [hfRepo, setHfRepo] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [engine, setEngine] = useState<EngineKind>("llamacpp");
  const [ggufFilename, setGgufFilename] = useState("");
  const [paramsB, setParamsB] = useState("");
  const [ctxMax, setCtxMax] = useState("16384");
  const [toolFormat, setToolFormat] = useState<ToolCallFormat>("hermes");
  const [autoDownload, setAutoDownload] = useState(true);

  const add = useMutation({
    mutationFn: () =>
      api.addModel({
        hf_repo: hfRepo.trim(),
        display_name: displayName.trim(),
        engine,
        gguf_filename: engine === "llamacpp" ? ggufFilename.trim() : "",
        params_b: Number(paramsB) || 0,
        ctx_max: Number(ctxMax) || 16384,
        tool_call_format: toolFormat,
        auto_download: autoDownload,
      }),
    onSuccess: (entry) => {
      toast(
        "success",
        `${entry.display_name} added${autoDownload ? " — downloading" : ""}`,
      );
      setHfRepo("");
      setDisplayName("");
      setGgufFilename("");
      setParamsB("");
      void queryClient.invalidateQueries({ queryKey: ["models"] });
    },
    onError: (err) => toast("error", errorMessage(err)),
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!hfRepo.trim()) return;
    if (engine === "llamacpp" && !ggufFilename.trim().endsWith(".gguf")) {
      toast("error", "llama.cpp models need a .gguf filename");
      return;
    }
    add.mutate();
  };

  return (
    <section className="rounded-xl border border-border bg-surface">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="flex min-h-12 w-full cursor-pointer items-center gap-2 px-4 text-left text-sm font-semibold text-text"
      >
        {open ? <IconChevronDown size={16} /> : <IconChevronRight size={16} />}
        Add model manually
      </button>
      {open && (
        <form onSubmit={submit} className="space-y-4 border-t border-border p-4">
          <Field label="Hugging Face repo">
            {(id) => (
              <TextInput
                id={id}
                value={hfRepo}
                onChange={(e) => setHfRepo(e.target.value)}
                placeholder="Qwen/Qwen2.5-Coder-14B-Instruct-GGUF"
                required
              />
            )}
          </Field>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field label="Display name" helper="Defaults to the repo name.">
              {(id) => (
                <TextInput
                  id={id}
                  value={displayName}
                  onChange={(e) => setDisplayName(e.target.value)}
                  placeholder="Qwen Coder 14B"
                />
              )}
            </Field>
            <Field label="Engine lane">
              {(id) => (
                <Select
                  id={id}
                  value={engine}
                  onChange={(e) => setEngine(e.target.value as EngineKind)}
                >
                  <option value="llamacpp">llama.cpp (GGUF)</option>
                  <option value="vllm">vLLM (AWQ)</option>
                  <option value="airllm">AirLLM (slow, chat-only)</option>
                </Select>
              )}
            </Field>
          </div>
          {engine === "llamacpp" && (
            <Field
              label="GGUF filename"
              helper="The exact *.gguf file inside the repo (e.g. model-Q4_K_M.gguf)."
            >
              {(id) => (
                <TextInput
                  id={id}
                  value={ggufFilename}
                  onChange={(e) => setGgufFilename(e.target.value)}
                  placeholder="qwen2.5-coder-14b-instruct-q4_k_m.gguf"
                  required
                />
              )}
            </Field>
          )}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <Field label="Params (B)">
              {(id) => (
                <TextInput
                  id={id}
                  type="number"
                  step="0.1"
                  min="0"
                  inputMode="decimal"
                  value={paramsB}
                  onChange={(e) => setParamsB(e.target.value)}
                  placeholder="14"
                />
              )}
            </Field>
            <Field label="Max context">
              {(id) => (
                <TextInput
                  id={id}
                  type="number"
                  min="1024"
                  step="1024"
                  inputMode="numeric"
                  value={ctxMax}
                  onChange={(e) => setCtxMax(e.target.value)}
                />
              )}
            </Field>
            <Field label="Tool-call format">
              {(id) => (
                <Select
                  id={id}
                  value={toolFormat}
                  onChange={(e) =>
                    setToolFormat(e.target.value as ToolCallFormat)
                  }
                >
                  {TOOL_FORMATS.map((f) => (
                    <option key={f} value={f}>
                      {f}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
          </div>
          <div className="flex items-center justify-between gap-3">
            <span className="text-sm text-muted">Download immediately</span>
            <Toggle
              checked={autoDownload}
              onChange={setAutoDownload}
              label="Download immediately"
            />
          </div>
          <Button
            type="submit"
            variant="primary"
            className="w-full"
            loading={add.isPending}
            disabled={!hfRepo.trim()}
          >
            Add to catalog
          </Button>
        </form>
      )}
    </section>
  );
}

// ── Page ────────────────────────────────────────────────────────────────────

export default function Models() {
  const { toast } = useToast();
  const queryClient = useQueryClient();

  const models = useQuery({ queryKey: ["models"], queryFn: api.listModels });
  const suggestions = useQuery({
    queryKey: ["suggestions"],
    queryFn: api.listSuggestions,
  });
  const engines = useQuery({
    queryKey: ["engines"],
    queryFn: api.enginesStatus,
  });

  const scan = useMutation({
    mutationFn: api.triggerScan,
    onSuccess: (res) => {
      toast(
        "info",
        res.new_suggestions > 0
          ? `Scan found ${res.new_suggestions} new suggestion(s)`
          : "Scan finished — nothing new above the bar",
      );
      void queryClient.invalidateQueries({ queryKey: ["suggestions"] });
    },
    onError: (err) => toast("error", errorMessage(err)),
  });

  const lease = engines.data?.lease ?? null;
  const catalog = useMemo(
    () => (models.data ?? []).filter((m) => m.status !== "suggested"),
    [models.data],
  );

  return (
    <div>
      <PageHeader
        title="Models"
        subtitle="Local catalog, registry suggestions and the GPU lease"
        actions={
          <Button
            size="sm"
            loading={scan.isPending}
            onClick={() => scan.mutate()}
          >
            <IconRefresh size={14} />
            Scan now
          </Button>
        }
      />

      <LeaseBanner />

      {/* Suggestions inbox */}
      <section className="mb-7">
        <h2 className="mb-2.5 flex items-center gap-2 text-sm font-semibold text-muted">
          Suggestions
          {(suggestions.data?.length ?? 0) > 0 && (
            <span className="rounded-full bg-accent/15 px-2 py-0.5 text-xs font-semibold text-accent">
              {suggestions.data?.length}
            </span>
          )}
        </h2>
        {suggestions.isLoading && <SkeletonList rows={2} />}
        {suggestions.isError && (
          <p className="text-sm text-danger">{errorMessage(suggestions.error)}</p>
        )}
        {suggestions.data && suggestions.data.length === 0 && (
          <p className="rounded-xl border border-dashed border-border px-4 py-5 text-center text-sm text-muted">
            Inbox zero. The weekly registry scan proposes new models that fit
            this hardware — or hit "Scan now".
          </p>
        )}
        {suggestions.data && suggestions.data.length > 0 && (
          <ul className="space-y-3">
            {suggestions.data.map((s) => (
              <li key={s.id}>
                <SuggestionCard suggestion={s} />
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Catalog */}
      <section className="mb-7">
        <h2 className="mb-2.5 text-sm font-semibold text-muted">Catalog</h2>
        {models.isLoading && <SkeletonList rows={3} />}
        {models.isError && (
          <EmptyState
            icon="search"
            title="Couldn't load the catalog"
            hint={errorMessage(models.error)}
            action={<Button onClick={() => void models.refetch()}>Retry</Button>}
          />
        )}
        {models.data && catalog.length === 0 && (
          <EmptyState
            icon="box"
            title="No models yet"
            hint="Approve a suggestion or add one manually below."
          />
        )}
        {catalog.length > 0 && (
          <ul className="space-y-3">
            {catalog.map((m) => (
              <li key={m.id}>
                <ModelCard model={m} lease={lease} />
              </li>
            ))}
          </ul>
        )}
      </section>

      <ManualAddForm />
    </div>
  );
}
