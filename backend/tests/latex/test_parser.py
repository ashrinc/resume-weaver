import time
from pathlib import Path

import pytest

from app.latex.models import ParsedResume
from app.latex.parser import (
    classify_section,
    extract_metrics,
    latex_to_plain,
    mask_latex,
    parse_dates,
    parse_resume,
    reconstruct,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "resumes"
FIXTURE_FILES = sorted(FIXTURES.glob("*.tex"))


def load(name: str) -> str:
    return (FIXTURES / name).read_text()


def section_map(parsed: ParsedResume) -> list[tuple[str | None, str | None]]:
    return [(s.name, s.section_type) for s in parsed.sections]


def minimal(body: str) -> str:
    return "\\documentclass{article}\n\\begin{document}\n" + body + "\n\\end{document}\n"


def assert_segments_cover(src: str, parsed: ParsedResume) -> None:
    pos = len(parsed.preamble)
    for seg in parsed.segments:
        assert seg.start == pos, f"gap or overlap before segment {seg.name!r}"
        assert seg.text == src[seg.start : seg.end]
        pos = seg.end
    assert pos == len(src)


# --- round trip -----------------------------------------------------------------


def test_all_fixtures_present() -> None:
    assert {f.name for f in FIXTURE_FILES} == {
        "jakes_resume.tex",
        "article_resume.tex",
        "moderncv_resume.tex",
        "malformed_resume.tex",
        "preservation_resume.tex",
    }


@pytest.mark.parametrize("path", FIXTURE_FILES, ids=lambda p: p.stem)
def test_round_trip_is_exact(path: Path) -> None:
    src = path.read_text()
    parsed = parse_resume(src)
    assert reconstruct(parsed) == src
    assert_segments_cover(src, parsed)


@pytest.mark.parametrize("path", FIXTURE_FILES, ids=lambda p: p.stem)
def test_round_trip_with_crlf_line_endings(path: Path) -> None:
    src = path.read_text().replace("\n", "\r\n")
    parsed = parse_resume(src)
    assert reconstruct(parsed) == src
    assert_segments_cover(src, parsed)


@pytest.mark.parametrize(
    "src",
    ["", "no latex at all", "\\begin{document}", "\\begin{document}\\section{A}x", "}}}{{{"],
)
def test_round_trip_on_degenerate_input(src: str) -> None:
    assert reconstruct(parse_resume(src)) == src


def test_preamble_ends_at_begin_document() -> None:
    src = load("jakes_resume.tex")
    parsed = parse_resume(src)
    assert parsed.preamble.endswith("\\begin{document}")
    assert parsed.has_document_body
    # \titleformat{\section} in the preamble must not create a segment.
    assert parsed.segments[0].start == len(parsed.preamble)


def test_header_and_end_document_are_raw() -> None:
    parsed = parse_resume(load("jakes_resume.tex"))
    assert (parsed.segments[0].kind, parsed.segments[0].name) == ("raw", "header")
    assert "Jordan Rivera" in parsed.segments[0].text
    assert (parsed.segments[-1].kind, parsed.segments[-1].name) == ("raw", "end_document")
    assert "\\end{document}" in parsed.segments[-1].text


# --- section mapping -----------------------------------------------------------


def test_section_mapping_jakes() -> None:
    assert section_map(parse_resume(load("jakes_resume.tex"))) == [
        ("Education", "education"),
        ("Experience", "experience"),
        ("Projects", "projects"),
        ("Technical Skills", "skills"),
    ]


def test_section_mapping_article_with_starred_sections() -> None:
    assert section_map(parse_resume(load("article_resume.tex"))) == [
        ("Professional Summary", "summary"),
        ("Work Experience", "experience"),
        ("Education", "education"),
        ("Skills", "skills"),
        ("Certifications", "other"),
    ]


def test_section_mapping_moderncv() -> None:
    assert section_map(parse_resume(load("moderncv_resume.tex"))) == [
        ("Profile", "summary"),
        ("Experience", "experience"),
        ("Education", "education"),
        ("Skills", "skills"),
        ("Certifications", "other"),
    ]


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Professional Experience", "experience"),
        ("Employment History", "experience"),
        ("Technical Projects", "projects"),
        ("Education & Coursework", "education"),
        ("Technical Skills", "skills"),
        ("Tools and Technologies", "skills"),
        ("Career Objective", "summary"),
        ("Career Summary", "summary"),
        ("Career History", "experience"),
        ("Objective", "summary"),
        ("About Me", "summary"),
        ("Awards", "other"),
        ("Certifications", "other"),
    ],
)
def test_classify_section(title: str, expected: str) -> None:
    assert classify_section(title) == expected


# --- entries, dates, bullets -----------------------------------------------------


def test_jakes_experience_entries_and_bullets() -> None:
    (exp,) = parse_resume(load("jakes_resume.tex")).sections_of("experience")
    entries = exp.content.entries
    assert [(e.title, e.organization, e.start_date, e.end_date) for e in entries] == [
        ("Software Engineer II", "Brightline Logistics", "June 2023", "Present"),
        # \resumeSubSubheading inherits the company from the previous entry.
        ("Software Engineer I", "Brightline Logistics", "July 2021", "May 2023"),
        ("Software Engineering Intern", "Pinecrest Fintech", "May 2020", "Aug. 2020"),
    ]
    assert entries[0].location == "Austin, TX"
    assert [len(e.bullets) for e in entries] == [3, 2, 1]

    bullet = entries[0].bullets[1]
    assert bullet.text == (
        "Reduced cloud spend by 32% ($180K/yr) by right-sizing Kubernetes workloads"
    )
    assert bullet.metrics == ["32%", "$180K"]
    assert bullet.latex.startswith("Reduced cloud spend by 32\\%")


def test_bullet_offsets_point_at_original_latex() -> None:
    src = load("jakes_resume.tex")
    parsed = parse_resume(src)
    for seg in parsed.sections:
        for b in seg.content.all_bullets:
            assert src[b.start : b.end] == b.latex
            assert seg.start <= b.start < b.end <= seg.end


def test_jakes_education_layout_puts_dates_bottom_right() -> None:
    (edu,) = parse_resume(load("jakes_resume.tex")).sections_of("education")
    (entry,) = edu.content.entries
    assert entry.organization == "University of Texas at Austin"
    assert entry.title == "Bachelor of Science in Computer Science, Minor in Mathematics"
    assert entry.location == "Austin, TX"
    assert (entry.start_date, entry.end_date) == ("Aug. 2017", "May 2021")


def test_jakes_project_heading_splits_name_and_tech_stack() -> None:
    (proj,) = parse_resume(load("jakes_resume.tex")).sections_of("projects")
    first = proj.content.entries[0]
    assert first.title == "RouteWise"
    assert first.technologies == ["TypeScript", "React", "Node.js", "Mapbox"]
    assert first.start_date == "Jan. 2022"


def test_article_subsection_entries_and_item_bullets() -> None:
    parsed = parse_resume(load("article_resume.tex"))
    (exp,) = parsed.sections_of("experience")
    first, second = exp.content.entries
    assert (first.title, first.organization) == ("Senior Software Engineer", "Cascade Payments")
    assert (first.start_date, first.end_date) == ("Mar 2021", "Present")
    assert (second.start_date, second.end_date) == ("Jul 2018", "Feb 2021")
    assert [b.text for b in first.bullets][1] == (
        "Cut p99 API latency by 45% by introducing Redis caching and query batching."
    )
    assert len(second.bullets) == 2

    (edu,) = parsed.sections_of("education")
    assert edu.content.entries[0].title == "B.S. in Computer Science"
    assert edu.content.entries[0].organization == "University of Washington"


def test_moderncv_cventry_with_nested_itemize() -> None:
    (exp,) = parse_resume(load("moderncv_resume.tex")).sections_of("experience")
    first = exp.content.entries[0]
    assert (first.title, first.organization, first.location) == (
        "Senior Data Engineer",
        "Northwind Analytics",
        "Chicago, IL",
    )
    assert (first.start_date, first.end_date) == ("2021", "Present")
    assert [b.metrics for b in first.bullets] == [["40+", "70%"], ["1.5B"]]


def test_summary_text_extracted() -> None:
    (summary,) = parse_resume(load("moderncv_resume.tex")).sections_of("summary")
    assert summary.content.text.startswith("Data engineer with 5 years")


def test_achievements_are_quantified_bullets() -> None:
    parsed = parse_resume(load("jakes_resume.tex"))
    texts = [b.text for b in parsed.achievements]
    assert "Developed REST APIs with FastAPI and PostgreSQL for the carrier portal" not in texts
    assert all(b.metrics for b in parsed.achievements)
    assert len(texts) == 8


# --- skills and certifications ---------------------------------------------------------


def test_jakes_skills_groups() -> None:
    parsed = parse_resume(load("jakes_resume.tex"))
    (skills,) = parsed.sections_of("skills")
    groups = {g.category: g.skills for g in skills.content.skill_groups}
    assert groups["Languages"] == ["Go", "Python", "TypeScript", "SQL", "C/C++"]
    assert list(groups) == ["Languages", "Frameworks", "Developer Tools", "Libraries"]
    assert "Kubernetes" in parsed.skills


def test_skills_keep_parenthesized_lists_together() -> None:
    parsed = parse_resume(load("article_resume.tex"))
    assert "AWS (ECS, Lambda, RDS)" in parsed.skills
    assert "Lambda" not in parsed.skills


def test_moderncv_cvitem_skills() -> None:
    parsed = parse_resume(load("moderncv_resume.tex"))
    (skills,) = parsed.sections_of("skills")
    assert [g.category for g in skills.content.skill_groups] == ["Languages", "Big Data", "Cloud"]
    assert parsed.skills[:3] == ["Python", "SQL", "Scala"]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (
            "article_resume.tex",
            [
                "AWS Certified Solutions Architect – Associate (2022)",
                "Certified Kubernetes Application Developer (CKAD)",
            ],
        ),
        (
            "moderncv_resume.tex",
            [
                "AWS Certified Data Analytics – Specialty",
                "Databricks Certified Data Engineer Associate",
            ],
        ),
    ],
)
def test_certifications(name: str, expected: list[str]) -> None:
    assert parse_resume(load(name)).certifications == expected


def test_certifications_inside_skills_section() -> None:
    src = minimal("\\section{Skills}\nLanguages: Go, Rust \\\\\nCertifications: CKA, CKAD")
    parsed = parse_resume(src)
    assert parsed.skills == ["Go", "Rust"]
    assert parsed.certifications == ["CKA", "CKAD"]


# --- malformed input -> raw fallback ----------------------------------------------------


def test_malformed_fixture_section_falls_back_to_raw() -> None:
    src = load("malformed_resume.tex")
    parsed = parse_resume(src)
    assert reconstruct(parsed) == src

    projects = next(s for s in parsed.segments if s.name == "Projects")
    assert projects.kind == "raw"
    assert projects.content is None
    assert "unbalanced braces" in projects.error
    assert projects.text == src[projects.start : projects.end]

    # The broken section must not take its neighbours down with it.
    assert section_map(parsed) == [("Experience", "experience"), ("Skills", "skills")]
    assert parsed.skills == ["Python", "React", "Firebase", "Dialogflow"]


def test_unclosed_section_title_falls_back_to_raw() -> None:
    src = minimal("\\section{Awards\nDean's list\n\\section{Skills}\nGo, Rust")
    parsed = parse_resume(src)
    assert reconstruct(parsed) == src
    header, awards = parsed.segments[:2]
    assert (header.name, header.text) == ("header", "\n")
    assert awards.kind == "raw"
    assert "title" in awards.error
    assert section_map(parsed) == [("Skills", "skills")]


def test_unreadable_heading_arguments_fall_back_to_raw() -> None:
    src = minimal("\\section{Experience}\n\\resumeSubheading{Acme}{2020 -- 2021}\n")
    parsed = parse_resume(src)
    experience = parsed.segments[1]  # segments[0] is the "\n" header
    assert (experience.kind, experience.name) == ("raw", "Experience")
    assert "arguments" in experience.error
    assert reconstruct(parsed) == src


def test_missing_begin_document_keeps_everything_raw() -> None:
    src = "\\documentclass{article}\n\\section{Experience}\nStuff\n"
    parsed = parse_resume(src)
    assert not parsed.has_document_body
    assert parsed.preamble == ""
    assert len(parsed.segments) == 1
    assert parsed.segments[0].kind == "raw"
    assert reconstruct(parsed) == src


# --- comments and escaped text ------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        "% \\section{Hidden}\ntext",
        "text \\\\section{Hidden}",  # \\ is a line break; 'section' is plain text
        "\\verb|\\section{Hidden}| text",
        "\\begin{verbatim}\n\\section{Hidden}\n\\end{verbatim}",
        "\\newcommand{\\sec}{\\section{Hidden}}",  # inside a group, not at line start
        "\\begin{itemize}\n\\item x \\section{Hidden}\n\\end{itemize}",  # inside an env
        "\\subsection{Not top level}",
    ],
    ids=["comment", "double-backslash", "verb", "verbatim-env", "macro-body", "inside-env", "sub"],
)
def test_non_section_commands_are_not_split(body: str) -> None:
    src = minimal("\\section{Real}\n" + body)
    parsed = parse_resume(src)
    assert [s.name for s in parsed.sections] == ["Real"]
    assert reconstruct(parsed) == src


def test_escaped_percent_is_not_a_comment() -> None:
    src = minimal("text 50\\% \\section{Second}")
    assert [s.name for s in parse_resume(src).sections] == ["Second"]


def test_mask_preserves_length_and_newlines() -> None:
    src = "a % c\n\\{ \\verb|x| \\section{S}\n"
    masked = mask_latex(src)
    assert len(masked) == len(src)
    assert [i for i, c in enumerate(masked) if c == "\n"] == [
        i for i, c in enumerate(src) if c == "\n"
    ]
    assert "%" not in masked and "verb" not in masked and "\\section" in masked


# --- helpers ---------------------------------------------------------------------


def test_extract_metrics() -> None:
    text = "Cut costs by $1.2M and latency 40% for 10K+ users, 3x faster since 2021 on EC2 and k8s"
    assert extract_metrics(text) == ["$1.2M", "40%", "10K+", "3x"]


@pytest.mark.parametrize(
    "text",
    [
        "Upgraded services to Python 3.11",
        "Migrated the API gateway to Node 18",
        "Rewrote the frontend in Angular 15 and Node.js 20",
        "Moved to Spring Boot 3 and Java 17",
        "Shipped version 2 of the billing SDK",
        "Ran (Postgres 15) on Kubernetes 1.28",
    ],
)
def test_version_numbers_are_not_metrics(text: str) -> None:
    assert extract_metrics(text) == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Led migration of 3 services to Python 3.11", ["3"]),
        ("Upgraded to Node 18, cutting cold starts by 30%", ["30%"]),
        ("Python 3 workers handled 40% more load", ["40%"]),
        ("Mentored 4 engineers", ["4"]),  # 'Mentored' is not a tech term
        ("Used Python, 3 teams adopted it", ["3"]),  # comma: not a version
        ("Scaled Kafka 10x", ["10x"]),  # suffix makes it a real metric
    ],
)
def test_versions_dropped_but_real_metrics_kept(text: str, expected: list[str]) -> None:
    assert extract_metrics(text) == expected


def test_python_version_bullet_in_fixture_has_no_version_metric() -> None:
    (exp,) = parse_resume(load("jakes_resume.tex")).sections_of("experience")
    bullet = exp.content.entries[0].bullets[2]
    assert bullet.text == "Led migration of 3 legacy services to Python 3.11 with zero downtime"
    assert bullet.metrics == ["3"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Aug. 2018 -- May 2021", ("Aug. 2018", "May 2021")),
        ("2021–Present", ("2021", "Present")),
        ("Summer 2020 to Fall 2020", ("Summer 2020", "Fall 2020")),
        ("06/2019 - 08/2019", ("06/2019", "08/2019")),
        ("Expected May 2026", (None, "May 2026")),
    ],
)
def test_parse_dates(text: str, expected: tuple[str | None, str | None]) -> None:
    found = parse_dates(text)
    assert found is not None
    assert (found.start, found.end) == expected


def test_parse_dates_none() -> None:
    assert parse_dates("Austin, TX") is None


def test_latex_to_plain() -> None:
    latex = (
        r"\textbf{Built} 3 APIs -- cut cost 20\% (\$5K) "
        r"\href{https://x.io}{\underline{x.io}} % note"
    )
    assert latex_to_plain(latex) == "Built 3 APIs – cut cost 20% ($5K) x.io"


# --- performance --------------------------------------------------------------------


def test_parse_time_under_two_seconds() -> None:
    src = load("jakes_resume.tex")
    start = src.index("\\section{Education}")
    end = src.index("\\end{document}")
    # ~10x a typical one-page resume body.
    big = src[:start] + src[start:end] * 10 + src[end:]
    t0 = time.perf_counter()
    parsed = parse_resume(big)
    elapsed = time.perf_counter() - t0
    assert reconstruct(parsed) == big
    assert len(parsed.sections) == 40
    assert elapsed < 2.0, f"parse took {elapsed:.3f}s"
