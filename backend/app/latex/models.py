"""Data models produced by the LaTeX resume parser (PRD FR-02).

All ``start``/``end`` values are absolute character offsets into the original
source string (``end`` exclusive), so ``source[start:end]`` is always the exact
original text. Later milestones use these offsets to splice rewritten content
back into the document.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SectionType = Literal["experience", "projects", "education", "skills", "summary", "other"]
SegmentKind = Literal["section", "raw"]


class Bullet(BaseModel):
    text: str = Field(description="Plain-text rendering of the bullet")
    latex: str = Field(description="Exact original LaTeX of the bullet content")
    start: int
    end: int
    metrics: list[str] = Field(
        default_factory=list, description="Quantified values found, e.g. '40%', '$1.2M', '10K+'"
    )

    @property
    def quantified(self) -> bool:
        return bool(self.metrics)


class Entry(BaseModel):
    """One heading inside a section: a job, degree or project."""

    title: str | None = None
    organization: str | None = Field(default=None, description="Company or institution")
    location: str | None = None
    dates: str | None = Field(
        default=None, description="Date text as written, e.g. 'May 2020 – Present'"
    )
    start_date: str | None = None
    end_date: str | None = None
    technologies: list[str] = Field(default_factory=list)
    bullets: list[Bullet] = Field(default_factory=list)
    start: int
    end: int


class SkillGroup(BaseModel):
    category: str | None = None
    skills: list[str] = Field(default_factory=list)


class SectionContent(BaseModel):
    title: str
    entries: list[Entry] = Field(default_factory=list)
    bullets: list[Bullet] = Field(
        default_factory=list, description="Bullets not under any entry heading"
    )
    skill_groups: list[SkillGroup] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    text: str = Field(default="", description="Plain text of the section body (title excluded)")

    @property
    def all_bullets(self) -> list[Bullet]:
        return self.bullets + [b for e in self.entries for b in e.bullets]

    @property
    def achievements(self) -> list[Bullet]:
        """Bullets containing at least one quantified metric."""
        return [b for b in self.all_bullets if b.quantified]


class Segment(BaseModel):
    """A contiguous slice of the document body.

    ``kind="section"`` segments were parsed and may be edited later.
    ``kind="raw"`` segments must be passed through untouched: the header before
    the first section, ``\\end{document}`` and anything after it, and any
    section that failed to parse (``error`` says why).
    """

    kind: SegmentKind
    name: str | None = None
    section_type: SectionType | None = None
    start: int
    end: int
    text: str
    content: SectionContent | None = None
    error: str | None = None


class ParsedResume(BaseModel):
    preamble: str = Field(description="Everything up to and including \\begin{document}")
    segments: list[Segment] = Field(default_factory=list)
    has_document_body: bool = Field(
        description="False if no \\begin{document} was found (whole input kept as one raw segment)"
    )
    skills: list[str] = Field(default_factory=list, description="All skills, deduplicated")
    certifications: list[str] = Field(default_factory=list)

    @property
    def sections(self) -> list[Segment]:
        return [s for s in self.segments if s.kind == "section"]

    def sections_of(self, section_type: SectionType) -> list[Segment]:
        return [s for s in self.sections if s.section_type == section_type]

    @property
    def achievements(self) -> list[Bullet]:
        return [b for s in self.sections if s.content for b in s.content.achievements]

    def reconstruct(self) -> str:
        return self.preamble + "".join(s.text for s in self.segments)
