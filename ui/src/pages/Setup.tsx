// First-run setup wizard: create the first profile (which becomes admin),
// then point at the next steps. /api/auth/status.setup_required routes here.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import {
  IconChat,
  IconCheck,
  IconCube,
  IconFlame,
  IconGlobe,
} from "../components/icons";
import { Button, Field, TextInput } from "../components/ui";
import { setAuth } from "../lib/auth";
import { cx } from "../lib/utils";

function StepDots({ step }: { step: 1 | 2 }) {
  return (
    <div className="flex items-center justify-center gap-2" aria-hidden>
      {[1, 2].map((s) => (
        <span
          key={s}
          className={cx(
            "h-1.5 rounded-full transition-all duration-300",
            s === step ? "w-6 bg-accent" : "w-1.5 bg-edge",
          )}
        />
      ))}
    </div>
  );
}

function CreateAdminStep({ onDone }: { onDone: () => void }) {
  const queryClient = useQueryClient();
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");

  const register = useMutation({
    mutationFn: () => api.register(username.trim(), password, displayName.trim()),
    onSuccess: ({ token, user }) => {
      setAuth(token, user);
      void queryClient.invalidateQueries({ queryKey: ["auth-status"] });
      onDone();
    },
  });

  const valid = username.trim().length >= 3 && password.length >= 6;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (valid) register.mutate();
  };

  return (
    <>
      <div className="mb-8 text-center">
        <span className="mb-4 inline-flex h-14 w-14 items-center justify-center rounded-2xl bg-accent/15 text-accent">
          <IconFlame size={30} />
        </span>
        <h1 className="text-2xl font-bold tracking-tight text-text">
          Welcome to Forge
        </h1>
        <p className="mx-auto mt-2 max-w-sm text-sm text-muted">
          Your own AI workbench is almost ready. Create the first profile to
          get started — it becomes the admin for this Forge.
        </p>
      </div>

      <form onSubmit={submit} className="space-y-4">
        <Field
          label="Username"
          helper="3–32 chars: lowercase letters, digits, - and _."
        >
          {(id) => (
            <TextInput
              id={id}
              value={username}
              onChange={(e) => setUsername(e.target.value.toLowerCase())}
              autoComplete="username"
              autoCapitalize="none"
              spellCheck={false}
              placeholder="ada"
              autoFocus
              required
            />
          )}
        </Field>
        <Field label="Display name" helper="Optional — how you appear to others.">
          {(id) => (
            <TextInput
              id={id}
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              autoComplete="name"
              placeholder="Ada Lovelace"
            />
          )}
        </Field>
        <Field label="Password" helper="At least 6 characters.">
          {(id) => (
            <TextInput
              id={id}
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="new-password"
              minLength={6}
              required
            />
          )}
        </Field>

        {register.isError && (
          <p role="alert" className="text-sm text-danger">
            {errorMessage(register.error)}
          </p>
        )}

        <Button
          type="submit"
          variant="primary"
          className="w-full"
          loading={register.isPending}
          disabled={!valid}
        >
          Create admin profile
        </Button>
      </form>
    </>
  );
}

function DoneStep() {
  const navigate = useNavigate();
  const origin = window.location.origin;

  return (
    <>
      <div className="mb-8 text-center">
        <span className="mb-4 inline-flex h-14 w-14 items-center justify-center rounded-2xl bg-ok/15 text-ok">
          <IconCheck size={30} />
        </span>
        <h1 className="text-2xl font-bold tracking-tight text-text">
          You're all set
        </h1>
        <p className="mx-auto mt-2 max-w-sm text-sm text-muted">
          Your admin profile is ready. Two things make this Forge come alive:
        </p>
      </div>

      <ol className="mb-8 space-y-3">
        <li className="flex items-start gap-3 rounded-xl border border-border bg-surface p-4">
          <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-raised text-accent">
            <IconCube size={18} />
          </span>
          <span>
            <span className="block text-sm font-medium text-text">
              Download a model
            </span>
            <span className="mt-0.5 block text-sm text-muted">
              Head to Models and pull one that fits your hardware — it powers
              every chat and coding session.
            </span>
          </span>
        </li>
        <li className="flex items-start gap-3 rounded-xl border border-border bg-surface p-4">
          <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-raised text-accent">
            <IconGlobe size={18} />
          </span>
          <span>
            <span className="block text-sm font-medium text-text">
              Invite the others
            </span>
            <span className="mt-0.5 block text-sm text-muted">
              Anyone on your network can visit{" "}
              <code className="rounded bg-overlay px-1 py-0.5 font-mono text-xs text-text">
                {origin}
              </code>{" "}
              and register their own profile.
            </span>
          </span>
        </li>
      </ol>

      <div className="space-y-3">
        <Button
          variant="primary"
          className="w-full"
          onClick={() => navigate("/models", { replace: true })}
        >
          <IconCube size={16} />
          Go to Models
        </Button>
        <Button
          className="w-full"
          onClick={() => navigate("/chats", { replace: true })}
        >
          <IconChat size={16} />
          Open Forge
        </Button>
      </div>
    </>
  );
}

export default function Setup() {
  const [step, setStep] = useState<1 | 2>(1);
  const status = useQuery({
    queryKey: ["auth-status"],
    queryFn: api.authStatus,
    staleTime: 30_000,
  });

  // Someone else already finished setup — this screen no longer applies.
  if (step === 1 && status.data && !status.data.setup_required) {
    return <Navigate to="/login" replace />;
  }

  return (
    <div className="flex min-h-dvh items-center justify-center bg-bg px-6 pt-safe pb-safe">
      <div className="w-full max-w-sm py-10">
        {step === 1 ? (
          <CreateAdminStep onDone={() => setStep(2)} />
        ) : (
          <DoneStep />
        )}
        <div className="mt-10">
          <StepDots step={step} />
        </div>
      </div>
    </div>
  );
}
