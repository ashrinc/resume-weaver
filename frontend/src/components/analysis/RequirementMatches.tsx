import type { MatchStatus, RequirementMatch } from "@/lib/api";
import styles from "./analysis.module.css";

const STATUS_LABEL: Record<MatchStatus, string> = {
  matched: "matched",
  partially_matched: "partial",
  missing: "missing",
};

const STATUS_CLASS: Record<MatchStatus, string> = {
  matched: styles.statusMatched,
  partially_matched: styles.statusPartial,
  missing: styles.statusMissing,
};

const KIND_LABEL: Record<RequirementMatch["kind"], string> = {
  mandatory_skill: "Required skill",
  preferred_skill: "Preferred skill",
  responsibility: "Responsibility",
};

/** Every JD requirement vs the final resume; evidence and reason on expand. */
export function RequirementMatches({ matches }: { matches: RequirementMatch[] }) {
  if (matches.length === 0) {
    return <p className="muted">No requirements could be extracted from the job description.</p>;
  }
  const counts = {
    matched: matches.filter((m) => m.status === "matched").length,
    partially_matched: matches.filter((m) => m.status === "partially_matched").length,
    missing: matches.filter((m) => m.status === "missing").length,
  };
  return (
    <>
      <p className="muted">
        {counts.matched} matched · {counts.partially_matched} partial · {counts.missing} missing
      </p>
      <ul className={styles.requirementList}>
        {matches.map((m) => (
          <li key={`${m.kind}:${m.requirement}`}>
            <details className={styles.requirement}>
              <summary>
                <span className={`${styles.statusChip} ${STATUS_CLASS[m.status]}`}>
                  {STATUS_LABEL[m.status]}
                </span>
                <span className={styles.requirementKind}>{KIND_LABEL[m.kind]}</span>
                <span className={styles.requirementText}>{m.requirement}</span>
              </summary>
              <div className={styles.requirementBody}>
                <p>{m.reason}</p>
                {m.evidence && (
                  <blockquote className={styles.evidence}>
                    {m.evidence}
                    {m.bullet_ids.length > 0 && (
                      <span className={styles.bulletIds}> ({m.bullet_ids.join(", ")})</span>
                    )}
                  </blockquote>
                )}
              </div>
            </details>
          </li>
        ))}
      </ul>
    </>
  );
}
