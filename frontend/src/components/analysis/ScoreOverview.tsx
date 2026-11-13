import { ScoreBar } from "./ScoreBar";
import { formatScore, scoreTone } from "./score";
import styles from "./analysis.module.css";

function ScoreDisplay({
  title,
  subtitle,
  score,
  children,
}: {
  title: string;
  subtitle: string;
  score: number;
  children?: React.ReactNode;
}) {
  return (
    <div className={styles.scoreCard}>
      <h3 className={styles.scoreTitle}>{title}</h3>
      <p className={styles.scoreSubtitle}>{subtitle}</p>
      <p className={styles.scoreValue}>
        <span className={styles[scoreTone(score)]}>{formatScore(score)}</span>
        <span className={styles.scoreMax}>/100</span>
      </p>
      <ScoreBar percent={score} label={`${title}: ${formatScore(score)} out of 100`} />
      {children}
    </div>
  );
}

export function ScoreOverview({
  llmScore,
  programmaticScore,
  originalProgrammaticScore,
  provider,
  model,
}: {
  provider: string;
  model: string;
  llmScore: number;
  programmaticScore: number;
  originalProgrammaticScore: number | null | undefined;
}) {
  const hasBefore = typeof originalProgrammaticScore === "number";
  const delta = hasBefore ? programmaticScore - originalProgrammaticScore : 0;
  return (
    <div className={styles.scoreGrid}>
      <ScoreDisplay title="LLM assessment" subtitle="Model-generated analysis" score={llmScore}>
        <p className={styles.modelInfo}>
          Provider: {provider || "unknown"} · Model: <code>{model || "unknown"}</code>
        </p>
      </ScoreDisplay>
      <ScoreDisplay
        title="Programmatic Score"
        subtitle="Deterministic keyword & structure scorer"
        score={programmaticScore}
      >
        {hasBefore && (
          <p className={styles.beforeAfter}>
            Before: {formatScore(originalProgrammaticScore)} → After: {formatScore(programmaticScore)}
            <span className={delta >= 0 ? styles.deltaUp : styles.deltaDown}>
              {" "}
              ({delta >= 0 ? "+" : ""}
              {delta.toFixed(1)})
            </span>
          </p>
        )}
      </ScoreDisplay>
    </div>
  );
}
