import { clampPercent, scoreTone } from "./score";
import styles from "./analysis.module.css";

/** Thin bar filled to `percent` (0-100), coloured by score band. */
export function ScoreBar({
  percent,
  label,
  size = "normal",
}: {
  percent: number;
  label: string;
  size?: "normal" | "mini";
}) {
  const value = clampPercent(percent);
  return (
    <div
      className={size === "mini" ? styles.barMini : styles.bar}
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(value)}
    >
      <div className={`${styles.barFill} ${styles[scoreTone(value)]}`} style={{ width: `${value}%` }} />
    </div>
  );
}
