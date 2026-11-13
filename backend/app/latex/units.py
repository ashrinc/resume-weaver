"""Editable units of a parsed resume, with stable ids (M11.1).

Tailoring works on these units, not on whole sections: Gemini rewrites the
text of individual bullets by id, and the backend splices each rewrite back
into the exact span of the original bullet. Headings, dates, macros and
environments are never sent for rewriting, so they cannot change.

Ids look like ``exp1.b3``: a prefix for the section type, the entry number
within that type (counted across sections, in document order), then the
bullet number within the entry (1-based). Bullets that are not under an
entry heading form their own pseudo-entry. A summary section written as a
paragraph (no bullets, no entries) becomes a single unit ``sum1.b1``.

Skills sections are not split into units; they are sent as whole sections
(``s<segment index>``) because their layout varies too much.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.latex.models import Bullet, Entry, ParsedResume, SectionType, Segment
from app.latex.parser import latex_to_plain, mask_latex

__all__ = ["BulletUnit", "EntryUnit", "ResumeUnits", "extract_units", "section_id"]

_PREFIX: dict[SectionType | None, str] = {
    "experience": "exp",
    "projects": "proj",
    "education": "edu",
    "summary": "sum",
    "other": "sec",
    None: "sec",
}
# Anything that makes a paragraph more than plain running text.
_STRUCTURAL_RE = re.compile(
    r"\\(?:begin|end|item|section|subsection|resume[A-Za-z]*|cventry|cvitem)(?![a-zA-Z])"
)


def section_id(segment_index: int) -> str:
    """Stable id for the resume segment at ``segment_index``."""
    return f"s{segment_index}"


@dataclass(frozen=True)
class BulletUnit:
    id: str
    entry_key: str
    position: int  # 1-based position within the entry
    latex: str  # exact original LaTeX of the bullet content
    text: str  # plain text
    start: int  # absolute offsets into the original source
    end: int


@dataclass
class EntryUnit:
    key: str  # e.g. "exp1"
    label: str  # human label for messages: organization, else title, else section
    heading: str  # plain heading line sent to the model as context
    section_index: int
    section_name: str
    bullets: list[BulletUnit] = field(default_factory=list)


@dataclass
class ResumeUnits:
    entries: list[EntryUnit]
    skills_section_ids: list[str]  # "s<index>" of skills sections, sent whole

    @property
    def bullets(self) -> dict[str, BulletUnit]:
        return {b.id: b for e in self.entries for b in e.bullets}

    def entry_of(self, bullet_id: str) -> EntryUnit:
        key = bullet_id.split(".", 1)[0]
        return next(e for e in self.entries if e.key == key)


def _heading(entry: Entry | None) -> str:
    if entry is None:
        return ""
    parts = [entry.title, entry.organization, entry.location, entry.dates]
    text = " | ".join(p for p in parts if p)
    if entry.technologies:
        text += " | tech: " + ", ".join(entry.technologies)
    return text


def _paragraph_bullet(source: str, segment: Segment) -> Bullet | None:
    """The body of a paragraph-only section as one bullet, or None."""
    masked = mask_latex(source)
    body_start = segment.start
    title = re.compile(r"\\section\*?\s*\{").search(masked, segment.start, segment.end)
    if title is None:
        return None
    depth, i = 1, title.end()
    while i < segment.end and depth:
        depth += {"{": 1, "}": -1}.get(masked[i], 0)
        i += 1
    body_start = i
    region = masked[body_start : segment.end]
    stripped = region.strip()
    if not stripped or _STRUCTURAL_RE.search(stripped):
        return None
    start = body_start + (len(region) - len(region.lstrip()))
    end = body_start + len(region.rstrip())
    latex = source[start:end]
    if "%" in latex.replace("\\%", ""):  # contains a comment: not safe to replace
        return None
    text = latex_to_plain(latex).strip()
    if not text:
        return None
    return Bullet(text=" ".join(text.split()), latex=latex, start=start, end=end)


def extract_units(resume: ParsedResume) -> ResumeUnits:
    entries: list[EntryUnit] = []
    skills: list[str] = []
    counters: dict[str, int] = {}
    titles: dict[str, str | None] = {}
    source = resume.reconstruct()

    for index, seg in enumerate(resume.segments):
        if seg.kind != "section" or seg.content is None:
            continue
        if seg.section_type == "skills":
            skills.append(section_id(index))
            continue
        prefix = _PREFIX[seg.section_type]
        groups: list[tuple[Entry | None, list[Bullet]]] = []
        if seg.content.bullets:
            groups.append((None, seg.content.bullets))
        groups.extend((e, e.bullets) for e in seg.content.entries if e.bullets)
        if not groups and seg.section_type == "summary" and not seg.content.entries:
            paragraph = _paragraph_bullet(source, seg)
            if paragraph is not None:
                groups.append((None, [paragraph]))

        for entry, bullets in groups:
            counters[prefix] = counters.get(prefix, 0) + 1
            key = f"{prefix}{counters[prefix]}"
            label = ((entry.organization or entry.title) if entry is not None else None) or (
                seg.name or key
            )
            unit = EntryUnit(
                key=key,
                label=label,
                heading=_heading(entry),
                section_index=index,
                section_name=seg.name or "",
            )
            for pos, b in enumerate(sorted(bullets, key=lambda b: b.start), start=1):
                unit.bullets.append(
                    BulletUnit(
                        id=f"{key}.b{pos}",
                        entry_key=key,
                        position=pos,
                        latex=b.latex,
                        text=b.text,
                        start=b.start,
                        end=b.end,
                    )
                )
            entries.append(unit)
            titles[key] = entry.title if entry is not None else None

    # Two roles at the same company: add the title so messages are unambiguous.
    labels = [e.label for e in entries]
    for e in entries:
        title = titles.get(e.key)
        if labels.count(e.label) > 1 and title and title != e.label:
            e.label = f"{e.label}, {title}"

    _check_no_overlaps(entries)
    return ResumeUnits(entries=entries, skills_section_ids=skills)


def _check_no_overlaps(entries: list[EntryUnit]) -> None:
    """Defensive: bullet spans must not overlap, or splicing would corrupt text."""
    spans = sorted((b.start, b.end) for e in entries for b in e.bullets)
    for (_, a_end), (b_start, _) in zip(spans, spans[1:], strict=False):
        if b_start < a_end:
            raise ValueError("overlapping bullet spans")
