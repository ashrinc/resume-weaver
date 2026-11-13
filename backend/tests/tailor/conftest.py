"""Shared fixtures: a fake Gemini client (no network) and app/pipeline builders."""

from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.jd.models import JDRequirements
from app.latex.models import ParsedResume
from app.latex.units import extract_units
from app.llm.models import (
    BulletRewrite,
    LLMAtsAnalysis,
    TailoredSection,
    TailoringResponse,
    VerificationResponse,
    VerificationVerdict,
)
from app.llm.prompts import VerifyItem
from app.main import create_app
from app.tailor.pipeline import TailorPipeline

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
JAKES = (FIXTURES / "resumes" / "jakes_resume.tex").read_text()
BACKEND_JD = (FIXTURES / "jds" / "backend_engineer.txt").read_text()
FRONTEND_JD = (FIXTURES / "jds" / "frontend_engineer.txt").read_text()


def analysis(score: int = 70, **kwargs) -> LLMAtsAnalysis:
    return LLMAtsAnalysis(
        score=score,
        reasoning=kwargs.get("reasoning", "ok"),
        matched_keywords=kwargs.get("matched_keywords", []),
        missing_keywords=kwargs.get("missing_keywords", []),
        improvement_suggestions=kwargs.get("improvement_suggestions", []),
    )


def identity_response(resume: ParsedResume) -> TailoringResponse:
    """Every bullet and skills section returned unchanged."""
    units = extract_units(resume)
    return TailoringResponse(
        bullets=[
            BulletRewrite(id=b.id, text=b.latex, changed=False, reason="already aligned")
            for b in units.bullets.values()
        ],
        skills_sections=[
            TailoredSection(section_id=sid, latex=resume.segments[int(sid[1:])].text.strip())
            for sid in units.skills_section_ids
        ],
        ats_analysis=analysis(),
    )


def edit(
    response: TailoringResponse, bullet_id: str, latex: str | None = None, **changes
) -> TailoringResponse:
    """Copy of ``response`` with one bullet rewritten (and/or repositioned)."""
    bullets = []
    for b in response.bullets:
        if b.id == bullet_id:
            update = {"text": latex, "changed": True, "reason": ""} if latex is not None else {}
            b = b.model_copy(update={**update, **changes})
        bullets.append(b)
    return response.model_copy(update={"bullets": bullets})


def realistic_tailoring(resume: ParsedResume, jd: JDRequirements) -> TailoringResponse:
    """One bullet reworded, one entry reordered, PostgreSQL (already used in a
    bullet) added to the skills, plus a model note claiming preservation."""
    response = identity_response(resume)
    if "Developed REST APIs with" in resume.reconstruct():
        response = edit(
            response,
            "exp2.b1",
            "Developed REST APIs using \\textbf{FastAPI} and \\textbf{PostgreSQL} for the "
            "carrier portal",
            reason="States the REST API work in the JD's terms.",
        )
        response = edit(response, "exp1.b3", position=1)
    skills = [
        s.model_copy(
            update={
                "latex": s.latex.replace(
                    "\\textbf{Languages}{: Go, Python,",
                    "\\textbf{Languages}{: Go, Python, PostgreSQL,",
                )
            }
        )
        for s in response.skills_sections
    ]
    return response.model_copy(
        update={
            "skills_sections": skills,
            "ats_analysis": analysis(
                87,
                reasoning="Strong Go/Python backend match; Kubernetes depth is limited.",
                matched_keywords=["Go", "Python", "PostgreSQL"],
                missing_keywords=["Helm", "Redis"],
                improvement_suggestions=["Gain hands-on experience with Helm."],
            ),
            "notes": [
                "Put the most payments-relevant bullet first.",
                "All factual details were preserved.",
            ],
        }
    )


def supported_everything(resume_text: str, items: list[VerifyItem]) -> VerificationResponse:
    """Default fake verifier: every rewrite supported, quoting its original."""
    return VerificationResponse(
        results=[
            VerificationVerdict(id=i.id, verdict="supported", evidence=i.original) for i in items
        ]
    )


class FakeGemini:
    """Stands in for GeminiClient. ``behaviour`` returns a tailoring response
    or raises; ``verifier`` answers the second (verification) call."""

    def __init__(
        self,
        behaviour: Callable[[ParsedResume, JDRequirements], TailoringResponse] | None = None,
        error: Exception | None = None,
        verifier: Callable[[str, list[VerifyItem]], VerificationResponse] | None = None,
        verify_error: Exception | None = None,
    ) -> None:
        self.behaviour = behaviour or realistic_tailoring
        self.error = error
        self.verifier = verifier or supported_everything
        self.verify_error = verify_error
        self.calls = 0
        self.verify_calls: list[list[VerifyItem]] = []
        self.request_count = 0

    async def tailor_resume(self, resume, jd, keywords=None, missing_keywords=None, plan=None):
        self.calls += 1
        self.request_count += 1
        self.last_missing_keywords = missing_keywords
        self.last_plan = plan
        if self.error is not None:
            raise self.error
        return self.behaviour(resume, jd)

    async def verify_rewrites(self, resume_text, items):
        self.request_count += 1
        self.verify_calls.append(items)
        self.last_resume_text = resume_text
        if self.verify_error is not None:
            raise self.verify_error
        return self.verifier(resume_text, items)


@pytest.fixture
def fake() -> FakeGemini:
    return FakeGemini()


@pytest.fixture
def pipeline(fake: FakeGemini) -> TailorPipeline:
    return TailorPipeline(client_factory=lambda: fake, model_name="test-model")


@pytest.fixture
def client(pipeline: TailorPipeline) -> TestClient:
    return TestClient(create_app(tailor_pipeline=pipeline))
