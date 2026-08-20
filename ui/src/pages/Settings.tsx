// System-wide settings: runtime knobs, budgets, and (admin only) whether new
// profiles may register. Everything personal lives on /profile now.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import type { HeadroomStatus, OAuthAppSettings } from "../api/types";
import {
  IconChevronRight,
  IconExternal,
  IconUser,
} from "../components/icons";
import { PageHeader } from "../components/layout";
import {
  Button,
  Chip,
  EmptyState,
  Field,
  Select,
  SkeletonList,
  TextArea,
  TextInput,
  Toggle,
} from "../components/ui";
import { useToast } from "../hooks/toast";
import { useCurrentUser } from "../lib/auth";
import { cx, opencodeModelId } from "../lib/utils";

const MASK = "••••••";

function HeadroomCard({ headroom }: { headroom: HeadroomStatus }) {
  const { toast } = useToast();
  const queryClient = useQueryClient();

  const toggle = useMutation({
    mutationFn: (enabled: boolean) =>
      api.patchSettings({ headroom_enabled: enabled }),
    onSuccess: (data) => {
      // PATCH returns the full settings payload with a freshly re-probed
      // headroom status — swap it in so the status line updates at once.
      queryClient.setQueryData(["settings"], data);
      void queryClient.invalidateQueries({ queryKey: ["settings"] });
    },
    onError: (err) => toast("error", errorMessage(err)),
  });

  const active = headroom.enabled && headroom.healthy === true;
  const degraded = headroom.enabled && !headroom.healthy;

  return (
    <section className="rounded-xl border border-border bg-surface p-4">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-text">
            Context compression (Headroom)
          </h2>
          <p className="mt-0.5 text-sm text-muted">
            Compresses tool outputs and context before the model to save VRAM.
          </p>
        </div>
        <Toggle
          checked={headroom.enabled}
          onChange={(next) => toggle.mutate(next)}
          label="Context compression"
          disabled={toggle.isPending}
        />
      </div>
      <p className="mt-2 flex items-center gap-1.5 text-xs">
        <span
          aria-hidden
          className={cx(
            "h-1.5 w-1.5 rounded-full",
            active ? "bg-ok" : degraded ? "bg-warn" : "bg-faint",
          )}
        />
        <span
          className={cx(
            "font-medium",
            active ? "text-ok" : degraded ? "text-warn" : "text-faint",
          )}
        >
          {active
            ? "Active"
            : degraded
              ? "Enabled, proxy unreachable — using direct path"
              : "Off"}
        </span>
      </p>
    </section>
  );
}

// ── Chat system prompt (admin) ──────────────────────────────────────────────
// PATCH {chat_system_prompt} — "" (or the default verbatim) restores the
// built-in default; the server reports the effective prompt + customized flag.

function ChatSystemPromptCard({
  prompt,
  customized,
  defaultPrompt,
}: {
  prompt: string;
  customized: boolean;
  defaultPrompt: string;
}) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState(prompt);

  useEffect(() => setDraft(prompt), [prompt]);

  const refresh = (data: Awaited<ReturnType<typeof api.patchSettings>>) => {
    // PATCH returns the full payload with the re-resolved effective prompt —
    // swap it in so the textarea and Customized chip update at once.
    queryClient.setQueryData(["settings"], data);
    void queryClient.invalidateQueries({ queryKey: ["settings"] });
  };

  const save = useMutation({
    mutationFn: () => api.patchSettings({ chat_system_prompt: draft }),
    onSuccess: (data) => {
      refresh(data);
      toast("success", "Chat system prompt saved");
    },
    onError: (err) => toast("error", errorMessage(err)),
  });

  const restore = useMutation({
    mutationFn: () => api.patchSettings({ chat_system_prompt: "" }),
    onSuccess: (data) => {
      refresh(data);
      toast("success", "Default chat system prompt restored");
    },
    onError: (err) => toast("error", errorMessage(err)),
  });

  const busy = save.isPending || restore.isPending;
  const dirty = draft !== prompt;

  return (
    <section className="rounded-xl border border-border bg-surface p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold text-text">Chat system prompt</h2>
        {customized && <Chip color="text-info">Customized</Chip>}
      </div>
      <p className="mt-0.5 mb-3 text-sm text-muted">
        Injected as the system prompt for every text model in chat — each
        profile's personal instructions stack on top of it.
      </p>
      <TextArea
        aria-label="Chat system prompt"
        rows={12}
        spellCheck={false}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        placeholder={defaultPrompt}
        className="font-mono text-xs leading-relaxed"
      />
      <div className="mt-2 flex flex-wrap items-center justify-between gap-3">
        <span className="text-xs text-faint">
          {draft.length.toLocaleString()} characters
        </span>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="ghost"
            disabled={!customized || busy}
            loading={restore.isPending}
            onClick={() => restore.mutate()}
          >
            Restore default
          </Button>
          <Button
            size="sm"
            variant="primary"
            disabled={!dirty || busy}
            loading={save.isPending}
            onClick={() => save.mutate()}
          >
            Save
          </Button>
        </div>
      </div>
    </section>
  );
}

// ── Auto-routing model (admin) ──────────────────────────────────────────────
// PATCH {router_model_slug} — the tiny always-on model that reads each prompt
// in "Auto" chats and picks the answering model. "" disables LLM routing
// (auto then falls back to deterministic picks).

function AutoRoutingCard({
  routerSlug,
  routerReady,
}: {
  routerSlug: string;
  routerReady: boolean;
}) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState(routerSlug);

  useEffect(() => setDraft(routerSlug), [routerSlug]);

  const models = useQuery({ queryKey: ["models"], queryFn: api.listModels });
  // Only downloaded llama.cpp (GGUF) models qualify — the router has to be
  // tiny and always loadable next to whatever else is serving.
  const candidates = useMemo(
    () =>
      (models.data ?? []).filter(
        (m) => m.engine === "llamacpp" && m.status === "ready",
      ),
    [models.data],
  );
  const slugOf = (m: (typeof candidates)[number]) =>
    opencodeModelId(m.display_name, m.id);
  // A stored slug that no longer matches a ready model still shows in the
  // select (with the warning chip) instead of silently snapping elsewhere.
  const knownDraft = !draft || candidates.some((m) => slugOf(m) === draft);

  const save = useMutation({
    mutationFn: () => api.patchSettings({ router_model_slug: draft.trim() }),
    onSuccess: (data) => {
      // PATCH returns the full payload with the re-resolved ready flag.
      queryClient.setQueryData(["settings"], data);
      void queryClient.invalidateQueries({ queryKey: ["settings"] });
      // The chat composer's "Auto" option keys off /api/chat/status.
      void queryClient.invalidateQueries({ queryKey: ["chat-status"] });
      toast(
        "success",
        draft.trim()
          ? "Auto-routing model saved"
          : "LLM routing disabled — Auto uses deterministic picks",
      );
    },
    onError: (err) => toast("error", errorMessage(err)),
  });

  const dirty = draft !== routerSlug;

  return (
    <section className="rounded-xl border border-border bg-surface p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold text-text">Auto-routing model</h2>
        {routerSlug && !routerReady && (
          <Chip color="text-warn">not downloaded/ready</Chip>
        )}
      </div>
      <p className="mt-0.5 mb-3 text-sm text-muted">
        A tiny always-on model (TinyLlama / Qwen 0.6B class — it must be a
        downloaded llama.cpp/GGUF model) reads each prompt in "Auto" chats and
        picks the answering model. Disabled = Auto falls back to deterministic
        picks.
      </p>
      {routerSlug && !routerReady && (
        <p className="mb-3 text-xs text-warn">
          The configured router model isn't downloaded/ready — auto falls back
          to deterministic picks until it is.
        </p>
      )}
      <Field
        label="Router model"
        helper="Only ready llama.cpp (GGUF) models are listed."
      >
        {(id) => (
          <Select
            id={id}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
          >
            <option value="">Disabled — deterministic picks</option>
            {!knownDraft && (
              <option value={draft}>{draft} (not in the library)</option>
            )}
            {candidates.map((m) => (
              <option key={m.id} value={slugOf(m)}>
                {m.display_name} · {m.params_b} B
              </option>
            ))}
          </Select>
        )}
      </Field>
      <div className="mt-3 flex justify-end">
        <Button
          size="sm"
          variant="primary"
          disabled={!dirty}
          loading={save.isPending}
          onClick={() => save.mutate()}
        >
          Save
        </Button>
      </div>
    </section>
  );
}

// ── OAuth sign-in apps (admin) ──────────────────────────────────────────────
// PATCH /api/settings takes flat keys per provider; this maps connector kind
// → those keys. Providers the UI doesn't know are skipped (env-only).

const OAUTH_SETTING_KEYS: Record<
  string,
  {
    clientId: "github_oauth_client_id" | "hf_oauth_client_id";
    secret?: "hf_oauth_client_secret";
  }
> = {
  github: { clientId: "github_oauth_client_id" },
  "hugging-face": { clientId: "hf_oauth_client_id", secret: "hf_oauth_client_secret" },
};

function OAuthProviderForm({
  kind,
  app,
}: {
  kind: string;
  app: OAuthAppSettings;
}) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const keys = OAUTH_SETTING_KEYS[kind];
  const [clientId, setClientId] = useState(app.client_id);
  // "" = unchanged; the stored secret is never echoed back by the server.
  const [secret, setSecret] = useState("");

  useEffect(() => setClientId(app.client_id), [app.client_id]);

  const refresh = (data: Awaited<ReturnType<typeof api.patchSettings>>) => {
    queryClient.setQueryData(["settings"], data);
    void queryClient.invalidateQueries({ queryKey: ["settings"] });
    // Client-ID changes flip the connectors' oauth.ready flag.
    void queryClient.invalidateQueries({ queryKey: ["connectors"] });
  };

  const save = useMutation({
    mutationFn: () => {
      const body: Parameters<typeof api.patchSettings>[0] = {};
      body[keys.clientId] = clientId.trim();
      if (keys.secret && secret) body[keys.secret] = secret;
      return api.patchSettings(body);
    },
    onSuccess: (data) => {
      refresh(data);
      setSecret("");
      toast("success", `${app.label} sign-in app saved`);
    },
    onError: (err) => toast("error", errorMessage(err)),
  });

  const clearSecret = useMutation({
    mutationFn: () => api.patchSettings({ hf_oauth_client_secret: "" }),
    onSuccess: (data) => {
      refresh(data);
      setSecret("");
      toast("success", "Client secret cleared");
    },
    onError: (err) => toast("error", errorMessage(err)),
  });

  const dirty = clientId.trim() !== app.client_id || secret !== "";

  return (
    <div className="space-y-3 border-t border-border pt-3 first:border-t-0 first:pt-0">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-medium text-text">{app.label}</h3>
        <span
          className={cx(
            "text-xs",
            app.client_id ? "text-ok" : "text-faint",
          )}
        >
          {app.client_id ? "configured" : "not configured"}
        </span>
      </div>
      <Field label="Client ID" helper={app.setup_note}>
        {(id) => (
          <TextInput
            id={id}
            autoComplete="off"
            value={clientId}
            onChange={(e) => setClientId(e.target.value)}
            placeholder={kind === "github" ? "Iv1.…" : "client id"}
            className="font-mono"
          />
        )}
      </Field>
      {keys.secret && app.needs_secret && (
        <Field
          label="Client secret"
          helper={
            app.has_secret
              ? "Configured — leave blank to keep it."
              : "Optional — only if the provider issued one for your app."
          }
        >
          {(id) => (
            <div className="flex gap-2">
              <TextInput
                id={id}
                type="password"
                autoComplete="off"
                value={secret}
                onChange={(e) => setSecret(e.target.value)}
                placeholder={app.has_secret ? MASK : ""}
              />
              {app.has_secret && (
                <Button
                  className="shrink-0"
                  variant="ghost"
                  aria-label={`Clear ${app.label} client secret`}
                  loading={clearSecret.isPending}
                  onClick={() => clearSecret.mutate()}
                >
                  Clear
                </Button>
              )}
            </div>
          )}
        </Field>
      )}
      <div className="flex items-center justify-between gap-3">
        {app.setup_url ? (
          <a
            href={app.setup_url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex min-h-9 items-center gap-1 text-xs text-info underline underline-offset-2"
          >
            {app.label} developer settings
            <IconExternal size={12} />
          </a>
        ) : (
          <span />
        )}
        <Button
          size="sm"
          variant="primary"
          disabled={!dirty}
          loading={save.isPending}
          onClick={() => save.mutate()}
        >
          Save
        </Button>
      </div>
    </div>
  );
}

function OAuthAppsCard({ oauth }: { oauth: Record<string, OAuthAppSettings> }) {
  const providers = Object.entries(oauth).filter(
    ([kind]) => OAUTH_SETTING_KEYS[kind] !== undefined,
  );
  if (providers.length === 0) return null;
  return (
    <section className="rounded-xl border border-border bg-surface p-4">
      <h2 className="text-sm font-semibold text-text">OAuth sign-in apps</h2>
      <p className="mt-0.5 mb-4 text-sm text-muted">
        Lets each profile connect their own account on the Connectors page
        instead of pasting tokens.
      </p>
      <div className="space-y-4">
        {providers.map(([kind, app]) => (
          <OAuthProviderForm key={kind} kind={kind} app={app} />
        ))}
      </div>
    </section>
  );
}

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

          <HeadroomCard headroom={settings.data.headroom} />

          {user?.is_admin && (
            <ChatSystemPromptCard
              prompt={settings.data.chat_system_prompt}
              customized={settings.data.chat_system_prompt_customized}
              defaultPrompt={settings.data.chat_system_prompt_default}
            />
          )}

          {user?.is_admin && (
            <AutoRoutingCard
              routerSlug={settings.data.router_model_slug ?? ""}
              routerReady={settings.data.router_model_ready ?? false}
            />
          )}

          {user?.is_admin && settings.data.oauth && (
            <OAuthAppsCard oauth={settings.data.oauth} />
          )}

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
