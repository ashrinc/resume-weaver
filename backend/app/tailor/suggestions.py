"""Improvement suggestions in two groups (M11.2), built from the requirement
matching of the FINAL resume. Never suggests fabricating anything:

* surface: requirements the resume supports only indirectly (partial
  matches): the candidate may want to state them explicitly;
* missing: requirements with no support at all: the app never adds these,
  and the candidate should only add them if they are genuinely true.
"""

from __future__ import annotations

from app.matching.models import RequirementMatch
from app.scoring.models import ATSScore
from app.tailor.models import Suggestion, SuggestionGroups

__all__ = ["build_suggestions", "flatten"]


def _surface(m: RequirementMatch) -> Suggestion:
    where = f" ({', '.join(m.bullet_ids)})" if m.bullet_ids else ""
    if m.kind == "responsibility":
        detail = (
            f"Related work exists{where}. If it really covered this, say so explicitly "
            "in that bullet."
        )
    else:
        detail = (
            f"Your resume shows {m.resume_term}{where}. If that work involved {m.requirement}, "
            f"name it there in the job's wording."
        )
    return Suggestion(requirement=m.requirement, detail=detail, evidence=m.evidence)


def _missing(m: RequirementMatch) -> Suggestion:
    if m.kind == "responsibility":
        detail = (
            "No bullet describes this. The app will not add it; add a bullet yourself only "
            "if it reflects work you actually did."
        )
    else:
        detail = (
            f"{m.requirement} does not appear in your resume. The app will never add it; "
            "add it yourself only if you have real experience with it."
        )
    return Suggestion(requirement=m.requirement, detail=detail)


def build_suggestions(score: ATSScore) -> SuggestionGroups:
    matches = score.requirement_matches
    return SuggestionGroups(
        surface=[_surface(m) for m in matches if m.status == "partially_matched"],
        missing=[_missing(m) for m in matches if m.status == "missing"],
        other=list(score.structure_issues),
    )


def flatten(groups: SuggestionGroups) -> list[str]:
    """Plain list for the legacy ``improvement_summary`` field."""
    return (
        [f"{s.requirement}: {s.detail}" for s in groups.surface]
        + [f"{s.requirement}: {s.detail}" for s in groups.missing]
        + groups.other
    )
