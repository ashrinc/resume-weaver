from pathlib import Path

import pytest

from app.jd.analyzer import analyze_jd
from app.jd.models import JDRequirements, SkillRequirement
from app.jd.taxonomy import get_taxonomy
from app.latex.parser import parse_resume
from app.scoring.models import ATSScore
from app.scoring.scorer import FUZZY_CREDIT, WEIGHTS, score_resume

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def load_resume(path: str):
    return parse_resume((FIXTURES / path).read_text())


def load_jd(name: str) -> JDRequirements:
    return analyze_jd((FIXTURES / "jds" / name).read_text())


def skill(name: str, weight: str = "high", occurrences: int = 1) -> SkillRequirement:
    info = get_taxonomy().skills[name]
    return SkillRequirement(
        name=name,
        category=info.category,
        type=info.type,
        weight=weight,
        matched_terms=[name],
        occurrences=occurrences,
    )


def jd(mandatory=(), preferred=(), responsibilities=()) -> JDRequirements:
    return JDRequirements(
        mandatory_skills=[skill(s) if isinstance(s, str) else s for s in mandatory],
        preferred_skills=[skill(s, "medium") if isinstance(s, str) else s for s in preferred],
        responsibilities=list(responsibilities),
    )


def resume(body: str, preamble_extra: str = ""):
    return parse_resume(
        "\\documentclass{article}\n" + preamble_extra + "\\begin{document}\n" + body
        + "\n\\end{document}\n"
    )  # fmt: skip


def pct(result: ATSScore, key: str) -> float | None:
    return result.criterion(key).percent


STANDARD_BODY = r"""
\section{Experience}
\subsection{Engineer, Acme \hfill 2020 -- Present}
\begin{itemize}
  \item Designed and operated payment services for thousands of businesses.
\end{itemize}
\section{Education}
\subsection{B.S. Computer Science, State University \hfill 2016 -- 2020}
\section{Skills}
Cooking, Gardening
"""


# --- end-to-end on fixtures ---------------------------------------------------------


def test_well_matched_pair_scores_above_90() -> None:
    result = score_resume(
        load_resume("scoring/backend_tailored.tex"), load_jd("backend_engineer.txt")
    )
    assert result.total > 90
    assert result.missing_keywords == []
    assert {m.skill for m in result.matched_mandatory} >= {"Go", "Python", "PostgreSQL", "AWS"}
    assert result.improvement_summary == ["Strong match: all extracted requirements are covered."]


def test_completely_mismatched_pair_scores_below_30() -> None:
    result = score_resume(load_resume("scoring/pastry_chef.tex"), load_jd("backend_engineer.txt"))
    assert result.total < 30
    assert result.matched_mandatory == [] and result.matched_preferred == []
    assert pct(result, "mandatory_skills") == 0.0


def test_partial_match_lands_in_between() -> None:
    result = score_resume(
        load_resume("resumes/article_resume.tex"), load_jd("backend_engineer.txt")
    )
    assert 30 < result.total < 90


def test_total_equals_sum_of_points_when_all_applicable() -> None:
    result = score_resume(load_resume("resumes/jakes_resume.tex"), load_jd("backend_engineer.txt"))
    assert all(c.applicable for c in result.criteria)
    assert [c.max_points for c in result.criteria] == list(WEIGHTS.values())
    assert result.total == pytest.approx(sum(c.points for c in result.criteria), abs=0.06)


def test_scoring_is_deterministic() -> None:
    args = (load_resume("resumes/jakes_resume.tex"), load_jd("ml_engineer.txt"))
    assert score_resume(*args) == score_resume(*args)


# --- matched / missing keywords ---------------------------------------------------------


def test_matched_keywords_listed_separately_and_missing_prioritised() -> None:
    result = score_resume(
        resume(r"\section{Skills}" "\nPython, Rust"),
        jd(mandatory=["Python", "Go"], preferred=["Rust", "Kubernetes"]),
    )
    assert [(m.skill, m.weight) for m in result.matched_mandatory] == [("Python", "high")]
    assert [(m.skill, m.weight) for m in result.matched_preferred] == [("Rust", "medium")]
    assert [(m.skill, m.priority) for m in result.missing_keywords] == [
        ("Go", "high"),
        ("Kubernetes", "medium"),
    ]


def test_improvement_summary_mentions_missing_mandatory_skills() -> None:
    result = score_resume(
        resume(STANDARD_BODY), jd(mandatory=["Go", "Kubernetes", "Python"], preferred=["Helm"])
    )
    first = result.improvement_summary[0]
    assert first.startswith("Add missing mandatory skills (high priority)")
    for name in ("Go", "Kubernetes", "Python"):
        assert name in first
    assert any("Helm" in line for line in result.improvement_summary[1:])


def test_summary_from_fixture_names_missing_mandatory() -> None:
    result = score_resume(load_resume("resumes/jakes_resume.tex"), load_jd("backend_engineer.txt"))
    missing_high = [m.skill for m in result.missing_keywords if m.priority == "high"]
    assert "Redis" in missing_high
    assert "Redis" in result.improvement_summary[0]


# --- fuzzy and alias matching ---------------------------------------------------------


@pytest.mark.parametrize(
    ("resume_text", "skill_name", "match_type"),
    [
        ("Python", "Python", "exact"),
        ("React.js", "React", "alias"),
        ("Postgres", "PostgreSQL", "alias"),
        ("k8s", "Kubernetes", "alias"),
        ("Amazon S3", "AWS", "alias"),  # implied
        ("Kubernets", "Kubernetes", "fuzzy"),  # typo
        ("Java17", "Java", "fuzzy"),  # version suffix (Python3 is a real alias)
        (
            "Built distributed, fault-tolerant systems",
            "Distributed Systems",
            "fuzzy",
        ),  # token level
    ],
)
def test_match_types(resume_text: str, skill_name: str, match_type: str) -> None:
    result = score_resume(
        resume(r"\section{Skills}" + "\n" + resume_text), jd(mandatory=[skill_name])
    )
    (match,) = result.matched_mandatory
    assert (match.skill, match.match_type) == (skill_name, match_type)
    expected = FUZZY_CREDIT if match_type == "fuzzy" else 1.0
    assert pct(result, "mandatory_skills") == pytest.approx(expected)


@pytest.mark.parametrize(
    ("resume_text", "skill_name"),
    [("JavaScript", "Java"), ("Reach", "React"), ("Scale", "Scala"), ("we go fast", "Go")],
)
def test_no_false_fuzzy_matches(resume_text: str, skill_name: str) -> None:
    result = score_resume(
        resume(r"\section{Skills}" + "\n" + resume_text), jd(mandatory=[skill_name])
    )
    assert result.matched_mandatory == []


# --- each criterion in isolation --------------------------------------------------------


def test_only_mandatory_skills_present() -> None:
    result = score_resume(
        resume(r"\section{Skills}" "\nPython, Go"),
        jd(
            mandatory=["Python", "Go"],
            preferred=["Rust"],
            responsibilities=["Design high-throughput payment services"],
        ),
    )
    assert pct(result, "mandatory_skills") == 1.0
    assert pct(result, "preferred_skills") == 0.0
    assert pct(result, "experience_alignment") == 0.0
    assert pct(result, "keyword_coverage") == pytest.approx(2 / 3, abs=1e-4)
    assert result.criterion("mandatory_skills").points == 35.0


def test_only_preferred_skills_present() -> None:
    result = score_resume(
        resume(r"\section{Skills}" "\nRust, Helm"),
        jd(mandatory=["Python", "Go"], preferred=["Rust", "Helm"]),
    )
    assert pct(result, "mandatory_skills") == 0.0
    assert pct(result, "preferred_skills") == 1.0
    assert result.criterion("preferred_skills").points == 15.0


def test_only_experience_alignment() -> None:
    result = score_resume(
        resume(STANDARD_BODY),
        jd(
            mandatory=["Haskell"],
            responsibilities=["Design and operate payment services for thousands of businesses"],
        ),
    )
    assert pct(result, "experience_alignment") == 1.0
    assert pct(result, "mandatory_skills") == 0.0
    assert pct(result, "keyword_coverage") == 0.0


def test_alignment_is_partial_for_loosely_related_bullets() -> None:
    result = score_resume(
        resume(STANDARD_BODY),
        jd(
            responsibilities=[
                "Design and operate payment services for thousands of businesses",
                "Train recommendation models with PyTorch",
            ]
        ),
    )
    assert pct(result, "experience_alignment") == pytest.approx(0.5, abs=0.1)
    assert any("PyTorch" in line for line in result.improvement_summary)


def test_alignment_canonicalises_skill_aliases() -> None:
    # 'k8s' in the resume and 'Kubernetes' in the JD are the same TF-IDF term.
    body = STANDARD_BODY.replace(
        "Designed and operated payment services for thousands of businesses.",
        "Ran k8s clusters for Postgres workloads.",
    )
    result = score_resume(
        resume(body), jd(responsibilities=["Operate Kubernetes clusters for PostgreSQL workloads"])
    )
    assert pct(result, "experience_alignment") == 1.0


def test_keyword_coverage_is_frequency_weighted() -> None:
    result = score_resume(
        resume(r"\section{Skills}" "\nPython"),
        jd(mandatory=[skill("Python", occurrences=3), skill("Go", occurrences=1)]),
    )
    assert pct(result, "mandatory_skills") == 0.5
    assert pct(result, "keyword_coverage") == 0.75


def test_structure_full_marks_for_standard_resume() -> None:
    result = score_resume(resume(STANDARD_BODY), jd(mandatory=["Python"]))
    assert pct(result, "structure") == 1.0
    assert result.structure_issues == []


def test_structure_flags_complex_formatting() -> None:
    body = (
        STANDARD_BODY
        + r"""
\begin{multicols}{2}
\includegraphics{photo.png} \faPhone\ 555-0100
\end{multicols}
"""
    )
    result = score_resume(
        resume(body, preamble_extra="\\usepackage{multicol}\n\\usepackage{fontawesome5}\n"),
        jd(mandatory=["Python"]),
    )
    assert pct(result, "structure") == pytest.approx(0.7)  # 4 headings + 0 formatting + 3 bullets
    (issue,) = result.structure_issues
    assert "graphics" in issue and "multi-column" in issue and "icon" in issue


def test_structure_flags_missing_headings_and_bullets() -> None:
    body = r"""
\section{Experience}
\subsection{Engineer, Acme \hfill 2020 -- Present}
Did things.
\subsection{Intern, Beta \hfill 2019}
\begin{itemize}\item Helped.\end{itemize}
"""
    result = score_resume(resume(body), jd(mandatory=["Python"]))
    # experience heading 1.5 + formatting 3 + bullets 3 * 1/2 = 6.0 of 10
    assert pct(result, "structure") == pytest.approx(0.6)
    assert "Add a standard 'Skills' section heading." in result.structure_issues
    assert "Add a standard 'Education' section heading." in result.structure_issues
    assert any("1 of 2 have none" in issue for issue in result.structure_issues)


def test_preamble_tables_are_not_penalised_but_body_tables_are() -> None:
    preamble = "\\newcommand{\\row}[2]{\\begin{tabular*}{\\textwidth}{lr}#1 & #2\\end{tabular*}}\n"
    clean = score_resume(resume(STANDARD_BODY, preamble), jd(mandatory=["Python"]))
    assert pct(clean, "structure") == 1.0
    table = STANDARD_BODY + "\n\\begin{tabular}{ll} a & b \\end{tabular}"
    flagged = score_resume(resume(table), jd(mandatory=["Python"]))
    assert "tables in the resume body" in flagged.structure_issues[0]


# --- edge cases ----------------------------------------------------------------------


def test_empty_resume() -> None:
    result = score_resume(parse_resume(""), load_jd("backend_engineer.txt"))
    assert result.total == 0.0
    assert result.matched_mandatory == []
    assert len([m for m in result.missing_keywords if m.priority == "high"]) == 22
    assert "Resume is empty or has no document body." in result.warnings
    assert result.improvement_summary[0].startswith("Add missing mandatory skills")


def test_empty_jd_scores_structure_only() -> None:
    result = score_resume(load_resume("resumes/jakes_resume.tex"), analyze_jd(""))
    assert [c.applicable for c in result.criteria] == [False, False, False, False, True]
    assert result.total == pytest.approx(100 * pct(result, "structure"))
    assert result.missing_keywords == []
    assert (
        "The JD has no extractable requirements; the score reflects resume structure only."
        in result.warnings
    )


def test_both_empty() -> None:
    result = score_resume(parse_resume(""), analyze_jd(""))
    assert result.total == 0.0


def test_jd_with_no_mandatory_skills_rescales_weights() -> None:
    result = score_resume(
        resume(STANDARD_BODY.replace("Cooking, Gardening", "Rust")),
        jd(
            preferred=["Rust", "Helm"],
            responsibilities=["Design and operate payment services for thousands of businesses"],
        ),
    )
    mandatory = result.criterion("mandatory_skills")
    assert (mandatory.applicable, mandatory.percent, mandatory.points) == (False, None, 0.0)
    applicable = [c for c in result.criteria if c.applicable]
    expected = 100 * sum(c.points for c in applicable) / sum(c.max_points for c in applicable)
    assert result.total == pytest.approx(expected, abs=0.06)
    assert any("Not applicable (weights rescaled)" in w for w in result.warnings)
    assert all(m.priority == "medium" for m in result.missing_keywords)


def test_unparsed_section_is_still_scored_as_text() -> None:
    result = score_resume(load_resume("resumes/malformed_resume.tex"), jd(mandatory=["Firebase"]))
    assert [m.skill for m in result.matched_mandatory] == ["Firebase"]
    assert any("could not be parsed" in w for w in result.warnings)


def test_total_always_within_bounds() -> None:
    for resume_file in ("scoring/backend_tailored.tex", "scoring/pastry_chef.tex"):
        for jd_file in ("backend_engineer.txt", "frontend_engineer.txt", "messy.txt"):
            result = score_resume(load_resume(resume_file), load_jd(jd_file))
            assert 0.0 <= result.total <= 100.0
            for c in result.criteria:
                assert 0.0 <= c.points <= c.max_points
