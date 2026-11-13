"""Request/response models for the tailoring pipeline and POST /api/tailor."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.jd.models import JDRequirements
from app.latex.validator import ValidationResult
from app.matching.models import RequirementMatch
from app.scoring.models import ATSScore

MAX_LATEX_CHARS = 200_000
MAX_JD_CHARS = 50_000


class TailorRequest(BaseModel):
    latex_source: str = Field(max_length=MAX_LATEX_CHARS)
    job_description: str = Field(max_length=MAX_JD_CHARS)

    @field_validator("latex_source", "job_description")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        # Validate without stripping: the preamble must be returned verbatim.
        if not value.strip():
            raise ValueError("must not be empty")
        return value


class LLMScore(BaseModel):
    score: int
    reasoning: str
    matched_keywords: list[str] = Field(default_factory=list)
    missing_keywords: list[str] = Field(default_factory=list)


class EntryBulletCount(BaseModel):
    entry_id: str
    label: str
    before: int
    after: int


class ContentCheck(BaseModel):
    """Outcome of the content-preservation checks (M11.1)."""

    entries: list[EntryBulletCount] = Field(default_factory=list)
    reworded: int = 0
    unchanged: int = 0
    reverted: int = Field(default=0, description="Rewrites rejected by the guards")
    restored: int = Field(default=0, description="Bullets Gemini omitted, put back")
    discarded: int = Field(default=0, description="Unknown or duplicate ids from Gemini")
    reordered_entries: int = 0
    skills_before: int = 0
    skills_after: int = 0
    verification: Literal["ran", "unavailable", "disabled", "not_needed"] = Field(
        default="not_needed",
        description="Layer B (semantic verifier) status: disabled when ENABLE_VERIFY_CALL is "
        "off, not_needed when nothing was reworded",
    )
    semantic_reverts: int = Field(default=0, description="Reverts decided by the verifier")


class BulletChange(BaseModel):
    """Per-bullet outcome, for the 'What changed' list."""

    id: str
    label: str = Field(description="Entry label, e.g. the company")
    status: Literal["reworded", "unchanged", "reverted", "restored"]
    reason: str = Field(
        default="",
        description="reworded/unchanged: the model's reason; reverted/restored: the backend's",
    )
    model_reason: str = ""
    before: str = Field(description="Original bullet (plain text)")
    after: str | None = Field(default=None, description="Final text if reworded")
    proposed: str | None = Field(default=None, description="The rejected rewrite, if reverted")


class Suggestion(BaseModel):
    requirement: str
    detail: str
    evidence: str | None = None


class SuggestionGroups(BaseModel):
    surface: list[Suggestion] = Field(
        default_factory=list,
        description="Supported by your experience, consider surfacing (partial matches)",
    )
    missing: list[Suggestion] = Field(
        default_factory=list,
        description="Missing from your resume; the app will never add these",
    )
    other: list[str] = Field(default_factory=list, description="Structure and formatting")


class TailorResult(BaseModel):
    latex: str = Field(description="Complete tailored LaTeX: original preamble + tailored body")
    validation: ValidationResult
    llm_score: LLMScore
    programmatic_score: ATSScore = Field(
        description="M4 score of the tailored resume: total, per-criterion breakdown, "
        "matched/missing keywords, structure issues, improvement summary"
    )
    original_programmatic_score: float = Field(
        description="M4 total of the resume before tailoring"
    )
    jd_analysis: JDRequirements
    changes_summary: list[str] = Field(
        description="What changed, computed by the backend from the per-bullet diff"
    )
    model_notes: list[str] = Field(
        default_factory=list,
        description="Gemini's own notes, minus any claims about preserving facts",
    )
    content_check: ContentCheck = Field(default_factory=ContentCheck)
    bullet_changes: list[BulletChange] = Field(default_factory=list)
    requirement_matches: list[RequirementMatch] = Field(
        default_factory=list,
        description="Each JD requirement vs the FINAL resume: matched/partially_matched/missing",
    )
    suggestions: SuggestionGroups = Field(default_factory=SuggestionGroups)
    provider: str = Field(default="Google Gemini", description="LLM provider used")
    model: str = Field(default="", description="Exact model identifier used (GEMINI_MODEL)")
    llm_requests: int | None = Field(
        default=None,
        description="HTTP requests sent to the LLM for this run, incl. retries and repairs",
    )
    improvement_summary: list[str] = Field(
        description="Remaining opportunities: LLM suggestions, then programmatic ones"
    )
    warnings: list[str] = Field(default_factory=list)
    prompt_version: str
    timings_ms: dict[str, float] = Field(
        default_factory=dict,
        description="Per-stage wall time of the run that produced this result "
        "(parse, analyze_jd, score_original, gemini, assemble_validate, score_tailored, total)",
    )
    cached: bool = False
