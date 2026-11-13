"use client";

import { useLocalStorage } from "@/lib/useLocalStorage";

const STORAGE_KEY = "resume-tailr.gemini-disclosure-dismissed";

// One-time notice (PRD security requirement). Rendered as "dismissed" on the
// server so returning visitors never see it flash before hydration.
export function DisclosureBanner() {
  const [dismissed, setDismissed] = useLocalStorage(STORAGE_KEY, "", "1");
  if (dismissed) return null;
  return (
    <div className="disclosure" role="region" aria-label="Data disclosure">
      <p>
        <strong>Heads up:</strong> Your resume content is sent to the Google Gemini API for
        processing, together with the job description you paste.
      </p>
      <button type="button" className="btn btn-secondary btn-small" onClick={() => setDismissed("1")}>
        Got it
      </button>
    </div>
  );
}
