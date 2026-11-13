"""Structured output requested from Gemini for resume tailoring.

The same models serve as the ``response_schema`` sent to Gemini and as the
validator for what comes back.

Since tailor-v3 (M11.1) the model no longer returns whole sections: it returns
one rewrite per bullet id, and whole LaTeX only for skills sections. The
backend splices and checks everything (``app/tailor/``). tailor-v4 (M11.2)
adds ``changed`` and a per-bullet ``reason``. ``VerificationResponse`` is the
second, semantic-check call (verify-v1).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class BulletRewrite(BaseModel):
    id: str = Field(description="The input bullet id, e.g. 'exp1.b3'")
    text: str = Field(
        description="The bullet content as LaTeX (what goes inside \\resumeItem{...} "
        "or after \\item): no \\item, no \\resumeItem, no environments. The original "
        "text unchanged if changed is false."
    )
    changed: bool = Field(default=True, description="False if the bullet was left as is")
    reason: str = Field(
        default="",
        description="Why it was rewritten (which requirement it now serves) or why not",
    )
    position: int | None = Field(
        default=None,
        description="Optional new 1-based position of this bullet within its own entry",
    )


class TailoredSection(BaseModel):
    section_id: str = Field(description="The id of the input skills section, e.g. 's4'")
    latex: str = Field(
        description="The complete rewritten LaTeX for this section, starting with its "
        "\\section command. Body-level LaTeX only: no preamble, no \\begin{document}."
    )


class LLMAtsAnalysis(BaseModel):
    score: int = Field(ge=0, le=100, description="ATS/JD alignment of the tailored resume")
    reasoning: str = Field(description="2-4 sentences explaining the score")
    matched_keywords: list[str] = Field(description="JD keywords present in the tailored resume")
    missing_keywords: list[str] = Field(description="JD keywords still absent")
    improvement_suggestions: list[str] = Field(
        description="What the candidate could still do that the rewrite cannot"
    )


class TailoringResponse(BaseModel):
    bullets: list[BulletRewrite]
    skills_sections: list[TailoredSection] = Field(default_factory=list)
    ats_analysis: LLMAtsAnalysis
    notes: list[str] = Field(
        default_factory=list,
        description="Short notes on the reasoning behind the rewrite (shown as the model's notes)",
    )


Verdict = Literal["supported", "partially_supported", "unsupported"]


class VerificationVerdict(BaseModel):
    id: str = Field(description="The bullet id being verified")
    verdict: Verdict
    evidence: str = Field(
        description="An exact quote from the original resume that supports the rewrite"
    )
    problem: str = Field(default="", description="What the rewrite claims that is unsupported")
    wording_only: bool = Field(
        default=False,
        description="For partially_supported: true if the only unsupported part is wording "
        "(phrasing or emphasis), not a new fact, tool, number, scope or ownership claim",
    )


class VerificationResponse(BaseModel):
    results: list[VerificationVerdict]
