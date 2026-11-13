import type { ApiError } from "@/lib/api";
import styles from "./tailor.module.css";

// Titles for the backend's structured error codes (app/api/errors.py).
const TITLES: Record<string, string> = {
  gemini_timeout: "The model timed out",
  gemini_deadline: "Tailoring took too long",
  rate_limited: "Too many requests",
  unauthorized: "Signed out",
  gemini_rate_limited: "Model rate limit reached",
  gemini_bad_response: "The model returned an unusable response",
  gemini_not_configured: "LLM API key missing",
  invalid_input: "This LaTeX can't be tailored",
  invalid_request: "Check your input",
  internal_error: "Something went wrong on the server",
  network_error: "Backend unreachable",
  client_timeout: "Request took too long",
};

export function ErrorCard({ error, onRetry }: { error: ApiError; onRetry: () => void }) {
  const title = TITLES[error.code] ?? "Something went wrong";
  return (
    <div className={styles.errorCard} role="alert">
      <h2 className={styles.errorTitle}>
        <span aria-hidden="true">✕</span> {title}
      </h2>
      <p className={styles.errorMessage}>{error.message}</p>
      {error.retryAfterSeconds !== null && (
        <p className={styles.errorHint}>
          The provider suggests waiting about {Math.ceil(error.retryAfterSeconds)} seconds before retrying.
        </p>
      )}
      <div className={styles.errorFooter}>
        <button type="button" className="btn btn-primary btn-small" onClick={onRetry}>
          Retry
        </button>
        <span className={styles.errorCode}>
          {error.status !== null ? `HTTP ${error.status} · ` : ""}
          {error.code}
        </span>
      </div>
    </div>
  );
}
