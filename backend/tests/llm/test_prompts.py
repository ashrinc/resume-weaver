import json
import re
from pathlib import Path

import pytest

from app.jd.analyzer import analyze_jd
from app.latex.parser import parse_resume
from app.latex.units import extract_units
from app.llm.prompts import (
    MAX_REPAIR_ECHO_CHARS,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    VERIFY_PROMPT_VERSION,
    VerifyItem,
    build_repair_prompt,
    build_tailoring_prompt,
    build_verify_prompt,
    keyword_priority,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
JAKES = (FIXTURES / "resumes" / "jakes_resume.tex").read_text()
BACKEND_JD = analyze_jd((FIXTURES / "jds" / "backend_engineer.txt").read_text())


NOT_IN_RESUME = "not_in_resume (JD skills the resume does not mention: never add)"


def block(prompt: str, heading: str):
    """Decode the JSON block that follows '## <heading>' in the prompt."""
    m = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", prompt, re.S | re.M)
    assert m, heading
    return json.loads(m.group(1))


@pytest.fixture(scope="module")
def prompt():
    return build_tailoring_prompt(parse_resume(JAKES), BACKEND_JD)


def test_system_prompt() -> None:
    assert "make this resume a stronger, clearer match" in SYSTEM_PROMPT
    assert "never what they claim" in SYSTEM_PROMPT
    assert "Return only JSON" in SYSTEM_PROMPT
    assert PROMPT_VERSION == "tailor-v4"
    assert VERIFY_PROMPT_VERSION == "verify-v1"


def test_prompt_contains_every_bullet_with_its_id(prompt) -> None:
    units = extract_units(parse_resume(JAKES))
    entries = block(prompt.user, "Resume entries and their bullets")
    sent = [(b["id"], b["latex"]) for e in entries for b in e["bullets"]]
    assert sent == [(b.id, b.latex) for e in units.entries for b in e.bullets]
    assert [e["entry_id"] for e in entries] == ["exp1", "exp2", "exp3", "proj1", "proj2"]
    assert entries[0]["heading"].startswith("Software Engineer II | Brightline Logistics")
    assert prompt.bullet_ids == [i for i, _ in sent]
    assert sent[0][0] == "exp1.b1"
    assert sent[0][1].startswith("Built a real-time shipment tracking service in \\textbf{Go}")


def test_skills_sections_are_sent_whole(prompt) -> None:
    resume = parse_resume(JAKES)
    skills = block(prompt.user, "Skills sections")
    assert skills == [
        {"id": "s4", "title": "Technical Skills", "latex": resume.segments[4].text.strip()}
    ]
    assert prompt.skills_section_ids == ["s4"]


def test_headings_and_education_without_bullets_are_not_editable(prompt) -> None:
    entries = block(prompt.user, "Resume entries and their bullets")
    assert all(not e["entry_id"].startswith("edu") for e in entries)
    assert "\\resumeSubheading" not in json.dumps(entries)


def test_prompt_excludes_preamble_and_raw_header(prompt) -> None:
    preamble = parse_resume(JAKES).preamble
    for marker in ("latexsym", "glyphtounicode", "\\titleformat", "\\newcommand{\\resumeItem}",
                   "\\pdfgentounicode", "\\addtolength"):  # fmt: skip
        assert marker in preamble
        assert marker not in prompt.user
        assert marker not in prompt.system
    # The raw header (name / contact block) is passed through, not sent.
    assert "Jordan Rivera" not in prompt.user
    assert "512-555-0147" not in prompt.user


def test_prompt_contains_jd_requirements_with_weights(prompt) -> None:
    job = block(prompt.user, "Job requirements (with responsibilities)")
    assert job["title"] == "Senior Backend Engineer — Payments Platform"
    assert job["seniority"] == "senior"
    assert job["years_of_experience"] == {"min": 5, "max": None}
    by_name = {s["name"]: s for g in ("mandatory_skills", "preferred_skills", "soft_skills")
               for s in job[g]}  # fmt: skip
    assert by_name["Go"]["weight"] == "high"
    assert by_name["Helm"]["weight"] == "medium"
    assert by_name["Ownership"]["weight"] == "low"
    # The JD's own wording travels with each skill (e.g. "code reviews").
    assert by_name["Code Review"]["jd_wording"] == ["code reviews"]
    assert by_name["Ownership"]["jd_wording"] == ["sense of ownership"]
    assert job["responsibilities"] == BACKEND_JD.responsibilities
    assert job["certifications"] == [
        {"name": "AWS Certified Solutions Architect", "weight": "medium"}
    ]


def test_keyword_priority_list(prompt) -> None:
    keywords = block(prompt.user, "Keyword priority list (most important first)")
    assert keywords == keyword_priority(BACKEND_JD)
    mandatory = {s.name for s in BACKEND_JD.mandatory_skills}
    preferred = {s.name for s in BACKEND_JD.preferred_skills}
    n_mand = len(mandatory)
    assert set(keywords[:n_mand]) == mandatory
    assert set(keywords[n_mand : n_mand + len(preferred)]) == preferred
    assert keywords[-3:] == ["Mentoring", "Communication", "Ownership"]


def test_keyword_priority_orders_by_mentions_within_group() -> None:
    jd = analyze_jd("Requirements\n- Go\n- Python, Python and more Python\n- Go")
    assert keyword_priority(jd)[:2] == ["Python", "Go"]


def test_keyword_override() -> None:
    p = build_tailoring_prompt(parse_resume(JAKES), BACKEND_JD, keywords=["Redis", "Go"])
    assert block(p.user, "Keyword priority list (most important first)") == ["Redis", "Go"]


def test_allowed_latex_lists_commands_used_in_sections(prompt) -> None:
    allowed = block(prompt.user, "Allowed LaTeX")
    for cmd in ("\\section", "\\textbf", "\\item"):  # from bullets and the skills section
        assert cmd in allowed["commands"]
    assert "\\resumeSubheading" not in allowed["commands"]  # headings are not sent
    assert "itemize" in allowed["environments"]
    assert "\\newcommand" not in allowed["commands"]  # only defined in the preamble
    assert "\\%" in allowed["special_characters"]


@pytest.mark.parametrize(
    "instruction",
    [
        "## Goal\nImprove the bullets that are relevant to this job",
        "strong, accurate action verb",
        "technical implementation first, then the outcome or metric",
        '"RESTful APIs"',
        "keeps every metric exactly as written",
        "do not play it safe",
        'say which in its "reason"',
        "reorder bullets within an entry",
        "## Hard limits",
        "Never delete a bullet or merge two bullets",
        "No new numbers or metrics",
        "No tools, technologies or platforms the resume does not mention",
        '"Vercel and Render" must not become',
        '"prevent" is not "eliminate"',
        '"some" is not "all"',
        '"contributed to" or "assisted" is not',
        "never drop a skill",
        'PostgreSQL for "relational databases"',
        'Nothing from "not_in_resume" may be added anywhere',
        'write "40\\%", not "40%"',
        '"changed": true or false',
        '"reason": one short sentence',
        '"notes"',
        "Do not claim facts were",
    ],
)
def test_prompt_contains_required_instructions(prompt, instruction: str) -> None:
    assert instruction in prompt.user


def test_json_backslash_instruction_is_rendered_correctly(prompt) -> None:
    assert 'written as "\\\\textbf"' in prompt.user  # i.e. the text "\\textbf"


def test_failed_sections_are_not_sent() -> None:
    p = build_tailoring_prompt(
        parse_resume((FIXTURES / "resumes" / "malformed_resume.tex").read_text()), BACKEND_JD
    )
    sections = {e["section"] for e in block(p.user, "Resume entries and their bullets")}
    assert sections == {"Experience"}  # Projects failed to parse
    assert [s["title"] for s in block(p.user, "Skills sections")] == ["Skills"]
    assert "Tracker app" not in p.user


def test_dollar_signs_in_resume_do_not_break_templating() -> None:
    src = (
        "\\documentclass{article}\\begin{document}\n"
        "\\section{Skills}\nSaved \\$5M $|$ $x$\n\\end{document}"
    )
    p = build_tailoring_prompt(parse_resume(src), BACKEND_JD)
    assert "Saved \\\\$5M $|$ $x$" in p.user  # JSON-escaped backslash, dollars intact


def test_empty_resume_and_jd_still_build() -> None:
    p = build_tailoring_prompt(parse_resume(""), analyze_jd(""))
    assert block(p.user, "Resume entries and their bullets") == []
    assert p.bullet_ids == [] and p.skills_section_ids == []


def test_repair_prompt() -> None:
    repair = build_repair_prompt('{"sections": [', "invalid JSON (Expecting value at line 1)")
    assert "Problem: invalid JSON (Expecting value at line 1)" in repair
    assert '{"sections": [' in repair
    assert '"bullets", "skills_sections", "ats_analysis" and "notes"' in repair
    assert "every bullet id exactly once" in repair
    long = build_repair_prompt("x" * (MAX_REPAIR_ECHO_CHARS + 500), "too long")
    assert long.endswith("...[truncated]\n")
    assert len(long) < MAX_REPAIR_ECHO_CHARS + 2_000


def test_not_in_resume_block() -> None:
    p = build_tailoring_prompt(parse_resume(JAKES), BACKEND_JD, missing_keywords=["Celery", "Helm"])
    assert block(p.user, NOT_IN_RESUME) == [
        "Celery",
        "Helm",
    ]
    empty = build_tailoring_prompt(parse_resume(JAKES), BACKEND_JD)
    assert block(empty.user, NOT_IN_RESUME) == []


def test_goal_comes_before_constraints(prompt) -> None:
    assert prompt.user.index("## Goal") < prompt.user.index("## Hard limits")
    assert prompt.user.count("\n1. ") == 1  # one short numbered list


def test_matching_plan_is_sent_partial_first() -> None:
    from app.scoring.scorer import score_resume

    resume = parse_resume(JAKES)
    plan = score_resume(resume, BACKEND_JD).requirement_matches
    p = build_tailoring_prompt(resume, BACKEND_JD, plan=plan)
    sent = block(p.user, "Matching plan (requirement -> supporting bullets)")
    assert len(sent) == len(plan)
    statuses = [item["status"] for item in sent]
    assert statuses == sorted(
        statuses, key={"partially_matched": 0, "matched": 1, "missing": 2}.__getitem__
    )
    assert any(item["target_bullets"] for item in sent if item["status"] != "missing")
    assert all(item["target_bullets"] == [] for item in sent if item["status"] == "missing")
    responsibilities = block(p.user, "Job requirements (with responsibilities)")["responsibilities"]
    assert responsibilities == BACKEND_JD.responsibilities


def test_verify_prompt() -> None:
    items = [VerifyItem(id="exp1.b1", original="Tuned slow queries", rewrite="Optimized queries")]
    system, user = build_verify_prompt("Experience Tuned slow queries", items)
    assert "strict fact checker" in system
    assert "EXACT quote" in user
    assert '"wording_only"' in user
    assert "Experience Tuned slow queries" in user
    assert block(user, "Items to check") == [
        {"id": "exp1.b1", "original": "Tuned slow queries", "rewrite": "Optimized queries"}
    ]
