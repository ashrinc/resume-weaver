"use client";

import { useEffect, useState } from "react";
import { API_BASE_URL } from "@/lib/api";
import { TOKEN_KEY, checkSession, signIn, type SessionState } from "@/lib/auth";
import { useLocalStorage } from "@/lib/useLocalStorage";
import styles from "./AuthGate.module.css";

/** Shows the sign-in screen when the backend requires it (ACCESS_PASSWORD set),
 * otherwise the page. Re-checks whenever the stored token changes, so a 401
 * anywhere (which clears the token) brings the sign-in screen back. */
export function AuthGate({ children }: { children: React.ReactNode }) {
  // "\u0000" on the server: never equal to a real token, so it reads as "checking".
  const [token] = useLocalStorage(TOKEN_KEY, "", "\u0000");
  const [checked, setChecked] = useState<{ token: string; state: SessionState } | null>(null);

  useEffect(() => {
    if (token === "\u0000") return;
    let alive = true;
    void checkSession(API_BASE_URL, token).then((state) => {
      if (alive) setChecked({ token, state });
    });
    return () => {
      alive = false;
    };
  }, [token]);

  if (checked === null || checked.token !== token) {
    return (
      <p className={`muted ${styles.checking}`} aria-live="polite">
        Connecting…
      </p>
    );
  }
  if (checked.state === "login") return <SignIn />;
  return <>{children}</>;
}

function SignIn() {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!password || busy) return;
    setBusy(true);
    const problem = await signIn(API_BASE_URL, password);
    setBusy(false);
    if (problem) setError(problem);
    // On success the stored token changes and AuthGate re-checks the session.
  }

  return (
    <section className={`card ${styles.panel}`} aria-labelledby="signin-title">
      <h1 id="signin-title" className={styles.title}>
        Sign in
      </h1>
      <p className="muted">This deployment is private. Enter the access password.</p>
      <form onSubmit={submit} className={styles.form}>
        <label htmlFor="access-password" className="sr-only">
          Access password
        </label>
        <input
          id="access-password"
          type="password"
          className="input"
          autoComplete="current-password"
          placeholder="Access password"
          value={password}
          onChange={(e) => {
            setPassword(e.target.value);
            setError(null);
          }}
          autoFocus
        />
        <button type="submit" className="btn btn-primary" disabled={!password || busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
