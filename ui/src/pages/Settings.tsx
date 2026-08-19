// System-wide settings: runtime knobs, budgets, and (admin only) whether new
// profiles may register. Everything personal lives on /profile now.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import { IconChevronRight, IconUser } from "../components/icons";
import { PageHeader } from "../components/layout";
import {
  Button,
  EmptyState,
  Field,
  SkeletonList,
  TextInput,
  Toggle,
} from "../components/ui";
import { useToast } from "../hooks/toast";
import { useCurrentUser } from "../lib/auth";

function RegistrationCard() {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const status = useQuery({
    queryKey: ["auth-status"],
    queryFn: api.authStatus,
    staleTime: 30_000,
  });

  const toggle = useMutation({
    mutationFn: (allow: boolean) => api.setRegistration(allow),
    onSuccess: ({ allow_registration }) => {
      void queryClient.invalidateQueries({ queryKey: ["auth-status"] });
      toast(
        "success",
        allow_registration
          ? "Registration is open"
          : "Registration is closed",
      );
    },
    onError: (err) => toast("error", errorMessage(err)),
  });

  return (
    <section className="rounded-xl border border-border bg-surface p-4">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-text">Registration</h2>
          <p className="mt-0.5 text-sm text-muted">
            Let people on your network create their own profiles.
          </p>
        </div>
        <Toggle
          checked={status.data?.allow_registration ?? false}
          onChange={(next) => toggle.mutate(next)}
          label="Allow registration"
          disabled={status.isLoading || toggle.isPending}
        />
      </div>
      {status.data && (
        <p className="mt-2 text-xs text-faint">
          {status.data.user_count}{" "}
          {status.data.user_count === 1 ? "profile" : "profiles"} on this Forge.
        </p>
      )}
    </section>
  );
}

export default function Settings() {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const user = useCurrentUser();

  const settings = useQuery({
    queryKey: ["settings"],
    queryFn: api.getSettings,
  });

  const [idleMin, setIdleMin] = useState("");
  const [cron, setCron] = useState("");

  useEffect(() => {
    if (settings.data) {
      setIdleMin(String(settings.data.session_idle_min));
      setCron(settings.data.registry_cron);
    }
  }, [settings.data]);

  const save = useMutation({
    mutationFn: () =>
      api.patchSettings({
        session_idle_min: Number(idleMin) || undefined,
        registry_cron: cron.trim() || undefined,
      }),
    onSuccess: () => {
      toast("success", "Settings saved");
      void queryClient.invalidateQueries({ queryKey: ["settings"] });
    },
    onError: (err) => toast("error", errorMessage(err)),
  });

  return (
    <div>
      <PageHeader title="Settings" subtitle="System-wide runtime knobs" />

      {settings.isLoading && <SkeletonList rows={3} />}
      {settings.isError && (
        <EmptyState
          icon="search"
          title="Couldn't load settings"
          hint={errorMessage(settings.error)}
          action={<Button onClick={() => void settings.refetch()}>Retry</Button>}
        />
      )}

      {settings.data && (
        <div className="space-y-4">
          {/* Personal bits moved to the profile */}
          <Link
            to="/profile"
            className="flex min-h-14 items-center gap-3 rounded-xl border border-border bg-surface px-4 hover:bg-raised"
          >
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-raised text-muted">
              <IconUser size={18} />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-sm font-medium text-text">
                Your profile
              </span>
              <span className="block text-xs text-faint">
                Display name, instructions, memory, password
              </span>
            </span>
            <IconChevronRight size={16} className="text-faint" />
          </Link>

          {/* Runtime */}
          <form
            onSubmit={(e) => {
              e.preventDefault();
              save.mutate();
            }}
            className="space-y-3 rounded-xl border border-border bg-surface p-4"
          >
            <h2 className="text-sm font-semibold text-text">Runtime</h2>
            <Field
              label="Idle timeout (minutes)"
              helper="Idle session containers are stopped after this long; they resume on demand."
            >
              {(id) => (
                <TextInput
                  id={id}
                  type="number"
                  min={5}
                  inputMode="numeric"
                  value={idleMin}
                  onChange={(e) => setIdleMin(e.target.value)}
                />
              )}
            </Field>
            <Field
              label="Registry scan schedule (cron)"
              helper='Five-field cron, e.g. "0 6 * * 1" = Mondays 06:00. The scan proposes new models that fit this hardware.'
            >
              {(id) => (
                <TextInput
                  id={id}
                  value={cron}
                  onChange={(e) => setCron(e.target.value)}
                  className="font-mono"
                  placeholder="0 6 * * 1"
                />
              )}
            </Field>
            <Button
              type="submit"
              variant="primary"
              loading={save.isPending}
              disabled={!idleMin || Number(idleMin) < 5 || !cron.trim()}
            >
              Save
            </Button>
          </form>

          {user?.is_admin && <RegistrationCard />}

          {/* Budgets (read-only) */}
          <section className="rounded-xl border border-border bg-surface p-4">
            <h2 className="mb-2 text-sm font-semibold text-text">Budgets</h2>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
              <dt className="text-muted">VRAM budget</dt>
              <dd className="text-right font-mono text-text">
                {settings.data.vram_budget_gb} GB
              </dd>
              <dt className="text-muted">RAM offload cap</dt>
              <dd className="text-right font-mono text-text">
                {settings.data.ram_offload_budget_gb} GB
              </dd>
              <dt className="text-muted">Max parallel sessions</dt>
              <dd className="text-right font-mono text-text">
                {settings.data.max_parallel_sessions}
              </dd>
              <dt className="text-muted">llama.cpp slots</dt>
              <dd className="text-right font-mono text-text">
                {settings.data.llamacpp_slots}
              </dd>
            </dl>
            <p className="mt-2 text-xs text-faint">
              Set via .env (FORGE_*) — restart the orchestrator to change.
            </p>
          </section>

          <p className="pb-2 text-center text-xs text-faint">
            Forge v0.1.0 · self-hosted agentic coding platform
          </p>
        </div>
      )}
    </div>
  );
}
