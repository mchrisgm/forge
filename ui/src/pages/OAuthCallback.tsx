// Redirect target of the authorization-code OAuth flow (Hugging Face PKCE).
// The provider sends the browser back to /oauth/callback?code=…&state=… after
// consent; the connector kind was stashed in sessionStorage before we left
// (lib/oauth.ts), because the query string alone doesn't identify it. This
// page finishes the exchange and returns to the connectors list.

import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import { IconCheck } from "../components/icons";
import { PageHeader } from "../components/layout";
import { Button, Spinner } from "../components/ui";
import { clearPendingOAuth, readPendingOAuth } from "../lib/oauth";

type Phase =
  | { phase: "exchanging" }
  | { phase: "connected"; account: string }
  | { phase: "error"; message: string };

export default function OAuthCallback() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [state, setState] = useState<Phase>({ phase: "exchanging" });
  // The exchange is single-use server-side — never fire it twice (StrictMode
  // double-invokes effects in dev, and back/forward can revisit this URL).
  const fired = useRef(false);

  useEffect(() => {
    if (fired.current) return;
    fired.current = true;

    const fail = (message: string) => setState({ phase: "error", message });

    const providerError = params.get("error");
    if (providerError) {
      clearPendingOAuth();
      fail(
        params.get("error_description") ||
          (providerError === "access_denied"
            ? "Sign-in was declined on the provider page."
            : `The provider returned an error: ${providerError}`),
      );
      return;
    }

    const code = params.get("code") ?? "";
    const stateParam = params.get("state") ?? "";
    const pending = readPendingOAuth();
    if (!code || !stateParam) {
      fail("The provider didn't send a sign-in code — try connecting again.");
      return;
    }
    if (!pending) {
      fail(
        "This sign-in wasn't started from this browser session — start again from the Connectors page.",
      );
      return;
    }

    api.oauthExchange(pending.kind, code, stateParam).then(
      (res) => {
        clearPendingOAuth();
        setState({ phase: "connected", account: res.account });
        void queryClient.invalidateQueries({ queryKey: ["connectors"] });
        void queryClient.invalidateQueries({ queryKey: ["github-repos"] });
        window.setTimeout(() => {
          navigate("/connectors", { replace: true });
        }, 1200);
      },
      (err: unknown) => {
        clearPendingOAuth();
        fail(errorMessage(err));
      },
    );
  }, [params, navigate, queryClient]);

  return (
    <div>
      <PageHeader title="Connecting account" subtitle="Finishing OAuth sign-in" />
      <div className="rounded-xl border border-border bg-surface p-5">
        {state.phase === "exchanging" && (
          <p className="flex items-center gap-3 text-sm text-muted">
            <Spinner size={18} />
            Completing sign-in…
          </p>
        )}
        {state.phase === "connected" && (
          <div className="flex items-center gap-3">
            <IconCheck size={18} className="shrink-0 text-ok" />
            <p className="text-sm text-text">
              Connected{state.account ? ` as ${state.account}` : ""} —
              returning…
            </p>
          </div>
        )}
        {state.phase === "error" && (
          <div className="space-y-4">
            <p className="rounded-md bg-danger/10 px-3 py-2 text-sm break-words text-danger">
              {state.message}
            </p>
            <Link to="/connectors">
              <Button variant="primary">Back to connectors</Button>
            </Link>
          </div>
        )}
      </div>
    </div>
  );
}
