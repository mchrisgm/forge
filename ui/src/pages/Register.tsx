import { useMutation, useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import { IconFlame } from "../components/icons";
import { Button, Field, TextInput } from "../components/ui";
import { getToken, setAuth } from "../lib/auth";

export default function Register() {
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");

  const status = useQuery({
    queryKey: ["auth-status"],
    queryFn: api.authStatus,
    staleTime: 30_000,
  });

  const register = useMutation({
    mutationFn: () => api.register(username.trim(), password, displayName.trim()),
    onSuccess: ({ token, user }) => {
      setAuth(token, user);
      navigate("/chats", { replace: true });
    },
  });

  if (status.data?.setup_required) return <Navigate to="/setup" replace />;
  if (getToken()) return <Navigate to="/chats" replace />;

  const closed = status.data ? !status.data.allow_registration : false;
  const valid = username.trim().length >= 3 && password.length >= 6;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (valid) register.mutate();
  };

  return (
    <div className="flex min-h-dvh items-center justify-center bg-bg px-6 pt-safe pb-safe">
      <div className="w-full max-w-xs">
        <div className="mb-10 flex flex-col items-center">
          <span className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-accent/15 text-accent">
            <IconFlame size={30} />
          </span>
          <h1 className="text-2xl font-bold tracking-tight text-text">
            Join this Forge
          </h1>
          <p className="mt-1 text-center text-sm text-muted">
            Your own profile, chats and memory
          </p>
        </div>

        {closed ? (
          <div className="rounded-xl border border-border bg-surface p-5 text-center">
            <p className="text-sm font-medium text-text">
              Registration is closed
            </p>
            <p className="mt-1.5 text-sm text-muted">
              The admin has paused new sign-ups on this Forge. Ask them to open
              registration in Settings, then try again.
            </p>
            <Link
              to="/login"
              className="mt-4 inline-block text-sm font-medium text-accent underline-offset-2 hover:underline"
            >
              Back to sign in
            </Link>
          </div>
        ) : (
          <>
            <form onSubmit={submit} className="space-y-4">
              <Field
                label="Username"
                helper="3–32 chars: lowercase letters, digits, - and _."
              >
                {(id) => (
                  <TextInput
                    id={id}
                    autoComplete="username"
                    autoCapitalize="none"
                    spellCheck={false}
                    autoFocus
                    value={username}
                    onChange={(e) => setUsername(e.target.value.toLowerCase())}
                    placeholder="ada"
                    required
                  />
                )}
              </Field>
              <Field label="Display name" helper="Optional — how you appear to others.">
                {(id) => (
                  <TextInput
                    id={id}
                    autoComplete="name"
                    value={displayName}
                    onChange={(e) => setDisplayName(e.target.value)}
                    placeholder="Ada Lovelace"
                  />
                )}
              </Field>
              <Field label="Password" helper="At least 6 characters.">
                {(id) => (
                  <TextInput
                    id={id}
                    type="password"
                    autoComplete="new-password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
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
                Create profile
              </Button>
            </form>

            <p className="mt-8 text-center text-sm text-muted">
              Already have one?{" "}
              <Link
                to="/login"
                className="font-medium text-accent underline-offset-2 hover:underline"
              >
                Sign in
              </Link>
            </p>
          </>
        )}
      </div>
    </div>
  );
}
