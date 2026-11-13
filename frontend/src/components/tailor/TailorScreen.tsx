"use client";

import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import {
  ApiError,
  MAX_JD_CHARS,
  MAX_LATEX_CHARS,
  tailorResume,
  type TailorResult,
} from "@/lib/api";
import { useLocalStorage } from "@/lib/useLocalStorage";
import { AnalysisPanel } from "@/components/analysis/AnalysisPanel";
import { ErrorCard } from "./ErrorCard";
import { LatexEditor } from "./LatexCode";
import { LatexOutput } from "./LatexOutput";
import { ValidationStatus } from "./ValidationStatus";
import styles from "./tailor.module.css";

// Inputs are persisted so they survive errors, reloads and navigation.
const LATEX_KEY = "resume-tailr.latex-source";
const JD_KEY = "resume-tailr.job-description";

interface Inputs {
  latex: string;
  jd: string;
}

type Status =
  | { kind: "idle" }
  | { kind: "loading"; startedAt: number }
  | { kind: "success"; result: TailorResult; inputs: Inputs; runId: number }
  | { kind: "error"; error: ApiError };

function loadingHint(seconds: number): string {
  if (seconds < 4) return "Parsing your LaTeX and analyzing the job description…";
  if (seconds < 60) return "Waiting for the model to rewrite and verify your bullets…";
  return "Still working: if the model is rate-limited, the backend retries with backoff.";
}

function count(value: string, max: number): string {
  return `${value.length.toLocaleString("en-US")} / ${max.toLocaleString("en-US")}`;
}

export function TailorScreen() {
  const [latex, setLatex] = useLocalStorage(LATEX_KEY, "");
  const [jd, setJd] = useLocalStorage(JD_KEY, "");
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const [now, setNow] = useState(0);
  const abortRef = useRef<AbortController | null>(null);

  const loading = status.kind === "loading";
  const tooLong = latex.length > MAX_LATEX_CHARS || jd.length > MAX_JD_CHARS;
  const canGenerate = latex.trim() !== "" && jd.trim() !== "" && !tooLong && !loading;

  // Tick once a second while a request is in flight (for the elapsed timer).
  useEffect(() => {
    if (status.kind !== "loading") return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [status.kind]);

  // Abort an in-flight request if the user navigates away.
  useEffect(() => () => abortRef.current?.abort(), []);

  async function generate() {
    if (!canGenerate) return;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const inputs = { latex, jd };
    const startedAt = Date.now();
    setNow(startedAt);
    setStatus({ kind: "loading", startedAt });
    try {
      const result = await tailorResume(inputs.latex, inputs.jd, controller.signal);
      setStatus({ kind: "success", result, inputs, runId: startedAt });
    } catch (e) {
      if (controller.signal.aborted && abortRef.current !== controller) return; // superseded
      const error =
        e instanceof ApiError
          ? e
          : new ApiError("An unexpected error occurred in the browser.", "client_error", true);
      setStatus(error.code === "cancelled" ? { kind: "idle" } : { kind: "error", error });
    }
  }

  function cancel() {
    abortRef.current?.abort();
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      void generate();
    }
  }

  const elapsed = status.kind === "loading" ? Math.max(0, Math.round((now - status.startedAt) / 1000)) : 0;
  const stale =
    status.kind === "success" && (status.inputs.latex !== latex || status.inputs.jd !== jd);

  return (
    <div className={styles.screen}>
      {/* ---------------- Left: inputs ---------------- */}
      <section className={`card ${styles.inputPanel}`} aria-labelledby="inputs-title">
        <h1 id="inputs-title" className={styles.panelTitle}>
          Inputs
        </h1>

        <div className={styles.field}>
          <div className={styles.fieldHeader}>
            <label htmlFor="latex-source">LaTeX resume</label>
            <span
              id="latex-count"
              className={latex.length > MAX_LATEX_CHARS ? styles.countOver : styles.count}
            >
              {count(latex, MAX_LATEX_CHARS)}
            </span>
          </div>
          <LatexEditor
            id="latex-source"
            value={latex}
            onChange={setLatex}
            onKeyDown={onKeyDown}
            describedBy="latex-count"
            placeholder={"Paste your complete .tex file here, from \\documentclass to \\end{document}"}
          />
        </div>

        <div className={styles.field}>
          <div className={styles.fieldHeader}>
            <label htmlFor="job-description">Job description</label>
            <span id="jd-count" className={jd.length > MAX_JD_CHARS ? styles.countOver : styles.count}>
              {count(jd, MAX_JD_CHARS)}
            </span>
          </div>
          <textarea
            id="job-description"
            className={`input ${styles.jdInput}`}
            value={jd}
            onChange={(event) => setJd(event.target.value)}
            onKeyDown={onKeyDown}
            aria-describedby="jd-count"
            placeholder="Paste the full job description, including requirements and nice-to-haves"
          />
        </div>

        <div className={styles.actions}>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => void generate()}
            disabled={!canGenerate}
          >
            {loading ? "Generating…" : "Generate"}
          </button>
          <span className={styles.shortcut}>
            {tooLong ? "Input is over the size limit." : "Ctrl/⌘ + Enter"}
          </span>
        </div>
      </section>

      {/* ---------------- Right: output ---------------- */}
      <div className={styles.rightColumn}>
        <section
          className={`card ${styles.outputPanel} ${loading ? styles.outputLoading : ""}`}
          aria-labelledby="output-title"
          aria-busy={loading}
        >
          <h2 id="output-title" className="sr-only">
            Output
          </h2>

          {status.kind === "idle" && (
            <div className={styles.placeholder}>
              <p className={styles.placeholderTitle}>Your tailored LaTeX will appear here.</p>
              <p className="muted">
                Paste your resume and a job description, then press Generate. The preamble is kept
                exactly as you wrote it; only the body sections are rewritten.
              </p>
            </div>
          )}

          {status.kind === "loading" && (
            <div className={styles.loading} role="status" aria-live="polite">
              <div className={styles.loadingOrb} aria-hidden="true" />
              <p className={styles.loadingTitle}>Tailoring your resume...</p>
              <p className="muted">{loadingHint(elapsed)}</p>
              <p className={styles.loadingTimer}>{elapsed}s</p>
              <button type="button" className="btn btn-secondary btn-small" onClick={cancel}>
                Cancel
              </button>
            </div>
          )}

          {status.kind === "error" && <ErrorCard error={status.error} onRetry={() => void generate()} />}

          {status.kind === "success" && (
            <>
              {stale && (
                <p className={styles.staleNote}>
                  Inputs changed since this result was generated. Press Generate to refresh it.
                </p>
              )}
              <LatexOutput latex={status.result.latex} cached={status.result.cached} />
              <ValidationStatus validation={status.result.validation} />
            </>
          )}
        </section>

        {/* Keyed per run so the enter animation replays for every new result. */}
        {status.kind === "success" && <AnalysisPanel key={status.runId} result={status.result} />}
      </div>
    </div>
  );
}
