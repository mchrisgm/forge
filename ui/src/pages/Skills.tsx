import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { api, ApiError, errorMessage } from "../api/client";
import type {
  CatalogSkill,
  PackInstallResult,
  PackSkill,
  Skill,
  SkillCategory,
} from "../api/types";
import {
  IconAlert,
  IconCheck,
  IconChevronDown,
  IconChevronRight,
  IconExternal,
  IconPlus,
  IconSearch,
  IconTrash,
} from "../components/icons";
import { PageHeader } from "../components/layout";
import {
  Button,
  Collapsible,
  ConfirmDialog,
  EmptyState,
  Field,
  SkeletonBlock,
  SkeletonList,
  TextInput,
  Toggle,
} from "../components/ui";
import { useToast } from "../hooks/toast";
import { cx, relativeTime } from "../lib/utils";

const CATEGORY_META: { id: SkillCategory; label: string }[] = [
  { id: "workflow", label: "Workflow" },
  { id: "languages", label: "Languages & frameworks" },
  { id: "quality", label: "Code quality" },
  { id: "research", label: "Research & writing" },
  { id: "other", label: "Agent discipline" },
];

const PACK_SELECT_CAP = 100; // mirrors skills_service.PACK_INSTALL_CAP

/** Parse "…multiple skills — pass one of these as subdir: a, b/c, d" */
function parseSubdirSuggestions(err: unknown): string[] {
  if (!(err instanceof ApiError) || typeof err.detail !== "string") return [];
  const marker = "subdir:";
  const idx = err.detail.indexOf(marker);
  if (idx === -1 || !err.detail.includes("multiple skills")) return [];
  return err.detail
    .slice(idx + marker.length)
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean)
    .slice(0, 24);
}

function SkillCard({ skill }: { skill: Skill }) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [confirmDelete, setConfirmDelete] = useState(false);

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["skills"] });

  const toggle = useMutation({
    mutationFn: (enabled: boolean) => api.patchSkill(skill.id, enabled),
    // Optimistic flip; global SSE + invalidation reconciles.
    onMutate: async (enabled) => {
      await queryClient.cancelQueries({ queryKey: ["skills"] });
      const previous = queryClient.getQueryData<Skill[]>(["skills"]);
      queryClient.setQueryData<Skill[]>(["skills"], (old) =>
        (old ?? []).map((s) => (s.id === skill.id ? { ...s, enabled } : s)),
      );
      return { previous };
    },
    onError: (err, _vars, ctx) => {
      if (ctx?.previous) queryClient.setQueryData(["skills"], ctx.previous);
      toast("error", errorMessage(err));
    },
    onSettled: () => void invalidate(),
  });

  const remove = useMutation({
    mutationFn: () => api.deleteSkill(skill.id),
    onSuccess: () => {
      setConfirmDelete(false);
      toast("success", `Removed skill "${skill.name}"`);
      void invalidate();
    },
    onError: (err) => toast("error", errorMessage(err)),
  });

  return (
    <div className="rounded-xl border border-border bg-surface p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="font-mono text-sm font-semibold text-text">
            {skill.name}
          </p>
          {skill.description && (
            <p className="mt-1 text-sm break-words text-muted">
              {skill.description}
            </p>
          )}
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-faint">
            {skill.source_url && (
              <a
                href={skill.source_url}
                target="_blank"
                rel="noreferrer noopener"
                className="inline-flex items-center gap-1 text-info hover:underline"
              >
                <IconExternal size={12} />
                source
              </a>
            )}
            <span>installed {relativeTime(skill.installed_at)}</span>
          </div>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <Toggle
            checked={skill.enabled}
            onChange={(next) => toggle.mutate(next)}
            label={`${skill.enabled ? "Disable" : "Enable"} skill ${skill.name}`}
          />
          <Button
            size="sm"
            variant="ghost"
            aria-label={`Delete skill ${skill.name}`}
            className="text-danger/80 hover:bg-danger/10 hover:text-danger"
            onClick={() => setConfirmDelete(true)}
          >
            <IconTrash size={15} />
          </Button>
        </div>
      </div>
      <ConfirmDialog
        open={confirmDelete}
        title={`Remove "${skill.name}"?`}
        body="The skill directory is deleted from the shared skills volume. Sessions lose access on their next boot."
        busy={remove.isPending}
        onCancel={() => setConfirmDelete(false)}
        onConfirm={() => remove.mutate()}
      />
    </div>
  );
}

// ── Suggested skills (curated ECC catalog) ──────────────────────────────────

function CatalogRow({
  skill,
  installing,
  disabled,
  onInstall,
}: {
  skill: CatalogSkill;
  installing: boolean;
  disabled: boolean;
  onInstall: () => void;
}) {
  return (
    <div className="flex items-start justify-between gap-3 rounded-lg border border-border/60 bg-raised/40 p-3">
      <div className="min-w-0 flex-1">
        <p className="font-mono text-xs font-semibold text-text">{skill.name}</p>
        <p className="mt-0.5 text-xs break-words text-muted">
          {skill.description}
        </p>
      </div>
      {skill.installed ? (
        <span className="inline-flex shrink-0 items-center gap-1 rounded-full border border-ok/30 bg-ok/10 px-2.5 py-1 text-xs font-medium text-ok">
          <IconCheck size={13} />
          Installed
        </span>
      ) : (
        <Button
          size="sm"
          variant="secondary"
          className="shrink-0"
          loading={installing}
          disabled={disabled}
          onClick={onInstall}
        >
          {!installing && <IconPlus size={14} />}
          Install
        </Button>
      )}
    </div>
  );
}

function SuggestedSkills() {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const catalog = useQuery({
    queryKey: ["skill-catalog"],
    queryFn: api.skillCatalog,
  });

  const install = useMutation({
    mutationFn: (name: string) => api.installCatalogSkill(name),
    onSuccess: (skill) => {
      toast("success", `Installed "${skill.name}"`);
      void queryClient.invalidateQueries({ queryKey: ["skills"] });
      void queryClient.invalidateQueries({ queryKey: ["skill-catalog"] });
    },
    // 404 (unknown) / 409 (already installed) surface via the toast pattern.
    onError: (err) => toast("error", errorMessage(err)),
  });

  if (catalog.isLoading) {
    return <SkeletonBlock className="mb-6 h-28 w-full" />;
  }
  if (catalog.isError || !catalog.data || catalog.data.length === 0) {
    return null;
  }

  const entries = catalog.data;
  const grouped = CATEGORY_META.map((c) => ({
    ...c,
    items: entries.filter((s) => s.category === c.id),
  })).filter((g) => g.items.length > 0);

  return (
    <section className="mb-6 rounded-xl border border-border bg-surface p-4">
      <h2 className="text-sm font-semibold text-text">Suggested skills</h2>
      <p className="mt-0.5 mb-3 text-xs text-muted">
        Curated from the ECC pack. One click installs and enables a skill for
        your sessions.
      </p>
      <div className="space-y-0.5">
        {grouped.map((g) => {
          const installedCount = g.items.filter((s) => s.installed).length;
          return (
            <Collapsible
              key={g.id}
              summary={
                <span className="flex items-center gap-2">
                  <span className="font-medium text-text">{g.label}</span>
                  <span className="text-xs text-faint">
                    {g.items.length}
                    {installedCount > 0 && ` · ${installedCount} installed`}
                  </span>
                </span>
              }
            >
              <ul className="space-y-2 pb-1">
                {g.items.map((s) => (
                  <li key={s.name}>
                    <CatalogRow
                      skill={s}
                      installing={
                        install.isPending && install.variables === s.name
                      }
                      disabled={install.isPending}
                      onInstall={() => install.mutate(s.name)}
                    />
                  </li>
                ))}
              </ul>
            </Collapsible>
          );
        })}
      </div>
    </section>
  );
}

// ── Skill-pack importer (bulk import from a git monorepo) ────────────────────

function PackResults({ result }: { result: PackInstallResult }) {
  return (
    <div className="space-y-2 rounded-lg border border-border bg-raised/40 p-3">
      {result.installed.length > 0 && (
        <div>
          <p className="flex items-center gap-1.5 text-xs font-semibold text-ok">
            <IconCheck size={13} />
            Imported {result.installed.length} skill
            {result.installed.length === 1 ? "" : "s"} — disabled
          </p>
          <p className="mt-1 font-mono text-xs break-words text-muted">
            {result.installed.join(", ")}
          </p>
        </div>
      )}
      {result.skipped.length > 0 && (
        <div>
          <p className="flex items-center gap-1.5 text-xs font-semibold text-warn">
            <IconAlert size={13} />
            Skipped {result.skipped.length}
          </p>
          <ul className="mt-1 space-y-0.5">
            {result.skipped.map((s) => (
              <li key={s.subdir} className="text-xs break-words text-faint">
                <span className="font-mono text-muted">{s.subdir}</span> —{" "}
                {s.reason}
              </li>
            ))}
          </ul>
        </div>
      )}
      <p className="text-xs text-faint">
        Enable the imported skills below to expose them to sessions.
      </p>
    </div>
  );
}

function SkillPackImporter() {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [gitUrl, setGitUrl] = useState("");
  const [scanned, setScanned] = useState<PackSkill[] | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState("");
  const [result, setResult] = useState<PackInstallResult | null>(null);

  const scan = useMutation({
    mutationFn: (url: string) => api.scanSkillPack(url),
    onSuccess: (skills) => {
      setScanned(skills);
      setSelected(new Set());
      setResult(null);
      if (skills.length === 0) {
        toast("info", "No installable skills found in that repository.");
      }
    },
    onError: (err) => toast("error", errorMessage(err)),
  });

  const install = useMutation({
    mutationFn: (vars: { url: string; subdirs: string[] }) =>
      api.installSkillPack(vars.url, vars.subdirs),
    onSuccess: (res) => {
      setResult(res);
      if (res.installed.length > 0) {
        toast(
          "success",
          `Imported ${res.installed.length} skill${res.installed.length === 1 ? "" : "s"} (disabled)`,
        );
        void queryClient.invalidateQueries({ queryKey: ["skills"] });
        void queryClient.invalidateQueries({ queryKey: ["skill-catalog"] });
      }
    },
    onError: (err) => toast("error", errorMessage(err)),
  });

  const filtered = (scanned ?? []).filter((s) => {
    const q = search.trim().toLowerCase();
    if (!q) return true;
    return (
      s.name.toLowerCase().includes(q) ||
      s.subdir.toLowerCase().includes(q) ||
      s.description.toLowerCase().includes(q)
    );
  });

  const toggle = (subdir: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(subdir)) next.delete(subdir);
      else if (next.size < PACK_SELECT_CAP) next.add(subdir);
      return next;
    });
  };

  const selectAllVisible = () => {
    setSelected((prev) => {
      const next = new Set(prev);
      for (const s of filtered) {
        if (next.size >= PACK_SELECT_CAP) break;
        next.add(s.subdir);
      }
      return next;
    });
  };

  const doScan = (e: FormEvent) => {
    e.preventDefault();
    if (!gitUrl.trim()) return;
    scan.mutate(gitUrl.trim());
  };

  const atCap = selected.size >= PACK_SELECT_CAP;

  return (
    <section className="mb-6 rounded-xl border border-border bg-surface">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="flex min-h-12 w-full cursor-pointer items-center gap-2 px-4 py-3 text-left"
      >
        {open ? (
          <IconChevronDown size={16} className="shrink-0 text-muted" />
        ) : (
          <IconChevronRight size={16} className="shrink-0 text-muted" />
        )}
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-semibold text-text">
            Import a skill pack
          </span>
          <span className="block text-xs text-faint">
            Bulk-import many skills from one git monorepo
          </span>
        </span>
      </button>

      {open && (
        <div className="space-y-3 border-t border-border p-4">
          <p className="rounded-md border border-info/30 bg-info/10 px-3 py-2 text-xs text-info">
            Pack-imported skills arrive <strong>disabled</strong> — enable the
            ones you want in the installed list below after importing.
          </p>

          <form onSubmit={doScan} className="space-y-3">
            <Field
              label="Repository URL"
              helper="An https git repo bundling many SKILL.md skill directories."
            >
              {(id) => (
                <TextInput
                  id={id}
                  type="url"
                  value={gitUrl}
                  onChange={(e) => setGitUrl(e.target.value)}
                  placeholder="https://github.com/affaan-m/ECC"
                  required
                />
              )}
            </Field>
            <Button
              type="submit"
              loading={scan.isPending}
              disabled={!gitUrl.trim()}
            >
              <IconSearch size={15} />
              {scan.isPending ? "Scanning repository…" : "Scan repository"}
            </Button>
          </form>

          {scanned && scanned.length > 0 && (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-xs font-medium text-muted">
                  {selected.size} selected
                  <span className="text-faint"> / {PACK_SELECT_CAP} max</span>
                </span>
                <div className="flex gap-2">
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={selectAllVisible}
                    disabled={atCap}
                  >
                    Select shown
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => setSelected(new Set())}
                    disabled={selected.size === 0}
                  >
                    Clear
                  </Button>
                </div>
              </div>

              <TextInput
                type="search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder={`Search ${scanned.length} skills…`}
              />

              <ul className="max-h-80 space-y-1.5 overflow-y-auto rounded-lg border border-border bg-bg/40 p-2">
                {filtered.length === 0 && (
                  <li className="px-2 py-3 text-center text-xs text-faint">
                    No skills match "{search}".
                  </li>
                )}
                {filtered.map((s) => {
                  const checked = selected.has(s.subdir);
                  const blocked = !checked && atCap;
                  return (
                    <li key={s.subdir}>
                      <label
                        className={cx(
                          "flex cursor-pointer items-start gap-2.5 rounded-md p-2 transition-colors",
                          checked ? "bg-accent/10" : "hover:bg-raised",
                          blocked && "cursor-not-allowed opacity-40",
                        )}
                      >
                        <input
                          type="checkbox"
                          checked={checked}
                          disabled={blocked}
                          onChange={() => toggle(s.subdir)}
                          className="mt-0.5 h-4 w-4 shrink-0 accent-accent"
                        />
                        <span className="min-w-0 flex-1">
                          <span className="flex items-center gap-1.5">
                            <span className="font-mono text-xs font-semibold text-text">
                              {s.name}
                            </span>
                            {s.note && (
                              <span
                                title={s.note}
                                className="inline-flex items-center gap-0.5 text-[10px] text-warn"
                              >
                                <IconAlert size={11} />
                                frontmatter
                              </span>
                            )}
                          </span>
                          {s.description && (
                            <span className="mt-0.5 block text-xs break-words text-muted">
                              {s.description}
                            </span>
                          )}
                          <span className="mt-0.5 block font-mono text-[10px] text-faint">
                            {s.subdir}
                          </span>
                        </span>
                      </label>
                    </li>
                  );
                })}
              </ul>

              <Button
                variant="primary"
                className="w-full"
                loading={install.isPending}
                disabled={selected.size === 0}
                onClick={() =>
                  install.mutate({
                    url: gitUrl.trim(),
                    subdirs: [...selected],
                  })
                }
              >
                {install.isPending
                  ? "Importing…"
                  : `Import ${selected.size || ""} skill${selected.size === 1 ? "" : "s"}`.trim()}
              </Button>
            </div>
          )}

          {scanned && scanned.length === 0 && !scan.isPending && (
            <p className="text-xs text-faint">
              That repository has no installable skills (no SKILL.md
              directories found).
            </p>
          )}

          {result && <PackResults result={result} />}
        </div>
      )}
    </section>
  );
}

export default function Skills() {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [gitUrl, setGitUrl] = useState("");
  const [subdir, setSubdir] = useState("");
  const [subdirOptions, setSubdirOptions] = useState<string[]>([]);
  const [installError, setInstallError] = useState<string | null>(null);

  const skills = useQuery({ queryKey: ["skills"], queryFn: api.listSkills });

  const install = useMutation({
    mutationFn: (vars: { git_url: string; subdir?: string }) =>
      api.installSkill(vars.git_url, vars.subdir),
    onSuccess: (skill) => {
      toast("success", `Installed "${skill.name}"`);
      setGitUrl("");
      setSubdir("");
      setSubdirOptions([]);
      setInstallError(null);
      void queryClient.invalidateQueries({ queryKey: ["skills"] });
    },
    onError: (err) => {
      const options = parseSubdirSuggestions(err);
      setSubdirOptions(options);
      setInstallError(
        options.length
          ? "This repository contains multiple skills — pick one:"
          : errorMessage(err),
      );
    },
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!gitUrl.trim()) return;
    setInstallError(null);
    install.mutate({
      git_url: gitUrl.trim(),
      subdir: subdir.trim() || undefined,
    });
  };

  return (
    <div>
      <PageHeader
        title="Skills"
        subtitle="Claude Code-format skills, exposed to sessions over MCP"
      />

      {/* Curated one-click catalog */}
      <SuggestedSkills />

      {/* Single install from git */}
      <form
        onSubmit={submit}
        className="mb-6 space-y-3 rounded-xl border border-border bg-surface p-4"
      >
        <Field
          label="Install from git"
          helper="Any repo with a SKILL.md (frontmatter: name, description). Community packs work too."
        >
          {(id) => (
            <TextInput
              id={id}
              type="url"
              value={gitUrl}
              onChange={(e) => setGitUrl(e.target.value)}
              placeholder="https://github.com/anthropics/skills"
              required
            />
          )}
        </Field>
        <Field label="Subdirectory (optional)" helper="For repos that bundle several skills.">
          {(id) => (
            <TextInput
              id={id}
              value={subdir}
              onChange={(e) => setSubdir(e.target.value)}
              placeholder="skills/pdf"
            />
          )}
        </Field>

        {installError && (
          <div role="alert" className="rounded-md border border-warn/30 bg-warn/10 p-3">
            <p className="text-sm break-words text-warn">{installError}</p>
            {subdirOptions.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {subdirOptions.map((option) => (
                  <button
                    key={option}
                    type="button"
                    disabled={install.isPending}
                    onClick={() => {
                      setSubdir(option);
                      install.mutate({
                        git_url: gitUrl.trim(),
                        subdir: option,
                      });
                    }}
                    className="min-h-9 cursor-pointer rounded-full border border-edge bg-raised px-3 font-mono text-xs text-text hover:border-accent hover:text-accent"
                  >
                    {option}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        <Button
          type="submit"
          variant="primary"
          className="w-full"
          loading={install.isPending}
          disabled={!gitUrl.trim()}
        >
          Install skill
        </Button>
      </form>

      {/* Bulk import from a monorepo */}
      <SkillPackImporter />

      {/* Installed list */}
      {skills.isLoading && <SkeletonList rows={3} />}
      {skills.isError && (
        <EmptyState
          icon="search"
          title="Couldn't load skills"
          hint={errorMessage(skills.error)}
          action={<Button onClick={() => void skills.refetch()}>Retry</Button>}
        />
      )}
      {skills.data && skills.data.length === 0 && (
        <EmptyState
          icon="spark"
          title="No skills installed"
          hint="Sessions see skill names and descriptions, and load one on demand — just like Claude Code."
        />
      )}
      {skills.data && skills.data.length > 0 && (
        <ul className="space-y-3">
          {skills.data.map((skill) => (
            <li key={skill.id}>
              <SkillCard skill={skill} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
