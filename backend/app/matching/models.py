"""Requirement-matching output (M11.2): how each JD requirement is supported
by the resume. Feeds the tailoring prompt (which bullets to strengthen), the
programmatic score and the analysis panel."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

RequirementKind = Literal["mandatory_skill", "preferred_skill", "responsibility"]
MatchStatus = Literal["matched", "partially_matched", "missing"]
SkillMatchType = Literal["exact", "alias", "fuzzy", "related"]
"""exact: canonical name; alias: taxonomy alias or implied skill; fuzzy: typo,
version suffix or all words of a multi-word skill in one line; related: a
narrower skill in the resume supports it through the taxonomy hierarchy
(PostgreSQL -> Relational Databases)."""


class RequirementMatch(BaseModel):
    requirement: str = Field(description="The JD requirement: a skill name or a responsibility")
    kind: RequirementKind
    status: MatchStatus
    evidence: str | None = Field(
        default=None, description="Resume text that supports it (quoted from the resume)"
    )
    reason: str = Field(description="Why it got this status")
    credit: float = Field(ge=0, le=1, description="Score credit, 0-1")
    bullet_ids: list[str] = Field(
        default_factory=list, description="Bullets that support it best (ids as in the prompt)"
    )
    match_type: SkillMatchType | None = Field(default=None, description="For skill requirements")
    resume_term: str | None = Field(default=None, description="For skills: what the resume says")
