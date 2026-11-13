"use client";

import { useState } from "react";
import { LatexView } from "./LatexCode";
import styles from "./tailor.module.css";

const DOWNLOAD_NAME = "tailored_resume.tex";

type CopyState = "idle" | "copied" | "failed";

export function LatexOutput({ latex, cached }: { latex: string; cached: boolean }) {
  const [copyState, setCopyState] = useState<CopyState>("idle");

  async function copy() {
    try {
      await navigator.clipboard.writeText(latex);
      setCopyState("copied");
    } catch {
      setCopyState("failed");
    }
    setTimeout(() => setCopyState("idle"), 2000);
  }

  function download() {
    const blob = new Blob([latex], { type: "application/x-tex;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = DOWNLOAD_NAME;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  return (
    <div className={styles.output}>
      <div className={styles.outputHeader}>
        <h2 className={styles.panelTitle}>
          Tailored LaTeX
          {cached && (
            <span className={styles.badge} title="Same inputs as an earlier run: served from cache">
              cached
            </span>
          )}
        </h2>
        <div className={styles.outputActions}>
          <button type="button" className="btn btn-secondary btn-small" onClick={copy}>
            {copyState === "copied" ? "✓ Copied" : copyState === "failed" ? "Copy failed" : "Copy"}
          </button>
          <button type="button" className="btn btn-secondary btn-small" onClick={download}>
            Download .tex
          </button>
        </div>
      </div>
      <span className="sr-only" aria-live="polite">
        {copyState === "copied" ? "LaTeX copied to clipboard" : copyState === "failed" ? "Copy failed" : ""}
      </span>
      <LatexView source={latex} label="Tailored LaTeX source" />
    </div>
  );
}
