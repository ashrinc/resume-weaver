import type { CriterionScore } from "@/lib/api";
import { ScoreBar } from "./ScoreBar";
import { formatScore } from "./score";
import styles from "./analysis.module.css";

export function CriteriaBreakdown({ criteria }: { criteria: CriterionScore[] }) {
  return (
    <ul className={styles.criteria}>
      {criteria.map((c) => (
        <li key={c.key} className={c.applicable ? styles.criterion : styles.criterionNa}>
          <div className={styles.criterionHeader}>
            <span className={styles.criterionName}>{c.label}</span>
            <span className={styles.criterionPoints}>
              {c.applicable ? (
                <>
                  {formatScore(c.points)}
                  <span className={styles.criterionMax}> / {c.max_points}</span>
                </>
              ) : (
                "n/a"
              )}
            </span>
          </div>
          {c.applicable && c.percent !== null ? (
            <ScoreBar size="mini" percent={c.percent * 100} label={`${c.label}: ${Math.round(c.percent * 100)}%`} />
          ) : (
            <div className={styles.barMiniEmpty} aria-hidden="true" />
          )}
          <p className={styles.criterionDetails}>{c.details}</p>
        </li>
      ))}
    </ul>
  );
}
