"""Data models produced by the rule-based JD analyzer (PRD FR-03)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Weight = Literal["high", "medium", "low"]
"""high = mandatory tech skill, medium = preferred tech skill, low = soft skill."""

Seniority = Literal[
    "intern", "junior", "mid", "senior", "lead", "staff", "principal", "manager", "director"
]
JDSection = Literal["intro", "responsibilities", "requirements", "preferred", "other"]


class SkillRequirement(BaseModel):
    name: str = Field(description="Canonical taxonomy name")
    category: str
    type: Literal["tech", "soft"]
    weight: Weight
    matched_terms: list[str] = Field(description="Surface forms found in the JD, e.g. 'k8s'")
    occurrences: int


class CertificationRequirement(BaseModel):
    name: str
    weight: Literal["high", "medium"]
    matched_text: str


class YearsMention(BaseModel):
    min_years: int
    max_years: int | None = None
    weight: Literal["high", "medium"]
    text: str


class ExperienceRequirement(BaseModel):
    min_years: int | None = None
    max_years: int | None = None
    mentions: list[YearsMention] = Field(default_factory=list)


class JDRequirements(BaseModel):
    title: str | None = None
    mandatory_skills: list[SkillRequirement] = Field(default_factory=list)
    preferred_skills: list[SkillRequirement] = Field(default_factory=list)
    soft_skills: list[SkillRequirement] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    experience: ExperienceRequirement = Field(default_factory=ExperienceRequirement)
    seniority: Seniority | None = None
    seniority_source: Literal["title", "text", "years"] | None = None
    certifications: list[CertificationRequirement] = Field(default_factory=list)
    sections_found: list[JDSection] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def all_skills(self) -> list[SkillRequirement]:
        return self.mandatory_skills + self.preferred_skills + self.soft_skills

    @property
    def keyword_weights(self) -> dict[str, Weight]:
        """Canonical skill name -> weight, for the scorer and the LLM prompt."""
        return {s.name: s.weight for s in self.all_skills}
