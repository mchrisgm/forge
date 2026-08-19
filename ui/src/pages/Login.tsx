import { useMutation, useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import { IconFlame } from "../components/icons";
import { Button, Field, TextInput } from "../components/ui";
import { getToken, setAuth } from "../lib/auth";
import { cx } from "../lib/utils";

export default function Login() {
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [shake, setShake] = useState(false);

  const status = useQuery({
    queryKey: ["auth-status"],
    queryFn: api.authStatus,
    staleTime: 30_000,
  });

  const login = useMutation({
    mutationFn: () => api.login(username.trim(), password),
    onSuccess: ({ token, user }) => {
      setAuth(token, user);
      navigate("/chats", { replace: true });
    },
    onError: () => {
      setShake(true);
      window.setTimeout(() => setShake(false), 450);
    },
  });

  if (status.data?.setup_required) return <Navigate to="/setup" replace />;
  if (getToken()) return <Navigate to="/chats" replace />;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (username.trim() && password) login.mutate();
  };

  return (
    <div className="flex min-h-dvh items-center justify-center bg-bg px-6 pt-safe pb-safe">
      <div className="w-full max-w-xs">
        <div className="mb-10 flex flex-col items-center">
          <span className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-accent/15 text-accent">
            <IconFlame size={30} />
          </span>
          <h1 className="text-2xl font-bold tracking-tight text-text">Forge</h1>
          <p className="mt-1 text-sm text-muted">Sign in to your profile</p>
        </div>

        <form
          onSubmit={submit}
          className={cx("space-y-4", shake && "animate-shake")}
        >
          <Field label="Username">
            {(id) => (
              <TextInput
                id={id}
                autoComplete="username"
                autoCapitalize="none"
                spellCheck={false}
                autoFocus
                value={username}
                onChange={(e) => setUsername(e.target.value.toLowerCase())}
                aria-invalid={login.isError}
                required
              />
            )}
          </Field>
          <Field label="Password">
            {(id) => (
              <TextInput
                id={id}
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                aria-invalid={login.isError}
                className={cx(login.isError && "border-danger focus:border-danger")}
                required
              />
            )}
          </Field>
          {login.isError && (
            <p role="alert" className="text-center text-xs text-danger">
              {errorMessage(login.error)}
            </p>
          )}
          <Button
            type="submit"
            variant="primary"
            className="w-full"
            loading={login.isPending}
            disabled={!username.trim() || !password}
          >
            Sign in
          </Button>
        </form>

        {status.data?.allow_registration ? (
          <p className="mt-8 text-center text-sm text-muted">
            New here?{" "}
            <Link
              to="/register"
              className="font-medium text-accent underline-offset-2 hover:underline"
            >
              Create a profile
            </Link>
          </p>
        ) : (
          <p className="mt-8 text-center text-xs text-faint">
            Registration is closed — ask the admin for an account.
          </p>
        )}
      </div>
    </div>
  );
}
