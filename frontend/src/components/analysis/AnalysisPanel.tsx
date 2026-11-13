import type { BulletChange, Suggestion, TailorResult } from "@/lib/api";
import { CriteriaBreakdown } from "./CriteriaBreakdown";
import { KeywordGroups } from "./KeywordGroups";
import { RequirementMatches } from "./RequirementMatches";
import { ScoreOverview } from "./ScoreOverview";
import styles from "./analysis.module.css";

function unique(items: string[]): string[] {
  const seen = new Set<string>();
  return items.filter((item) => {
    const key = item.trim().toLowerCase();
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function SuggestionList({ items }: { items: Suggestion[] }) {
  return (
    <ul className={styles.suggestions}>
      {items.map((s) => (
        <li key={s.requirement}>
          <span className={styles.suggestionIcon} aria-hidden="true">
            →
          </span>
          <span>
            <strong>{s.requirement}</strong>: {s.detail}
          </span>
        </li>
      ))}
    </ul>
  );
}

const CHANGE_LABEL: Record<BulletChange["status"], string> = {
  reworded: "Reworded",
  unchanged: "Unchanged",
  reverted: "Reverted",
  restored: "Restored",
};

/** Rewritten bullets with the model's reason; reverts and restores with the backend's. */
function BulletChanges({ changes }: { changes: BulletChange[] }) {
  const shown = changes.filter((c) => c.status !== "unchanged");
  if (shown.length === 0) return <p className="muted">No bullet was rewritten.</p>;
  return (
    <ul className={styles.bulletChanges}>
      {shown.map((c) => (
        <li key={c.id} className={styles[`change_${c.status}`]}>
          <p className={styles.changeHead}>
            <span className={styles.changeStatus}>{CHANGE_LABEL[c.status]}</span> {c.id} ({c.label})
          </p>
          {c.reason && <p className={styles.changeReason}>{c.reason}</p>}
          {c.status === "reworded" && c.after && (
            <p className={styles.changeDiff}>
              <del>{c.before}</del>
              <ins>{c.after}</ins>
            </p>
          )}
          {c.status === "reverted" && c.proposed && (
            <p className={styles.changeDiff}>
              <span className="muted">Rejected rewrite: </span>
              <del>{c.proposed}</del>
            </p>
          )}
        </li>
      ))}
    </ul>
  );
}

/** Renders only for a successful result (the caller decides when to mount it). */
export function AnalysisPanel({ result }: { result: TailorResult }) {
  const score = result.programmatic_score;
  const warnings = unique([...result.warnings, ...score.warnings]);
  const changes = unique(result.changes_summary);
  const notes = unique(result.model_notes ?? []);
  const check = result.content_check;
  const groups = result.suggestions ?? { surface: [], missing: [], other: [] };
  const other = unique(groups.other);

  return (
    <section className={`card ${styles.panel}`} aria-labelledby="analysis-title">
      <h2 id="analysis-title" className={styles.panelTitle}>
        ATS analysis
      </h2>

      {/* 1. Scores */}
      <ScoreOverview
        llmScore={result.llm_score.score}
        programmaticScore={score.total}
        originalProgrammaticScore={result.original_programmatic_score}
        provider={result.provider}
        model={result.model}
      />

      {/* 2. The model's reasoning, labelled as model-generated */}
      {result.llm_score.reasoning && (
        <details className={styles.reasoning}>
          <summary>LLM assessment: reasoning (model-generated analysis)</summary>
          <p>{result.llm_score.reasoning}</p>
        </details>
      )}

      {/* 3. Requirement matches (final resume) */}
      <div className={styles.block}>
        <h3 className={styles.blockTitle}>Job requirements</h3>
        <RequirementMatches matches={result.requirement_matches ?? []} />
      </div>

      {/* 4. Keywords */}
      <div className={styles.block}>
        <h3 className={styles.blockTitle}>Keywords</h3>
        <KeywordGroups
          matchedMandatory={score.matched_mandatory}
          matchedPreferred={score.matched_preferred}
          missing={score.missing_keywords}
        />
      </div>

      {/* 5. Breakdown */}
      <div className={styles.block}>
        <h3 className={styles.blockTitle}>Programmatic score breakdown</h3>
        <CriteriaBreakdown criteria={score.criteria} />
      </div>

      {/* 6. Suggestions, in two groups. Nothing here asks you to invent experience. */}
      <div className={styles.block}>
        <h3 className={styles.blockTitle}>Supported by your experience, consider surfacing</h3>
        {groups.surface.length === 0 ? (
          <p className="muted">Nothing partially supported is left to surface.</p>
        ) : (
          <SuggestionList items={groups.surface} />
        )}
      </div>
      <div className={styles.block}>
        <h3 className={styles.blockTitle}>Missing from your resume</h3>
        <p className="muted">
          The app never adds these. Add one yourself only if you genuinely have the experience.
        </p>
        {groups.missing.length === 0 ? (
          <p className="muted">No requirements are missing.</p>
        ) : (
          <SuggestionList items={groups.missing} />
        )}
        {other.length > 0 && (
          <ul className={styles.suggestions}>
            {other.map((s) => (
              <li key={s}>
                <span className={styles.suggestionIcon} aria-hidden="true">
                  →
                </span>
                <span>{s}</span>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* 7. What changed: computed by the backend from the per-bullet diff */}
      {changes.length > 0 && (
        <details className={styles.reasoning}>
          <summary>What changed ({changes.length})</summary>
          {check && (
            <p className="muted">
              {check.reworded} reworded, {check.unchanged} unchanged
              {check.reverted > 0 && `, ${check.reverted} reverted to the original`}
              {check.restored > 0 && `, ${check.restored} restored after the model dropped them`}
              . No bullets were removed.
              {check.verification === "ran" && " Rewrites were fact-checked against your resume."}
              {check.verification === "unavailable" &&
                " Semantic fact-checking was unavailable; deterministic checks only."}
            </p>
          )}
          <BulletChanges changes={result.bullet_changes ?? []} />
          <ul className={styles.changes}>
            {changes
              .filter((c) => !/^(Reworded|Reverted|Restored) /.test(c))
              .map((c) => (
                <li key={c}>{c}</li>
              ))}
          </ul>
        </details>
      )}

      {/* 7b. The model's own notes, clearly labelled as such */}
      {notes.length > 0 && (
        <details className={styles.reasoning}>
          <summary>Model&apos;s notes ({notes.length})</summary>
          <ul className={styles.changes}>
            {notes.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        </details>
      )}

      {/* 8. Warnings */}
      {warnings.length > 0 && (
        <div className={styles.warnings} role="note" aria-label="Warnings">
          <h3 className={styles.warningsTitle}>
            <span aria-hidden="true">⚠</span> Warnings ({warnings.length})
          </h3>
          <ul>
            {warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
