import type { KeywordMatch, MissingKeyword } from "@/lib/api";
import styles from "./analysis.module.css";

type Variant = "matchedMandatory" | "matchedPreferred" | "missingHigh" | "missingPreferred";

interface Chip {
  label: string;
  title?: string;
  fuzzy?: boolean;
}

function matchTitle(match: KeywordMatch): string {
  if (match.match_type === "exact") return "Exact match";
  const how =
    match.match_type === "alias"
      ? "Matched via alias"
      : match.match_type === "related"
        ? "Partial: a more specific skill in your resume supports it"
        : "Approximate match";
  return `${how}: resume says “${match.resume_term}”`;
}

function fromMatches(matches: KeywordMatch[]): Chip[] {
  return matches.map((m) => ({ label: m.skill, title: matchTitle(m), fuzzy: m.match_type === "fuzzy" || m.match_type === "related" }));
}

function ChipGroup({
  title,
  count,
  chips,
  variant,
  empty,
}: {
  title: string;
  count: string;
  chips: Chip[];
  variant: Variant;
  empty: string;
}) {
  return (
    <div className={styles.chipGroup}>
      <h4 className={styles.chipGroupTitle}>
        {title} <span className={styles.chipCount}>({count})</span>
      </h4>
      {chips.length === 0 ? (
        <p className={styles.chipEmpty}>{empty}</p>
      ) : (
        <ul className={styles.chipList}>
          {chips.map((chip) => (
            <li key={chip.label} className={`chip ${styles[variant]}`} title={chip.title}>
              {chip.label}
              {chip.fuzzy && (
                <span className={styles.fuzzyMark} aria-label="approximate match">
                  ~
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function KeywordGroups({
  matchedMandatory,
  matchedPreferred,
  missing,
}: {
  matchedMandatory: KeywordMatch[];
  matchedPreferred: KeywordMatch[];
  missing: MissingKeyword[];
}) {
  const missingHigh = missing.filter((m) => m.priority === "high");
  const missingPreferred = missing.filter((m) => m.priority !== "high");
  const totalMandatory = matchedMandatory.length + missingHigh.length;
  const totalPreferred = matchedPreferred.length + missingPreferred.length;

  return (
    <div className={styles.chipGroups}>
      <ChipGroup
        title="Matched Mandatory"
        count={`${matchedMandatory.length}/${totalMandatory}`}
        chips={fromMatches(matchedMandatory)}
        variant="matchedMandatory"
        empty={totalMandatory === 0 ? "The JD lists no mandatory skills." : "None matched yet."}
      />
      <ChipGroup
        title="Matched Preferred"
        count={`${matchedPreferred.length}/${totalPreferred}`}
        chips={fromMatches(matchedPreferred)}
        variant="matchedPreferred"
        empty={totalPreferred === 0 ? "The JD lists no preferred skills." : "None matched yet."}
      />
      <ChipGroup
        title="Missing (High Priority)"
        count={String(missingHigh.length)}
        chips={missingHigh.map((m) => ({ label: m.skill, title: "Mandatory in the JD" }))}
        variant="missingHigh"
        empty="None: every mandatory skill is covered."
      />
      <ChipGroup
        title="Missing (Preferred)"
        count={String(missingPreferred.length)}
        chips={missingPreferred.map((m) => ({ label: m.skill, title: "Preferred in the JD" }))}
        variant="missingPreferred"
        empty="None: every preferred skill is covered."
      />
    </div>
  );
}
