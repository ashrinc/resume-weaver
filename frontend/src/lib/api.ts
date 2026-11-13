// Client for the FastAPI backend. All Gemini calls happen server-side; the
// browser only ever talks to our own backend.

import { authHeaders, setToken } from "./auth";

// Inlined at build time by Next.js (NEXT_PUBLIC_ prefix): rebuild after changing it.
export const API_BASE_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(
  /\/+$/,
  "",
);

// Mirrors backend MAX_LATEX_CHARS / MAX_JD_CHARS (app/tailor/models.py).
export const MAX_LATEX_CHARS = 200_000;
export const MAX_JD_CHARS = 50_000;

// The backend stops a run after TAILOR_DEADLINE_SECONDS (240 s) and answers
// with a structured gemini_deadline error. The client waits slightly longer so
// that error normally arrives; this only fires if the backend hangs.
const REQUEST_TIMEOUT_MS = 250 * 1000;

// ---- Response types (mirror backend pydantic models) -----------------------

export interface ValidationIssue {
  code: string;
  message: string;
  line: number | null;
}

export interface ValidationResult {
  errors: ValidationIssue[];
  warnings: ValidationIssue[];
}

export interface LLMScore {
  score: number;
  reasoning: string;
  matched_keywords: string[];
  missing_keywords: string[];
}

export interface CriterionScore {
  key:
    | "mandatory_skills"
    | "experience_alignment"
    | "preferred_skills"
    | "keyword_coverage"
    | "structure";
  label: string;
  max_points: number;
  points: number;
  percent: number | null;
  applicable: boolean;
  details: string;
}

export interface KeywordMatch {
  skill: string;
  weight: "high" | "medium";
  match_type: "exact" | "alias" | "fuzzy" | "related";
  /** Score credit: 1, 0.8 fuzzy, 0.5 related (via the taxonomy hierarchy). */
  credit?: number;
  resume_term: string;
}

export interface MissingKeyword {
  skill: string;
  weight: "high" | "medium";
  priority: "high" | "medium";
}

export interface ATSScore {
  total: number;
  criteria: CriterionScore[];
  matched_mandatory: KeywordMatch[];
  matched_preferred: KeywordMatch[];
  missing_keywords: MissingKeyword[];
  structure_issues: string[];
  improvement_summary: string[];
  warnings: string[];
}

export interface SkillRequirement {
  name: string;
  category: string;
  type: "tech" | "soft";
  weight: "high" | "medium" | "low";
  matched_terms: string[];
  occurrences: number;
}

export interface JDRequirements {
  title: string | null;
  mandatory_skills: SkillRequirement[];
  preferred_skills: SkillRequirement[];
  soft_skills: SkillRequirement[];
  responsibilities: string[];
  experience: { min_years: number | null; max_years: number | null };
  seniority: string | null;
  certifications: { name: string; weight: "high" | "medium" }[];
  warnings: string[];
}

/** Outcome of the backend's content-preservation checks (M11.1). */
export interface ContentCheck {
  entries: { entry_id: string; label: string; before: number; after: number }[];
  reworded: number;
  unchanged: number;
  reverted: number;
  restored: number;
  discarded: number;
  reordered_entries: number;
  skills_before: number;
  skills_after: number;
  /** Semantic verifier (second LLM call): not_needed when nothing was reworded. */
  verification: "ran" | "unavailable" | "not_needed";
  semantic_reverts: number;
}

export type MatchStatus = "matched" | "partially_matched" | "missing";

/** One JD requirement vs the FINAL resume. */
export interface RequirementMatch {
  requirement: string;
  kind: "mandatory_skill" | "preferred_skill" | "responsibility";
  status: MatchStatus;
  evidence: string | null;
  reason: string;
  credit: number;
  bullet_ids: string[];
  match_type: "exact" | "alias" | "fuzzy" | "related" | null;
  resume_term: string | null;
}

export interface BulletChange {
  id: string;
  label: string;
  status: "reworded" | "unchanged" | "reverted" | "restored";
  /** reworded/unchanged: the model's reason; reverted/restored: the backend's. */
  reason: string;
  model_reason: string;
  before: string;
  after: string | null;
  proposed: string | null;
}

export interface Suggestion {
  requirement: string;
  detail: string;
  evidence: string | null;
}

export interface SuggestionGroups {
  /** Supported by your experience: consider surfacing (partial matches). */
  surface: Suggestion[];
  /** Missing from the resume: the app never adds these. */
  missing: Suggestion[];
  other: string[];
}

export interface TailorResult {
  latex: string;
  validation: ValidationResult;
  llm_score: LLMScore;
  programmatic_score: ATSScore;
  original_programmatic_score: number;
  jd_analysis: JDRequirements;
  /** Computed by the backend from the per-bullet diff, not by the model. */
  changes_summary: string[];
  /** The model's own notes, minus any claims about preserving facts. */
  model_notes: string[];
  content_check: ContentCheck;
  bullet_changes: BulletChange[];
  requirement_matches: RequirementMatch[];
  suggestions: SuggestionGroups;
  /** LLM provider and the exact model identifier the backend used. */
  provider: string;
  model: string;
  llm_requests: number | null;
  improvement_summary: string[];
  warnings: string[];
  prompt_version: string;
  cached: boolean;
}

// ---- Errors ------------------------------------------------------------------

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly code: string,
    public readonly retryable: boolean,
    public readonly status: number | null = null,
    public readonly retryAfterSeconds: number | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

interface ErrorBody {
  error?: {
    code?: string;
    message?: string;
    retryable?: boolean;
    retry_after_seconds?: number | null;
  };
}

export async function toApiError(response: Response): Promise<ApiError> {
  // Expired or invalid sign-in: drop the token so the sign-in screen appears.
  if (response.status === 401) setToken(null);
  let body: ErrorBody = {};
  try {
    body = (await response.json()) as ErrorBody;
  } catch {
    // Non-JSON error (e.g. a proxy page): fall through to a generic message.
  }
  const err = body.error;
  return new ApiError(
    err?.message ?? `The backend returned HTTP ${response.status}.`,
    err?.code ?? "http_error",
    err?.retryable ?? response.status >= 500,
    response.status,
    err?.retry_after_seconds ?? null,
  );
}

// ---- Calls -------------------------------------------------------------------

export async function tailorResume(
  latexSource: string,
  jobDescription: string,
  signal?: AbortSignal,
): Promise<TailorResult> {
  const timeout = AbortSignal.timeout(REQUEST_TIMEOUT_MS);
  const combined = signal ? AbortSignal.any([signal, timeout]) : timeout;

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/api/tailor`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ latex_source: latexSource, job_description: jobDescription }),
      signal: combined,
    });
  } catch {
    if (signal?.aborted) {
      throw new ApiError("Request cancelled.", "cancelled", true);
    }
    if (timeout.aborted) {
      throw new ApiError(
        `The request took longer than ${REQUEST_TIMEOUT_MS / 1000} seconds and was stopped. Try again.`,
        "client_timeout",
        true,
      );
    }
    throw new ApiError(
      `Could not reach the backend at ${API_BASE_URL}. Is it running? ` +
        "(cd backend && uv run uvicorn app.main:app --reload --port 8000)",
      "network_error",
      true,
    );
  }

  if (!response.ok) {
    throw await toApiError(response);
  }
  return (await response.json()) as TailorResult;
}
