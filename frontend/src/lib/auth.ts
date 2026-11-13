// Single-user sign-in (backend app/api/auth.py). When the backend has no
// ACCESS_PASSWORD (local development) /api/auth/session says auth is not
// required and the app opens directly.

import { readStored, writeStored } from "./useLocalStorage";

export const TOKEN_KEY = "resume-tailr.auth-token";

/** Authorization header for API calls, if signed in. */
export function authHeaders(): Record<string, string> {
  const token = typeof window === "undefined" ? null : readStored(TOKEN_KEY);
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export function setToken(token: string | null): void {
  writeStored(TOKEN_KEY, token);
}

export type SessionState = "open" | "login";

/** Whether to show the app or the sign-in screen. Network errors open the app,
 * so the screens can show their own "backend unreachable" messages. */
export async function checkSession(apiBase: string, token: string): Promise<SessionState> {
  try {
    const response = await fetch(`${apiBase}/api/auth/session`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!response.ok) return "open";
    const body = (await response.json()) as { auth_required: boolean; authenticated: boolean };
    return body.auth_required && !body.authenticated ? "login" : "open";
  } catch {
    return "open";
  }
}

/** Returns an error message, or null after storing the new token. */
export async function signIn(apiBase: string, password: string): Promise<string | null> {
  let response: Response;
  try {
    response = await fetch(`${apiBase}/api/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
  } catch {
    return `Could not reach the backend at ${apiBase}.`;
  }
  if (response.ok) {
    const body = (await response.json()) as { token: string };
    setToken(body.token);
    return null;
  }
  try {
    const body = (await response.json()) as { error?: { message?: string } };
    return body.error?.message ?? `Sign-in failed (HTTP ${response.status}).`;
  } catch {
    return `Sign-in failed (HTTP ${response.status}).`;
  }
}
