// Score colour bands shared by the analysis panel.
export type ScoreTone = "good" | "ok" | "bad";

/** >= 90 good (green), 60-89 ok (yellow), < 60 bad (red). Takes 0-100. */
export function scoreTone(score: number): ScoreTone {
  if (score >= 90) return "good";
  if (score >= 60) return "ok";
  return "bad";
}

/** Whole numbers as-is, otherwise one decimal: 87, 92.4. */
export function formatScore(score: number): string {
  return Number.isInteger(score) ? String(score) : score.toFixed(1);
}

export function clampPercent(value: number): number {
  return Math.max(0, Math.min(100, value));
}
