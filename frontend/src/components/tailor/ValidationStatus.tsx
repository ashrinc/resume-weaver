import type { ValidationIssue, ValidationResult } from "@/lib/api";
import styles from "./tailor.module.css";

function IssueList({ title, issues }: { title: string; issues: ValidationIssue[] }) {
  if (issues.length === 0) return null;
  return (
    <>
      <h3 className={styles.issueHeading}>{title}</h3>
      <ul className={styles.issueList}>
        {issues.map((issue, i) => (
          <li key={i}>
            {issue.line !== null && <span className={styles.issueLine}>line {issue.line}</span>}
            {issue.message}
          </li>
        ))}
      </ul>
    </>
  );
}

/** LaTeX validation (M1). Pipeline warnings are shown in the analysis panel. */
export function ValidationStatus({ validation }: { validation: ValidationResult }) {
  const issueCount = validation.errors.length + validation.warnings.length;
  const passed = issueCount === 0;
  return (
    <div className={passed ? styles.validationPass : styles.validationWarn} role="status">
      <p className={styles.validationSummary}>
        <span aria-hidden="true">{passed ? "✓" : "⚠"}</span>
        {passed
          ? "LaTeX validation passed: braces, environments and required commands look good."
          : `LaTeX validation found ${issueCount} issue${issueCount === 1 ? "" : "s"}. The output is shown anyway; review before compiling.`}
      </p>
      <IssueList title="Errors" issues={validation.errors} />
      <IssueList title="Warnings" issues={validation.warnings} />
    </div>
  );
}
