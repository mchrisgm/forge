import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import { IconLogout } from "../components/icons";
import { PageHeader } from "../components/layout";
import {
  Button,
  EmptyState,
  Field,
  SkeletonList,
  TextInput,
} from "../components/ui";
import { useToast } from "../hooks/toast";
import { clearToken } from "../lib/auth";

function PasswordForm() {
  const { toast } = useToast();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");

  const change = useMutation({
    mutationFn: () => api.changePassword(current, next),
    onSuccess: () => {
      toast("success", "Password changed");
      setCurrent("");
      setNext("");
    },
    onError: (err) => toast("error", errorMessage(err)),
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (current && next.length >= 8) change.mutate();
  };

  return (
    <form
      onSubmit={submit}
      className="space-y-3 rounded-xl border border-border bg-surface p-4"
    >
      <h2 className="text-sm font-semibold text-text">Password</h2>
      <Field label="Current password">
        {(id) => (
          <TextInput
            id={id}
            type="password"
            autoComplete="current-password"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
            required
          />
        )}
      </Field>
      <Field label="New password" helper="At least 8 characters.">
        {(id) => (
          <TextInput
            id={id}
            type="password"
            autoComplete="new-password"
            value={next}
            onChange={(e) => setNext(e.target.value)}
            minLength={8}
            required
          />
        )}
      </Field>
      <Button
        type="submit"
        variant="primary"
        loading={change.isPending}
        disabled={!current || next.length < 8}
      >
        Change password
      </Button>
    </form>
  );
}

export default function Settings() {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const navigate = useNavigate();

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

  const logout = () => {
    clearToken();
    navigate("/login", { replace: true });
  };

  return (
    <div>
      <PageHeader title="Settings" subtitle="Runtime knobs and access" />

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

          <PasswordForm />

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

          <Button variant="danger" className="w-full" onClick={logout}>
            <IconLogout size={16} />
            Log out
          </Button>

          <p className="pb-2 text-center text-xs text-faint">
            Forge v0.1.0 · self-hosted agentic coding platform
          </p>
        </div>
      )}
    </div>
  );
}
