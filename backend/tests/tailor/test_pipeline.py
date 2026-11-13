"""Pipeline-level tests (no HTTP). Gemini is always faked."""

import asyncio

import pytest

from app.latex.parser import parse_resume
from app.llm.models import TailoredSection, TailoringResponse
from app.llm.prompts import PROMPT_VERSION
from app.tailor.pipeline import PipelineInputError, ResultCache, TailorPipeline, cache_key
from tests.tailor.conftest import (
    BACKEND_JD,
    FRONTEND_JD,
    JAKES,
    FakeGemini,
    analysis,
    identity_response,
)


def run(pipeline: TailorPipeline, latex: str = JAKES, jd: str = BACKEND_JD):
    return asyncio.run(pipeline.run(latex, jd))


def pipeline_returning(build) -> TailorPipeline:  # type: ignore[no-untyped-def]
    """Pipeline whose fake Gemini returns ``build(resume)``."""
    fake = FakeGemini(behaviour=lambda r, j: build(r))
    return TailorPipeline(client_factory=lambda: fake, model_name="m")


def with_skills(resume, latex: str) -> TailoringResponse:  # type: ignore[no-untyped-def]
    response = identity_response(resume)
    return response.model_copy(
        update={"skills_sections": [TailoredSection(section_id="s4", latex=latex)]}
    )


# --- happy path -----------------------------------------------------------------------


def test_full_pipeline_happy_path(pipeline: TailorPipeline, fake: FakeGemini) -> None:
    result = run(pipeline)

    assert fake.calls == 1
    assert result.llm_score.score == 87
    assert result.llm_score.missing_keywords == ["Helm", "Redis"]
    assert 0 <= result.programmatic_score.total <= 100
    assert [c.key for c in result.programmatic_score.criteria] == [
        "mandatory_skills", "experience_alignment", "preferred_skills",
        "keyword_coverage", "structure",
    ]  # fmt: skip
    assert result.validation.errors == []
    assert result.jd_analysis.title == "Senior Backend Engineer — Payments Platform"
    # Computed by the backend from the diff, not taken from the model.
    assert result.changes_summary == [
        "Reordered bullets in Brightline Logistics, Software Engineer II (now b3, b1, b2)",
        "Reworded exp2.b1 (Brightline Logistics, Software Engineer I): States the REST API "
        "work in the JD's terms.",
        "Left 8 bullets unchanged",
        "Reorganized the 'Technical Skills' section (all original skills kept)",
    ]
    # The model's preservation claim is dropped; its other notes are kept.
    assert result.model_notes == ["Put the most payments-relevant bullet first."]
    assert result.content_check.reworded == 1
    assert result.content_check.reordered_entries == 1
    # Suggestions come from the deterministic requirement matching, never the
    # model's free text.
    assert "Gain hands-on experience with Helm." not in result.improvement_summary
    assert result.suggestions.surface and result.suggestions.missing
    assert any(m.requirement == "Helm" for m in result.suggestions.missing)
    assert result.prompt_version == PROMPT_VERSION
    assert result.cached is False


def test_tailored_content_is_applied_and_scored(pipeline: TailorPipeline) -> None:
    result = run(pipeline)
    assert "Go, Python, PostgreSQL, TypeScript," in result.latex
    assert "Developed REST APIs using \\textbf{FastAPI}" in result.latex
    matched = {m.skill for m in result.programmatic_score.matched_mandatory}
    assert "PostgreSQL" in matched
    # Nothing was added that the resume didn't have, so the score can't drop.
    assert result.programmatic_score.total >= result.original_programmatic_score


def test_original_preamble_appears_verbatim(pipeline: TailorPipeline) -> None:
    result = run(pipeline)
    preamble = parse_resume(JAKES).preamble
    assert preamble.endswith("\\begin{document}")
    assert result.latex.startswith(preamble)
    assert result.latex.count("\\documentclass") == 1
    assert result.latex.rstrip().endswith("\\end{document}")


def test_section_order_header_and_end_stay_in_place(pipeline: TailorPipeline) -> None:
    latex = run(pipeline).latex
    body = latex[len(parse_resume(JAKES).preamble) :]
    order = [
        "Jordan Rivera",
        "\\section{Education}",
        "\\section{Experience}",
        "\\section{Projects}",
        "\\section{Technical Skills}",
        "\\end{document}",
    ]
    positions = [body.index(t) for t in order]
    assert positions == sorted(positions)
    reparsed = parse_resume(latex)
    assert [s.name for s in reparsed.sections] == [
        "Education", "Experience", "Projects", "Technical Skills",
    ]  # fmt: skip


def test_output_with_crlf_input_keeps_preamble_bytes() -> None:
    crlf = JAKES.replace("\n", "\r\n")
    pipeline = TailorPipeline(client_factory=lambda: FakeGemini(), model_name="m")
    result = run(pipeline, latex=crlf)
    assert result.latex.startswith(parse_resume(crlf).preamble)


# --- reassembly safety --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("bad_latex", "reason"),
    [
        ("\\section{Skills}\n\\usepackage{evil}\nGo", "preamble-level commands"),
        ("\\section{Skills}\n\\textbf{Go", "unbalanced braces or environments"),
        ("\\section{Skills}\n\\begin{itemize}\\item Go", "unbalanced braces or environments"),
        ("Just some skills without a heading", "did not start with a \\section command"),
        ("   ", "was empty"),
    ],
)
def test_bad_tailored_skills_section_falls_back_to_original(bad_latex: str, reason: str) -> None:
    original = parse_resume(JAKES).segments[4].text
    result = run(pipeline_returning(lambda r: with_skills(r, bad_latex)))
    assert original in result.latex
    assert bad_latex.strip() not in result.latex or not bad_latex.strip()
    assert any(reason in w and "Technical Skills" in w for w in result.warnings)
    assert result.validation.errors == []


def test_unknown_and_duplicate_skills_sections_are_ignored() -> None:
    def build(resume):  # type: ignore[no-untyped-def]
        response = identity_response(resume)
        extra = [
            TailoredSection(section_id="s4", latex="\\section{Technical Skills}\nDuplicate."),
            TailoredSection(section_id="s2", latex="\\section{Experience}\nNot a skills section."),
        ]
        return response.model_copy(update={"skills_sections": response.skills_sections + extra})

    result = run(pipeline_returning(build))
    assert "Duplicate." not in result.latex and "Not a skills section." not in result.latex
    assert any("duplicate" in w for w in result.warnings)
    assert any("unknown id 's2'" in w for w in result.warnings)


def test_unparsed_sections_pass_through_unchanged(fake: FakeGemini) -> None:
    from tests.tailor.conftest import FIXTURES

    malformed = (FIXTURES / "resumes" / "malformed_resume.tex").read_text()
    pipeline = TailorPipeline(client_factory=lambda: fake, model_name="m")
    result = run(pipeline, latex=malformed)
    projects = next(s for s in parse_resume(malformed).segments if s.name == "Projects")
    assert projects.text in result.latex
    assert any("Could not parse 'Projects'" in w for w in result.warnings)


# --- invalid input -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("latex", "message"),
    [
        ("\\section{Experience}\nNo document environment.", "no \\begin{document}"),
        ("\\documentclass{article}\\begin{document}Just text.\\end{document}", "No \\section"),
    ],
)
def test_untailorable_input_raises_pipeline_input_error(
    pipeline: TailorPipeline, fake: FakeGemini, latex: str, message: str
) -> None:
    with pytest.raises(PipelineInputError, match=message.replace("\\", "\\\\")):
        run(pipeline, latex=latex)
    assert fake.calls == 0


# --- cache -------------------------------------------------------------------------------


def test_cache_hit_does_not_call_gemini_again(pipeline: TailorPipeline, fake: FakeGemini) -> None:
    first = run(pipeline)
    second = run(pipeline)
    assert fake.calls == 1
    assert (first.cached, second.cached) == (False, True)
    assert second.latex == first.latex
    assert second.model_dump(exclude={"cached"}) == first.model_dump(exclude={"cached"})


def test_cache_miss_on_different_input(pipeline: TailorPipeline, fake: FakeGemini) -> None:
    run(pipeline)
    run(pipeline, jd=FRONTEND_JD)
    run(pipeline, latex=JAKES + "\n% trailing comment")
    assert fake.calls == 3
    assert len(pipeline.cache) == 3


def test_failures_are_not_cached() -> None:
    from app.llm.errors import GeminiTimeoutError

    fake = FakeGemini(error=GeminiTimeoutError("Gemini did not respond within 60 seconds."))
    pipeline = TailorPipeline(client_factory=lambda: fake, model_name="m")
    with pytest.raises(GeminiTimeoutError):
        run(pipeline)
    fake.error = None
    assert run(pipeline).cached is False
    assert fake.calls == 2


def test_cache_key_includes_prompt_version_and_model() -> None:
    base = cache_key("a", "b", model="m", prompt_version="v1")
    assert base == cache_key("a", "b", model="m", prompt_version="v1")
    assert base != cache_key("a", "b", model="m", prompt_version="v2")
    assert base != cache_key("a", "b", model="other", prompt_version="v1")
    assert base != cache_key("a", "c", model="m", prompt_version="v1")
    # No ambiguity from concatenation: ("ab", "") vs ("a", "b").
    assert cache_key("ab", "", model="m", prompt_version="v") != cache_key(
        "a", "b", model="m", prompt_version="v"
    )


def test_prompt_version_change_invalidates_cache(
    monkeypatch: pytest.MonkeyPatch, pipeline: TailorPipeline, fake: FakeGemini
) -> None:
    run(pipeline)
    monkeypatch.setattr("app.tailor.pipeline.PROMPT_VERSION", "tailor-next")
    result = run(pipeline)
    assert fake.calls == 2
    assert result.prompt_version == "tailor-next"


def test_cache_is_lru_bounded() -> None:
    cache = ResultCache(max_entries=2)
    fake = FakeGemini()
    pipeline = TailorPipeline(client_factory=lambda: fake, model_name="m", cache=cache)
    run(pipeline, jd="Requirements: Go")
    run(pipeline, jd="Requirements: Rust")
    run(pipeline, jd="Requirements: Go")  # hit, becomes most recent
    run(pipeline, jd="Requirements: Java")  # evicts Rust
    run(pipeline, jd="Requirements: Rust")  # miss again
    assert fake.calls == 4
    assert len(cache) == 2


def test_client_is_created_lazily() -> None:
    created = []

    def factory():
        created.append(1)
        return FakeGemini()

    pipeline = TailorPipeline(client_factory=factory, model_name="m")
    assert created == []
    run(pipeline)
    run(pipeline, jd=FRONTEND_JD)
    assert created == [1]


def test_original_missing_keywords_are_passed_to_gemini(
    pipeline: TailorPipeline, fake: FakeGemini
) -> None:
    result = run(pipeline)
    # Exactly the skills the ORIGINAL resume lacked. Tailoring never adds them,
    # so Redis is still missing afterwards.
    assert "Redis" in fake.last_missing_keywords
    assert "Redis" in [m.skill for m in result.programmatic_score.missing_keywords]
    assert "Go" not in fake.last_missing_keywords  # already in the resume


def test_timings_are_recorded(pipeline: TailorPipeline) -> None:
    timings = run(pipeline).timings_ms
    assert set(timings) == {
        "parse", "analyze_jd", "score_original", "gemini", "verify",
        "assemble_validate", "score_tailored", "total",
    }  # fmt: skip
    assert timings["total"] >= max(v for k, v in timings.items() if k != "total")


def test_skills_rewrite_keeps_original_trailing_whitespace() -> None:
    """A section ending in a separator comment for the next section must not
    gain a blank line between that comment and the next \\section."""
    src = (
        "\\documentclass{article}\n\\begin{document}\n"
        "\\section{Skills}\nGo, Python\n\n%---- MORE ----\n"
        "\\section{Experience}\nDid things.\n\\end{document}\n"
    )

    def build(resume):  # type: ignore[no-untyped-def]
        latex = "\\section{Skills}\nPython, Go\n\n%---- MORE ----"
        return identity_response(resume).model_copy(
            update={"skills_sections": [TailoredSection(section_id="s1", latex=latex)]}
        )

    latex = run(pipeline_returning(build), latex=src).latex
    assert "Python, Go\n\n%---- MORE ----\n\\section{Experience}" in latex


def test_llm_reasoning_is_trimmed() -> None:
    def build(resume):  # type: ignore[no-untyped-def]
        return identity_response(resume).model_copy(
            update={"ats_analysis": analysis(reasoning="  matches well.  \n")}
        )

    assert run(pipeline_returning(build)).llm_score.reasoning == "matches well."
