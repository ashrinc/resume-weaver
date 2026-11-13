"""Content preservation and two-layer validation (M11.1 + M11.2). The tailor
may reword, reorder and emphasize, but never delete content or add facts.
Every LLM call is faked."""

import asyncio

import pytest

from app.jd.analyzer import analyze_jd
from app.latex.parser import parse_resume
from app.latex.units import extract_units
from app.llm.errors import GeminiRateLimitError, GeminiTimeoutError
from app.llm.models import (
    BulletRewrite,
    TailoredSection,
    TailoringResponse,
    VerificationResponse,
    VerificationVerdict,
)
from app.scoring.scorer import score_resume
from app.tailor.apply import apply_tailoring, filter_model_notes
from app.tailor.guards import skill_items
from app.tailor.pipeline import PAGE_LINES, TailorPipeline, estimate_lines
from tests.tailor.conftest import BACKEND_JD, FIXTURES, JAKES, FakeGemini, edit, identity_response

SOURCE = (FIXTURES / "resumes" / "preservation_resume.tex").read_text()
RESUME = parse_resume(SOURCE)
UNITS = extract_units(RESUME)
ORIGINAL = {b.id: b.latex for b in UNITS.bullets.values()}
SMALL_JD = """Backend Engineer
Requirements:
- Python
- Experience with relational databases
- Kubernetes
"""


def apply(response: TailoringResponse, resume=RESUME):  # type: ignore[no-untyped-def]
    return apply_tailoring(resume, response)


def run_pipeline(  # type: ignore[no-untyped-def]
    response: TailoringResponse,
    latex: str = SOURCE,
    jd: str = BACKEND_JD,
    enable_verify: bool = False,
    **fake_kwargs,
):
    fake = FakeGemini(behaviour=lambda r, j: response, **fake_kwargs)
    pipeline = TailorPipeline(
        client_factory=lambda: fake, model_name="test-model-1", enable_verify=enable_verify
    )
    return asyncio.run(pipeline.run(latex, jd)), fake


def run_verified(response: TailoringResponse, **kwargs):  # type: ignore[no-untyped-def]
    """With ENABLE_VERIFY_CALL on: the second (verifier) call runs."""
    return run_pipeline(response, enable_verify=True, **kwargs)


def without(response: TailoringResponse, *ids: str) -> TailoringResponse:
    return response.model_copy(update={"bullets": [b for b in response.bullets if b.id not in ids]})


def verdicts(*items: VerificationVerdict):  # type: ignore[no-untyped-def]
    return lambda text, sent: VerificationResponse(results=list(items))


def bullet_counts(latex: str) -> list[list[int]]:
    return [
        [len(seg.content.bullets)] + [len(e.bullets) for e in seg.content.entries]
        for seg in parse_resume(latex).sections
        if seg.content is not None
    ]


def assert_all_originals_present(latex: str) -> None:
    for bid, original in ORIGINAL.items():
        assert original in latex, bid


# --- preservation: dropped, merged and unknown bullets -----------------------------------


def test_dropped_bullets_are_restored_with_a_warning() -> None:
    result, _ = run_pipeline(without(identity_response(RESUME), "exp1.b2", "exp1.b3", "proj1.b1"))
    assert_all_originals_present(result.latex)
    assert bullet_counts(result.latex) == bullet_counts(SOURCE)
    assert result.warnings[0] == (
        "The model dropped 3 bullets; originals restored (exp1.b2, exp1.b3, proj1.b1)."
    )
    assert "Restored exp1.b3 (Quillpay): the model omitted it" in result.changes_summary
    assert result.content_check.restored == 3


def test_restored_bullets_keep_their_original_position() -> None:
    response = edit(without(identity_response(RESUME), "exp1.b2"), "exp1.b4", position=1)
    entry = parse_resume(apply(response).latex).sections[0].content.entries[0]
    assert [b.latex for b in entry.bullets] == [
        ORIGINAL["exp1.b4"], ORIGINAL["exp1.b2"], ORIGINAL["exp1.b1"], ORIGINAL["exp1.b3"],
    ]  # fmt: skip


def test_merged_bullets_keep_both_originals() -> None:
    merged = (
        "Implemented salary top-up business rules for 3 employer plans in \\textbf{Python} "
        "and debugged TIFF-to-PNG conversion failures in the document upload pipeline"
    )
    result = apply(without(edit(identity_response(RESUME), "exp1.b1", merged), "exp1.b3"))
    assert ORIGINAL["exp1.b1"] in result.latex and ORIGINAL["exp1.b3"] in result.latex
    assert merged not in result.latex
    assert any(
        "exp1.b1" in w and "merge: absorbed the content of exp1.b3" in w for w in result.warnings
    )
    assert bullet_counts(result.latex) == bullet_counts(SOURCE)


def test_unknown_and_duplicate_ids_are_discarded() -> None:
    response = identity_response(RESUME)
    extra = [
        BulletRewrite(id="exp9.b1", text="Invented a whole new bullet"),
        BulletRewrite(id="exp1.b1", text="Second version of b1"),
    ]
    result = apply(response.model_copy(update={"bullets": response.bullets + extra}))
    assert "Invented a whole new bullet" not in result.latex
    assert "Second version of b1" not in result.latex
    assert any("unknown ids" in w and "exp9.b1" in w for w in result.warnings)
    assert any("duplicate" in w and "exp1.b1" in w for w in result.warnings)
    assert result.check is not None and result.check.discarded == 2


# --- Layer A: what passes ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("bullet", "rewrite"),
    [
        # "Improved query performance" -> "query optimization" (taxonomy synonym).
        ("exp2.b3", "Drove query optimization for reporting jobs in \\textbf{PostgreSQL}"),
        # REST endpoints -> RESTful API development.
        (
            "exp1.b4",
            "Delivered RESTful API development for AI-agent APIs, cutting manual lookups by 30\\%",
        ),
        # PostgreSQL supports "relational database" through the hierarchy.
        (
            "exp2.b3",
            "Improved query performance of reporting jobs on a \\textbf{PostgreSQL} relational "
            "database",
        ),
    ],
    ids=["query-optimization", "restful", "relational-database"],
)
def test_supported_rewrites_are_kept(bullet: str, rewrite: str) -> None:
    response = edit(identity_response(RESUME), bullet, rewrite, reason="JD wording")
    result, fake = run_verified(response)
    assert rewrite in result.latex
    assert ORIGINAL[bullet] not in result.latex
    assert f"Reworded {bullet} " in " ".join(result.changes_summary)
    assert [i.id for i in fake.verify_calls[0]] == [bullet]  # Layer B saw it
    assert result.content_check.reworded == 1 and result.content_check.reverted == 0


# --- Layer A: what is reverted -------------------------------------------------------------


@pytest.mark.parametrize(
    ("bullet", "rewrite", "rule"),
    [
        (
            "exp2.b3",
            "Improved query performance of reporting jobs in \\textbf{PostgreSQL} and MongoDB",
            'unsupported tool: "MongoDB"',
        ),
        (
            "exp1.b4",
            "Exposed REST endpoints for AI-agent APIs, cutting manual lookups by 45\\%",
            'new number: "45"',
        ),
        (
            "exp2.b1",
            "Fine-tuned a DistilBERT classifier on support tickets",
            'lost number: dropped "12"',
        ),
        (
            "exp1.b2",
            "Added an alternate signup flow behind feature flags, with rate limits to eliminate "
            "repeated attempts",
            'inflated claim: "prevent" became "eliminate"',
        ),
        (
            "proj1.b2",
            "Built the dashboard in \\textbf{React}, deployed backend services and frontend "
            "applications",
            'vaguer scope: dropped "Vercel"',
        ),
        (
            "exp2.b2",
            "Led the migration of nightly jobs to \\textbf{Airflow}",
            'inflated claim: "Helped" became "Led"',
        ),
    ],
    ids=["mongodb", "new-number", "lost-number", "eliminate", "vercel-render", "helped-led"],
)
def test_layer_a_reverts_with_the_rule_named(bullet: str, rewrite: str, rule: str) -> None:
    result, fake = run_pipeline(edit(identity_response(RESUME), bullet, rewrite))
    assert ORIGINAL[bullet] in result.latex and rewrite not in result.latex
    assert any(w.startswith(f"Reverted {bullet} ") and rule in w for w in result.warnings)
    assert fake.verify_calls == []  # nothing passed Layer A, so no second call
    assert result.content_check.verification == "not_needed"


@pytest.mark.parametrize(
    "rewrite",
    [
        "Cut manual lookups by 30% with REST endpoints for AI-agent APIs",  # unescaped %
        "Exposed REST endpoints}\\resumeItem{for AI-agent APIs, cutting lookups by 30\\%",
        "Exposed \\textbf{REST endpoints for AI-agent APIs, cutting lookups by 30\\%",
        "\\item Exposed REST endpoints for AI-agent APIs, cutting manual lookups by 30\\%",
        "   ",
    ],
    ids=["unescaped-percent", "structural-command", "unbalanced-brace", "item", "empty"],
)
def test_unsafe_latex_in_a_bullet_is_reverted(rewrite: str) -> None:
    result, _ = run_pipeline(edit(identity_response(RESUME), "exp1.b4", rewrite))
    assert ORIGINAL["exp1.b4"] in result.latex
    assert result.validation.errors == []
    assert bullet_counts(result.latex) == bullet_counts(SOURCE)


# --- Layer B: semantic verifier ------------------------------------------------------------

GOOD = "Drove query optimization for reporting jobs in \\textbf{PostgreSQL}"
GOOD_EVIDENCE = "Improved query performance of reporting jobs in PostgreSQL"


def test_verifier_with_fabricated_evidence_is_reverted() -> None:
    fabricated = VerificationVerdict(
        id="exp2.b3",
        verdict="supported",
        evidence="Led a database performance team of five engineers",  # not in the resume
    )
    result, _ = run_verified(
        edit(identity_response(RESUME), "exp2.b3", GOOD), verifier=verdicts(fabricated)
    )
    assert ORIGINAL["exp2.b3"] in result.latex and GOOD not in result.latex
    assert any(
        "semantic check: the cited evidence is not in the original resume" in w
        for w in result.warnings
    )
    assert result.content_check.semantic_reverts == 1
    assert result.content_check.verification == "ran"


@pytest.mark.parametrize(
    ("verdict", "wording_only", "kept"),
    [
        ("supported", False, True),
        ("partially_supported", True, True),
        ("partially_supported", False, False),
        ("unsupported", False, False),
    ],
)
def test_verifier_verdicts(verdict: str, wording_only: bool, kept: bool) -> None:
    v = VerificationVerdict(
        id="exp2.b3",
        verdict=verdict,  # type: ignore[arg-type]
        evidence=GOOD_EVIDENCE,
        problem="" if kept else "claims more than the original",
        wording_only=wording_only,
    )
    result, _ = run_verified(edit(identity_response(RESUME), "exp2.b3", GOOD), verifier=verdicts(v))
    assert (GOOD in result.latex) is kept
    assert (ORIGINAL["exp2.b3"] in result.latex) is not kept


def test_evidence_check_normalises_whitespace_case_and_quotes() -> None:
    v = VerificationVerdict(
        id="exp2.b3",
        verdict="supported",
        evidence="  \u201cimproved   QUERY performance of reporting jobs\u201d  ",
    )
    result, _ = run_verified(edit(identity_response(RESUME), "exp2.b3", GOOD), verifier=verdicts(v))
    assert GOOD in result.latex


def test_missing_verdict_is_reverted() -> None:
    response = edit(identity_response(RESUME), "exp2.b3", GOOD)
    response = edit(
        response,
        "exp1.b4",
        "Delivered RESTful endpoints for AI-agent APIs, cutting manual lookups by 30\\%",
    )
    only_one = VerificationVerdict(id="exp2.b3", verdict="supported", evidence=GOOD_EVIDENCE)
    result, _ = run_verified(response, verifier=verdicts(only_one))
    assert GOOD in result.latex
    assert ORIGINAL["exp1.b4"] in result.latex
    assert any("exp1.b4" in w and "no verdict" in w for w in result.warnings)


@pytest.mark.parametrize("error", [GeminiRateLimitError("429"), GeminiTimeoutError("timeout")])
def test_verifier_failure_keeps_layer_a_rewrites_with_a_warning(error: Exception) -> None:
    result, fake = run_verified(
        edit(identity_response(RESUME), "exp2.b3", GOOD), verify_error=error
    )
    assert GOOD in result.latex
    assert any(w.startswith("Semantic verification unavailable") for w in result.warnings)
    assert result.content_check.verification == "unavailable"
    assert len(fake.verify_calls) == 1


def test_verifier_only_sees_bullets_that_passed_layer_a() -> None:
    response = edit(identity_response(RESUME), "exp2.b3", GOOD)
    response = edit(response, "exp1.b4", "Exposed REST endpoints, cutting lookups by 99\\%")
    result, fake = run_verified(response)
    assert [i.id for i in fake.verify_calls[0]] == ["exp2.b3"]
    sent_text = fake.last_resume_text
    assert "Sam Patel" not in sent_text and "555-0199" not in sent_text  # no contact header
    assert "\\usepackage" not in sent_text and "Improved query performance" in sent_text
    assert result.llm_requests == 2


def test_no_verifier_call_when_nothing_was_reworded() -> None:
    result, fake = run_pipeline(identity_response(RESUME))
    assert fake.verify_calls == []
    assert result.llm_requests == 1
    assert result.content_check.verification == "not_needed"


# --- reordering ---------------------------------------------------------------------------


def test_reordering_within_an_entry() -> None:
    response = edit(identity_response(RESUME), "exp1.b3", position=1)
    response = edit(response, "exp1.b1", position=4)
    result = apply(response)
    entry = parse_resume(result.latex).sections[0].content.entries[0]
    assert [b.latex for b in entry.bullets] == [
        ORIGINAL["exp1.b3"], ORIGINAL["exp1.b2"], ORIGINAL["exp1.b4"], ORIGINAL["exp1.b1"],
    ]  # fmt: skip
    assert "Reordered bullets in Quillpay (now b3, b2, b4, b1)" in result.changes


def test_out_of_range_position_is_ignored() -> None:
    result = apply(edit(identity_response(RESUME), "exp2.b2", position=7))
    assert result.latex == SOURCE
    assert result.changes == ["Left 9 bullets unchanged"]


# --- skills -------------------------------------------------------------------------------


def skills_response(latex: str) -> TailoringResponse:
    return identity_response(RESUME).model_copy(
        update={"skills_sections": [TailoredSection(section_id="s3", latex=latex)]}
    )


SKILLS_TEMPLATE = (
    "\\section{Technical Skills}\n \\begin{itemize}[leftmargin=0.15in, label={}]\n"
    "    \\small{\\item{\n     %s\n    }}\n \\end{itemize}"
)


def test_dropped_skills_are_restored() -> None:
    latex = SKILLS_TEMPLATE % (
        "\\textbf{Languages}{: Python, SQL} \\\\\n \\textbf{Tools}{: Airflow, Git}"
    )
    result, _ = run_pipeline(skills_response(latex))
    assert RESUME.segments[3].text in result.latex
    assert any(
        "Kept the original 'Technical Skills' section" in w and '"Docker"' in w
        for w in result.warnings
    )
    assert result.content_check.skills_after == result.content_check.skills_before == 7


def test_skills_added_only_if_the_resume_supports_them() -> None:
    # React is in a project; "Relational Databases" is supported by PostgreSQL.
    ok = SKILLS_TEMPLATE % (
        "\\textbf{Languages}{: Python, TypeScript, SQL} \\\\\n"
        " \\textbf{Frameworks \\& Tools}{: React, Airflow, Docker, Git, Vercel} \\\\\n"
        " \\textbf{Data}{: PostgreSQL, Relational Databases}"
    )
    accepted = apply(skills_response(ok))
    assert ok in accepted.latex
    items = skill_items(parse_resume(accepted.latex).segments[3].text)
    assert items is not None and len(items) == 10

    for bad in ("Kubernetes", "MongoDB"):
        rejected = apply(skills_response(ok.replace("Vercel}", f"Vercel, {bad}}}")))
        assert RESUME.segments[3].text in rejected.latex
        assert any(f'"{bad}"' in w for w in rejected.warnings)


# --- whole-resume invariants ----------------------------------------------------------------


def test_bullet_count_per_entry_is_unchanged_for_a_full_fixture() -> None:
    """A messy reply: rewrites, reorders, drops, merges, unknown ids, unsafe LaTeX."""
    resume = parse_resume(JAKES)
    units = extract_units(resume)
    ids = list(units.bullets)
    response = identity_response(resume)
    response = edit(response, ids[0], "Rewrote this bullet completely with no facts left")
    response = edit(response, ids[1], position=1)
    response = edit(response, ids[3], units.bullets[ids[3]].latex + " \\begin{itemize}")
    response = without(response, ids[2], ids[-1])
    response = response.model_copy(
        update={"bullets": response.bullets + [BulletRewrite(id="exp7.b1", text="New")]}
    )
    result, _ = run_pipeline(response, latex=JAKES)

    assert bullet_counts(result.latex) == bullet_counts(JAKES)
    assert all(e.before == e.after for e in result.content_check.entries)
    assert [(e.entry_id, e.before) for e in result.content_check.entries] == [
        ("exp1", 3), ("exp2", 2), ("exp3", 1), ("proj1", 2), ("proj2", 1),
    ]  # fmt: skip
    assert result.content_check.skills_after >= result.content_check.skills_before
    assert result.latex.startswith(resume.preamble)


def test_identity_reply_reproduces_the_input_exactly() -> None:
    for latex in (SOURCE, JAKES):
        resume = parse_resume(latex)
        result = apply_tailoring(resume, identity_response(resume))
        assert result.latex == latex
        assert result.warnings == []


def test_paragraph_summary_is_one_unit() -> None:
    resume = parse_resume((FIXTURES / "resumes" / "article_resume.tex").read_text())
    summary = extract_units(resume).entries[0]
    assert summary.key == "sum1" and len(summary.bullets) == 1
    original = summary.bullets[0].latex
    response = edit(identity_response(resume), "sum1.b1", original.replace("6+", "17+"))
    result = apply_tailoring(resume, response)
    assert original in result.latex
    assert any("sum1.b1" in w and '"17"' in w for w in result.warnings)


# --- scoring on the final resume, requirement matches, suggestions ---------------------------


def test_score_is_computed_on_the_final_post_revert_resume() -> None:
    # Kubernetes would count if the raw output were scored; it is reverted, so
    # the final score must not count it.
    raw = "Improved query performance of reporting jobs in \\textbf{PostgreSQL} on Kubernetes"
    result, _ = run_pipeline(edit(identity_response(RESUME), "exp2.b3", raw), jd=SMALL_JD)
    jd = analyze_jd(SMALL_JD)
    final = score_resume(parse_resume(result.latex), jd)
    assert result.programmatic_score.total == final.total
    statuses = {m.requirement: m.status for m in result.requirement_matches}
    assert statuses["Kubernetes"] == "missing"
    raw_resume = parse_resume(SOURCE.replace(ORIGINAL["exp2.b3"], raw))
    assert score_resume(raw_resume, jd).total > result.programmatic_score.total


def test_requirement_matches_in_the_api_shape() -> None:
    result, _ = run_pipeline(identity_response(RESUME), jd=SMALL_JD)
    by_name = {m.requirement: m for m in result.requirement_matches}
    assert by_name["Python"].status == "matched"
    assert by_name["Relational Databases"].status == "partially_matched"
    assert "PostgreSQL" in by_name["Relational Databases"].reason
    assert by_name["Relational Databases"].evidence == GOOD_EVIDENCE
    assert by_name["Kubernetes"].status == "missing" and by_name["Kubernetes"].evidence is None


def test_suggestions_split_partial_vs_missing() -> None:
    result, _ = run_pipeline(identity_response(RESUME), jd=SMALL_JD)
    surface = {s.requirement for s in result.suggestions.surface}
    missing = {s.requirement for s in result.suggestions.missing}
    assert "Relational Databases" in surface and "Kubernetes" in missing
    assert not surface & missing
    assert "Python" not in surface | missing  # matched: nothing to suggest
    kube = next(s for s in result.suggestions.missing if s.requirement == "Kubernetes")
    assert "will never add it" in kube.detail and "only if you have real experience" in kube.detail
    for s in result.suggestions.surface:
        assert "If that work involved" in s.detail or "If it really covered" in s.detail


# --- reporting -------------------------------------------------------------------------------


def test_changes_summary_has_reasons_and_revert_rules() -> None:
    response = edit(
        identity_response(RESUME),
        "exp1.b4",
        "Delivered RESTful endpoints for AI-agent APIs in \\textbf{Python}, cutting manual "
        "lookups by 30\\%",
        position=1,
        reason="Targets 'Design RESTful APIs'.",
    )
    response = edit(response, "exp2.b1", "Fine-tuned a DistilBERT classifier")
    response = without(response, "proj1.b2").model_copy(
        update={"notes": ["All original facts and details were preserved.", "Led with APIs."]}
    )
    result, _ = run_pipeline(response)
    assert result.changes_summary == [
        "Reworded exp1.b4 (Quillpay): Targets 'Design RESTful APIs'.",
        "Reordered bullets in Quillpay (now b4, b1, b2, b3)",
        'Reverted exp2.b1 (Northwind Labs) to the original (lost number: dropped "12" from the '
        "original bullet)",
        "Restored proj1.b2 (Carbon Lens): the model omitted it",
        "Left 6 bullets unchanged",
    ]
    assert result.model_notes == ["Led with APIs."]
    changes = {c.id: c for c in result.bullet_changes}
    assert changes["exp1.b4"].status == "reworded"
    assert changes["exp1.b4"].reason == "Targets 'Design RESTful APIs'."
    assert changes["exp1.b4"].after and changes["exp1.b4"].after.startswith("Delivered RESTful")
    assert changes["exp2.b1"].status == "reverted"
    assert changes["exp2.b1"].proposed == "Fine-tuned a DistilBERT classifier"
    assert changes["exp2.b2"].reason == "already aligned"  # unchanged: the model's reason


def test_provider_and_model_are_reported() -> None:
    result, _ = run_pipeline(identity_response(RESUME))
    assert result.provider == "Google Gemini"
    assert result.model == "test-model-1"


@pytest.mark.parametrize(
    ("note", "kept"),
    [
        ("All factual details preserved.", False),
        ("Kept every metric intact.", False),
        ("No new facts were invented.", False),
        ("Emphasized Python and REST experience.", True),
        ("Moved the API bullet first.", True),
    ],
)
def test_model_notes_filter(note: str, kept: bool) -> None:
    assert (filter_model_notes([note]) == [note]) is kept


def test_long_resume_gets_a_page_warning_and_nothing_is_cut() -> None:
    long_item = "\\resumeItem{" + "Wrote and reviewed internal tooling code " * 6 + "}\n"
    latex = SOURCE.replace(
        "\\resumeItem{Helped migrate nightly jobs to \\textbf{Airflow}}",
        "\\resumeItem{Helped migrate nightly jobs to \\textbf{Airflow}}\n" + long_item * 20,
    )
    assert estimate_lines(parse_resume(latex)) > PAGE_LINES
    result, _ = run_pipeline(identity_response(parse_resume(latex)), latex=latex)
    assert result.latex == latex
    assert any("may run past one page" in w and "Nothing was cut" in w for w in result.warnings)
    assert not any("one page" in w for w in run_pipeline(identity_response(RESUME))[0].warnings)


# --- practices (taxonomy "concept") are judged by the verifier, not Layer A -----------------

PRACTICE = (
    "Debugged TIFF-to-PNG conversion failures in the document upload pipeline through "
    "root-cause debugging and code review"
)


def test_unnamed_practice_passes_layer_a_and_goes_to_the_verifier() -> None:
    # "code review" is a practice the resume never names: not a tool, so Layer A
    # lets the verifier decide.
    result, fake = run_verified(edit(identity_response(RESUME), "exp1.b3", PRACTICE))
    assert [i.id for i in fake.verify_calls[0]] == ["exp1.b3"]
    assert PRACTICE in result.latex  # the fake verifier said "supported"


def test_unnamed_practice_is_reverted_when_the_verifier_rejects_it() -> None:
    v = VerificationVerdict(
        id="exp1.b3",
        verdict="unsupported",
        evidence="Debugged TIFF-to-PNG conversion failures",
        problem="The resume never mentions code review.",
    )
    result, _ = run_verified(
        edit(identity_response(RESUME), "exp1.b3", PRACTICE), verifier=verdicts(v)
    )
    assert ORIGINAL["exp1.b3"] in result.latex
    assert any("never mentions code review" in w for w in result.warnings)


def test_unnamed_practice_is_reverted_when_verification_is_unavailable() -> None:
    response = edit(identity_response(RESUME), "exp1.b3", PRACTICE)
    response = edit(response, "exp2.b3", GOOD)  # names nothing new: kept
    result, _ = run_verified(response, verify_error=GeminiTimeoutError("timeout"))
    assert ORIGINAL["exp1.b3"] in result.latex and PRACTICE not in result.latex
    assert GOOD in result.latex
    assert any('unverified practice: "Code Review"' in w for w in result.warnings)


# --- ENABLE_VERIFY_CALL off (the default) ----------------------------------------------------


def test_verify_call_is_off_by_default() -> None:
    from app.config import Settings

    assert Settings(_env_file=None).enable_verify_call is False


def test_disabled_verifier_makes_one_llm_call_and_keeps_layer_a_rewrites() -> None:
    result, fake = run_pipeline(edit(identity_response(RESUME), "exp2.b3", GOOD))
    assert fake.verify_calls == []
    assert result.llm_requests == 1
    assert GOOD in result.latex
    assert result.content_check.verification == "disabled"
    assert not any("Semantic verification unavailable" in w for w in result.warnings)


def test_disabled_verifier_still_runs_layer_a() -> None:
    rewrite = "Fine-tuned a DistilBERT classifier on support tickets"  # drops "12K"
    result, fake = run_pipeline(edit(identity_response(RESUME), "exp2.b1", rewrite))
    assert fake.verify_calls == []
    assert ORIGINAL["exp2.b1"] in result.latex
    assert any("lost number" in w for w in result.warnings)


def test_disabled_verifier_reverts_unnamed_practices() -> None:
    result, _ = run_pipeline(edit(identity_response(RESUME), "exp1.b3", PRACTICE))
    assert ORIGINAL["exp1.b3"] in result.latex
    assert any("ENABLE_VERIFY_CALL is off" in w and "Code Review" in w for w in result.warnings)


def test_verify_flag_is_part_of_the_cache_key() -> None:
    fake = FakeGemini(behaviour=lambda r, j: edit(identity_response(r), "exp2.b3", GOOD))
    on = TailorPipeline(client_factory=lambda: fake, model_name="m", enable_verify=True)
    off = TailorPipeline(client_factory=lambda: fake, model_name="m", enable_verify=False)
    off.cache = on.cache  # shared cache: must not serve one mode's result to the other
    assert asyncio.run(on.run(SOURCE, BACKEND_JD)).content_check.verification == "ran"
    assert asyncio.run(off.run(SOURCE, BACKEND_JD)).content_check.verification == "disabled"
