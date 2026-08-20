// The authorization-code flow leaves the SPA entirely (full-page redirect to
// the provider), so the connector kind + OAuth state must survive in
// sessionStorage: /oauth/callback gets only ?code=&state= back and needs to
// know which connector to finish the exchange for.

const KIND_KEY = "forge-oauth-kind";
const STATE_KEY = "forge-oauth-state";

/** Persist the pending sign-in just before redirecting to the provider. */
export function rememberPendingOAuth(kind: string, state: string): void {
  try {
    sessionStorage.setItem(KIND_KEY, kind);
    sessionStorage.setItem(STATE_KEY, state);
  } catch {
    // storage unavailable — the callback will show a readable error instead
  }
}

/** The sign-in the callback page should finish, if one is pending. */
export function readPendingOAuth(): { kind: string; state: string } | null {
  try {
    const kind = sessionStorage.getItem(KIND_KEY);
    if (!kind) return null;
    return { kind, state: sessionStorage.getItem(STATE_KEY) ?? "" };
  } catch {
    return null;
  }
}

export function clearPendingOAuth(): void {
  try {
    sessionStorage.removeItem(KIND_KEY);
    sessionStorage.removeItem(STATE_KEY);
  } catch {
    // ignore
  }
}
