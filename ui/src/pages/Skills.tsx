import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { api, ApiError, errorMessage } from "../api/client";
import type { Skill } from "../api/types";
import { IconExternal, IconTrash } from "../components/icons";
import { PageHeader } from "../components/layout";
import {
  Button,
  ConfirmDialog,
  EmptyState,
  Field,
  SkeletonList,
  TextInput,
  Toggle,
} from "../components/ui";
import { useToast } from "../hooks/toast";
import { relativeTime } from "../lib/utils";

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

      {/* Install form */}
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
