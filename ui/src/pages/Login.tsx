import { useMutation } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import { IconFlame } from "../components/icons";
import { Button, TextInput } from "../components/ui";
import { getToken, setToken } from "../lib/auth";
import { cx } from "../lib/utils";

export default function Login() {
  const navigate = useNavigate();
  const [password, setPassword] = useState("");
  const [shake, setShake] = useState(false);

  const login = useMutation({
    mutationFn: (pw: string) => api.login(pw),
    onSuccess: ({ token }) => {
      setToken(token);
      navigate("/sessions", { replace: true });
    },
    onError: () => {
      setShake(true);
      window.setTimeout(() => setShake(false), 450);
    },
  });

  if (getToken()) return <Navigate to="/sessions" replace />;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (password) login.mutate(password);
  };

  return (
    <div className="flex min-h-dvh items-center justify-center bg-bg px-6 pt-safe pb-safe">
      <div className="w-full max-w-xs">
        <div className="mb-10 flex flex-col items-center">
          <span className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-accent/15 text-accent">
            <IconFlame size={30} />
          </span>
          <h1 className="text-2xl font-bold tracking-tight text-text">Forge</h1>
          <p className="mt-1 text-sm text-muted">
            Self-hosted agentic coding
          </p>
        </div>

        <form
          onSubmit={submit}
          className={cx("space-y-4", shake && "animate-shake")}
        >
          <div>
            <label htmlFor="password" className="sr-only">
              Password
            </label>
            <TextInput
              id="password"
              type="password"
              autoComplete="current-password"
              autoFocus
              placeholder="Password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              aria-invalid={login.isError}
              className={cx(
                "text-center",
                login.isError && "border-danger focus:border-danger",
              )}
            />
            {login.isError && (
              <p role="alert" className="mt-2 text-center text-xs text-danger">
                {errorMessage(login.error)}
              </p>
            )}
          </div>
          <Button
            type="submit"
            variant="primary"
            className="w-full"
            loading={login.isPending}
            disabled={!password}
          >
            Unlock
          </Button>
        </form>

        <p className="mt-8 text-center text-xs text-faint">
          Set via FORGE_PASSWORD · changeable in Settings
        </p>
      </div>
    </div>
  );
}
