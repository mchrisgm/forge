// Multi-user auth state: bearer token + the signed-in user's profile, both
// mirrored in localStorage so a reload stays signed in. The old single-
// password flow is gone — login/register return {token, user} pairs.

import { useSyncExternalStore } from "react";
import type { UserProfile } from "../api/types";

const TOKEN_KEY = "forge_token";
const USER_KEY = "forge_user";

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string): void {
  try {
    localStorage.setItem(TOKEN_KEY, token);
  } catch {
    // storage unavailable (private mode) — session-only auth still works
  }
}

export function clearToken(): void {
  try {
    localStorage.removeItem(TOKEN_KEY);
  } catch {
    // ignore
  }
}

// ── Current user profile ────────────────────────────────────────────────────

/** Fired whenever the stored profile changes (login, PATCH /users/me, logout). */
export const USER_EVENT = "forge:user";

let cachedUser: UserProfile | null | undefined;

function readStoredUser(): UserProfile | null {
  try {
    const raw = localStorage.getItem(USER_KEY);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (parsed && typeof parsed === "object" && "username" in parsed) {
      return parsed as UserProfile;
    }
  } catch {
    // corrupt/unavailable storage — treat as signed out profile-wise
  }
  return null;
}

export function getStoredUser(): UserProfile | null {
  if (cachedUser === undefined) cachedUser = readStoredUser();
  return cachedUser;
}

export function setStoredUser(user: UserProfile): void {
  cachedUser = user;
  try {
    localStorage.setItem(USER_KEY, JSON.stringify(user));
  } catch {
    // in-memory cache still serves this page load
  }
  window.dispatchEvent(new Event(USER_EVENT));
}

export function clearStoredUser(): void {
  cachedUser = null;
  try {
    localStorage.removeItem(USER_KEY);
  } catch {
    // ignore
  }
  window.dispatchEvent(new Event(USER_EVENT));
}

/** Store a fresh {token, user} pair after login/register. */
export function setAuth(token: string, user: UserProfile): void {
  setToken(token);
  setStoredUser(user);
}

/** Wipe all auth state (logout / 401). */
export function clearAuth(): void {
  clearToken();
  clearStoredUser();
}

function subscribe(callback: () => void): () => void {
  const onStorage = (e: StorageEvent) => {
    if (e.key === USER_KEY || e.key === null) {
      cachedUser = undefined; // another tab changed it — re-read lazily
      callback();
    }
  };
  window.addEventListener(USER_EVENT, callback);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(USER_EVENT, callback);
    window.removeEventListener("storage", onStorage);
  };
}

/**
 * The signed-in user's profile (from localStorage, kept fresh by the app's
 * profile sync). Null while signed out or before the first login.
 */
export function useCurrentUser(): UserProfile | null {
  return useSyncExternalStore(subscribe, getStoredUser, () => null);
}

// ── 401 handling ────────────────────────────────────────────────────────────

/** Fired by the API client whenever the backend answers 401. */
export const UNAUTHORIZED_EVENT = "forge:unauthorized";

export function announceUnauthorized(): void {
  clearAuth();
  window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
}
