"""Deterministic programmatic ATS scorer (PRD FR-04).

Inputs are the parsed resume (M2) and the analyzed JD (M3). Five criteria,
with PRD weights:

=====================  ===  ==========================================================
mandatory_skills        35  share of mandatory JD skills found in the resume
experience_alignment    25  TF-IDF cosine of each JD responsibility vs best resume bullet
preferred_skills        15  share of preferred JD skills found
keyword_coverage        15  share of JD tech-term *mentions* found (frequency-weighted)
structure               10  standard headings, no complex formatting, bullet usage
=====================  ===  ==========================================================

Matching lives in ``app/matching`` (M11.2) and is shared with the tailoring
prompt and the analysis output:

* skills: exact or alias/implied (credit 1.0), fuzzy (FUZZY_CREDIT 0.8),
  related through the taxonomy hierarchy, e.g. PostgreSQL for "relational
  databases" (HIERARCHY_CREDIT 0.5, shown as partially matched), missing (0);
* responsibilities: TF-IDF cosine vs the best bullet, with skill aliases and
  hierarchy parents canonicalised; credit min(1, cosine / 0.35).

A criterion with nothing to measure (e.g. a JD with no preferred skills) is
marked not applicable, and the total is rescaled over the applicable ones.
"""

from __future__ import annotations

import re

from app.jd.models import JDRequirements
from app.jd.taxonomy import Taxonomy, get_taxonomy
from app.latex.models import ParsedResume
from app.latex.parser import latex_to_plain, mask_latex
from app.matching.matcher import (
    ALIGNMENT_FULL_SIMILARITY,
    FUZZY_CREDIT,
    HIERARCHY_CREDIT,
    TYPO_THRESHOLD,
    match_requirements,
)
from app.matching.models import RequirementMatch
from app.scoring.models import (
    ATSScore,
    CriterionScore,
    KeywordMatch,
    MissingKeyword,
)

__all__ = [
    "score_resume",
    "FUZZY_CREDIT",
    "HIERARCHY_CREDIT",
    "TYPO_THRESHOLD",
    "ALIGNMENT_FULL_SIMILARITY",
]

WEIGHTS = {
    "mandatory_skills": 35.0,
    "experience_alignment": 25.0,
    "preferred_skills": 15.0,
    "keyword_coverage": 15.0,
    "structure": 10.0,
}
LABELS = {
    "mandatory_skills": "Mandatory skills coverage",
    "experience_alignment": "Experience & responsibility alignment",
    "preferred_skills": "Preferred skills coverage",
    "keyword_coverage": "Technical keyword coverage",
    "structure": "ATS structure & readability",
}


def _keyword(m: RequirementMatch, weight: str) -> KeywordMatch:
    return KeywordMatch(
        skill=m.requirement,
        weight=weight,  # type: ignore[arg-type]
        match_type=m.match_type or "exact",
        resume_term=m.resume_term or "",
        credit=m.credit,
    )


# --------------------------------------------------------------------------
# Structure checks
# --------------------------------------------------------------------------

_HEADING_POINTS = {"experience": 1.5, "skills": 1.5, "education": 1.0}  # = 4
_FORMATTING_POINTS = 3.0
_BULLET_POINTS = 3.0
_COMPLEX_FORMATTING = {
    "graphics or images": re.compile(r"\\includegraphics"),
    "multi-column layout": re.compile(
        r"\\begin\s*\{(?:multicols\*?|paracol)\}|\\twocolumn\b"
        r"|\\usepackage(?:\[[^\]]*\])?\{[^}]*\b(?:multicol|paracol)\b"
        r"|\\documentclass\[[^\]]*\btwocolumn\b"
    ),
    "icon fonts": re.compile(r"\\fa[A-Z][A-Za-z]*|\\usepackage(?:\[[^\]]*\])?\{[^}]*fontawesome"),
    "drawings (TikZ)": re.compile(r"\\begin\s*\{tikzpicture\}"),
}
# Tables are only flagged in the body: templates such as Jake's Resume use
# tabular* inside preamble macros for date alignment, which ATS parse fine.
_BODY_TABLES = re.compile(r"\\multicolumn|\\begin\s*\{(?:tabular|tabularx|longtable)\*?\}")


def _structure(resume: ParsedResume) -> tuple[float, list[str], str]:
    """Return (percent, issues, details)."""
    if not resume.sections:
        return 0.0, ["No recognizable sections (\\section headings) were found."], "no sections"

    issues: list[str] = []
    points = 0.0
    present = {s.section_type for s in resume.sections}
    for section_type, value in _HEADING_POINTS.items():
        if section_type in present:
            points += value
        else:
            issues.append(f"Add a standard '{section_type.capitalize()}' section heading.")

    source = mask_latex(resume.reconstruct())
    body = source[len(resume.preamble) :]
    found = [name for name, rx in _COMPLEX_FORMATTING.items() if rx.search(source)]
    if _BODY_TABLES.search(body):
        found.append("tables in the resume body")
    points += _FORMATTING_POINTS * (1 - min(len(found), 3) / 3)
    if found:
        issues.append(f"Avoid ATS-unfriendly formatting: {', '.join(found)}.")

    entries = [
        e
        for s in resume.sections
        if s.section_type in ("experience", "projects") and s.content
        for e in s.content.entries
    ]
    total_bullets = sum(len(s.content.all_bullets) for s in resume.sections if s.content)
    if entries:
        with_bullets = sum(1 for e in entries if e.bullets)
        points += _BULLET_POINTS * with_bullets / len(entries)
        if with_bullets < len(entries):
            issues.append(
                f"Add bullet points under every experience/project entry "
                f"({len(entries) - with_bullets} of {len(entries)} have none)."
            )
    else:
        points += _BULLET_POINTS * min(total_bullets, 3) / 3
        if total_bullets < 3:
            issues.append("Use bullet points to describe your experience.")

    details = (
        f"headings: {sorted(t for t in present if t in _HEADING_POINTS)}, "
        f"formatting issues: {len(found)}, bullets: {total_bullets}"
    )
    return points / 10.0, issues, details


# --------------------------------------------------------------------------
# Main entry point
# --------------------------------------------------------------------------


def _criterion(key: str, percent: float | None, details: str) -> CriterionScore:
    max_points = WEIGHTS[key]
    applicable = percent is not None
    pct = max(0.0, min(1.0, percent)) if percent is not None else None
    return CriterionScore(
        key=key,  # type: ignore[arg-type]
        label=LABELS[key],
        max_points=max_points,
        points=round(max_points * pct, 2) if pct is not None else 0.0,
        percent=round(pct, 4) if pct is not None else None,
        applicable=applicable,
        details=details,
    )


def _join(names: list[str], limit: int = 8) -> str:
    shown = names[:limit]
    more = f" and {len(names) - limit} more" if len(names) > limit else ""
    return ", ".join(shown) + more


def score_resume(
    resume: ParsedResume, jd: JDRequirements, taxonomy: Taxonomy | None = None
) -> ATSScore:
    """Score how well ``resume`` matches ``jd``. Deterministic; never raises
    on empty inputs."""
    taxonomy = taxonomy or get_taxonomy()
    text = "\n".join(latex_to_plain(seg.text) for seg in resume.segments).strip()
    matches = match_requirements(resume, jd, taxonomy)
    warnings: list[str] = []
    if not text:
        warnings.append("Resume is empty or has no document body.")
    raw_sections = [s for s in resume.segments if s.kind == "raw" and s.error and s.name]
    if raw_sections:
        warnings.append(
            f"{len(raw_sections)} section(s) could not be parsed and were scored as plain text."
        )

    # 1 + 3: mandatory and preferred coverage (partial credit for fuzzy/related).
    matched_mand = [_keyword(m, "high") for m in matches.mandatory if m.status != "missing"]
    matched_pref = [_keyword(m, "medium") for m in matches.preferred if m.status != "missing"]
    missing_mand = [m for m in matches.mandatory if m.status == "missing"]
    missing_pref = [m for m in matches.preferred if m.status == "missing"]

    def coverage(matched: list[KeywordMatch], total: int) -> float | None:
        return sum(m.credit for m in matched) / total if total else None

    mand_pct = coverage(matched_mand, len(jd.mandatory_skills))
    pref_pct = coverage(matched_pref, len(jd.preferred_skills))

    # 4: frequency-weighted coverage of every JD tech term.
    credit = {m.skill: m.credit for m in matched_mand + matched_pref}
    tech = jd.mandatory_skills + jd.preferred_skills
    mentions = sum(s.occurrences for s in tech)
    kw_pct = (
        sum(s.occurrences * credit.get(s.name, 0.0) for s in tech) / mentions if mentions else None
    )

    # 2: responsibilities vs bullets (from the requirement matching).
    align_pct: float | None = None
    weakest: list[str] = []
    if matches.responsibilities:
        align_pct = sum(m.credit for m in matches.responsibilities) / len(matches.responsibilities)
        weakest = [
            m.requirement
            for m in sorted(matches.responsibilities, key=lambda m: m.credit)
            if m.credit < 1.0
        ][:2]

    # 5: structure.
    struct_pct, structure_issues, struct_details = _structure(resume)

    criteria = [
        _criterion(
            "mandatory_skills",
            mand_pct,
            f"{len(matched_mand)}/{len(jd.mandatory_skills)} mandatory skills matched"
            if jd.mandatory_skills
            else "JD lists no mandatory skills",
        ),
        _criterion(
            "experience_alignment",
            align_pct,
            f"{len(jd.responsibilities)} responsibilities vs {matches.bullet_count} resume bullets"
            if jd.responsibilities
            else "JD lists no responsibilities",
        ),
        _criterion(
            "preferred_skills",
            pref_pct,
            f"{len(matched_pref)}/{len(jd.preferred_skills)} preferred skills matched"
            if jd.preferred_skills
            else "JD lists no preferred skills",
        ),
        _criterion(
            "keyword_coverage",
            kw_pct,
            f"{sum(s.occurrences for s in tech if s.name in credit)}/{mentions} "
            "JD technical-term mentions covered"
            if mentions
            else "JD has no technical terms",
        ),
        _criterion("structure", struct_pct, struct_details),
    ]

    applicable = [c for c in criteria if c.applicable]
    if len(applicable) < len(criteria):
        if len(applicable) == 1:
            warnings.append(
                "The JD has no extractable requirements; the score reflects resume structure only."
            )
        else:
            skipped = [c.label for c in criteria if not c.applicable]
            warnings.append(f"Not applicable (weights rescaled): {', '.join(skipped)}.")
    max_total = sum(c.max_points for c in applicable)
    total = round(100 * sum(c.points for c in applicable) / max_total, 1) if max_total else 0.0

    missing_keywords = [
        MissingKeyword(skill=m.requirement, weight="high", priority="high") for m in missing_mand
    ] + [
        MissingKeyword(skill=m.requirement, weight="medium", priority="medium")
        for m in missing_pref
    ]

    summary: list[str] = []
    if missing_mand:
        summary.append(
            "Add missing mandatory skills (high priority) where you have real experience: "
            f"{_join([m.requirement for m in missing_mand])}."
        )
    fuzzy = [m for m in matched_mand + matched_pref if m.match_type == "fuzzy"]
    if fuzzy:
        summary.append(
            "Use the JD's exact wording for: "
            + _join([f"{m.skill} (resume: '{m.resume_term}')" for m in fuzzy], limit=4)
            + "."
        )
    if align_pct is not None and align_pct < 0.8 and weakest:
        summary.append(
            "Rewrite bullets to mirror these responsibilities: "
            + "; ".join(f"'{w}'" for w in weakest)
            + "."
        )
    if missing_pref:
        summary.append(
            "Consider adding preferred skills if you have them: "
            f"{_join([m.requirement for m in missing_pref])}."
        )
    summary.extend(structure_issues[:2])
    if not summary:
        summary.append("Strong match: all extracted requirements are covered.")

    return ATSScore(
        total=total,
        criteria=criteria,
        matched_mandatory=matched_mand,
        matched_preferred=matched_pref,
        missing_keywords=missing_keywords,
        structure_issues=structure_issues,
        improvement_summary=summary,
        warnings=warnings,
        requirement_matches=matches.requirements,
    )
