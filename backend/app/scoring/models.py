"""Output models for the programmatic ATS scorer (PRD FR-04)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.matching.models import RequirementMatch, SkillMatchType

CriterionKey = Literal[
    "mandatory_skills",
    "experience_alignment",
    "preferred_skills",
    "keyword_coverage",
    "structure",
]
MatchType = SkillMatchType
"""exact: the skill's canonical name appears; alias: a taxonomy alias or an
implied skill (k8s, Amazon S3 -> AWS); fuzzy: typo, version suffix or all
words of a multi-word skill in one line; related: a narrower skill supports it
through the taxonomy hierarchy. Fuzzy and related matches earn partial credit."""


class CriterionScore(BaseModel):
    key: CriterionKey
    label: str
    max_points: float = Field(description="PRD weight: 35, 25, 15, 15 or 10")
    points: float = Field(description="Points earned out of max_points")
    percent: float | None = Field(description="0-1, or None when not applicable")
    applicable: bool = Field(
        description="False when the JD gives nothing to measure (e.g. no preferred skills); "
        "the total is then rescaled over the applicable criteria"
    )
    details: str


class KeywordMatch(BaseModel):
    skill: str = Field(description="Canonical JD skill name")
    weight: Literal["high", "medium"]
    match_type: MatchType
    resume_term: str = Field(description="What the resume actually says")
    credit: float = Field(default=1.0, description="Score credit: 1, 0.8 fuzzy, 0.5 related")


class MissingKeyword(BaseModel):
    skill: str
    weight: Literal["high", "medium"]
    priority: Literal["high", "medium"] = Field(description="high for mandatory skills")


class ATSScore(BaseModel):
    total: float = Field(ge=0, le=100)
    criteria: list[CriterionScore]
    matched_mandatory: list[KeywordMatch] = Field(default_factory=list)
    matched_preferred: list[KeywordMatch] = Field(default_factory=list)
    missing_keywords: list[MissingKeyword] = Field(
        default_factory=list, description="Mandatory (high priority) first, then preferred"
    )
    structure_issues: list[str] = Field(default_factory=list)
    improvement_summary: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    requirement_matches: list[RequirementMatch] = Field(
        default_factory=list,
        description="Every mandatory/preferred skill and responsibility with its status",
    )

    def criterion(self, key: CriterionKey) -> CriterionScore:
        return next(c for c in self.criteria if c.key == key)
