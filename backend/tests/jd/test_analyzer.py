from pathlib import Path

import pytest

from app.jd.analyzer import analyze_jd
from app.jd.models import JDRequirements

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "jds"


def analyze(name: str) -> JDRequirements:
    return analyze_jd((FIXTURES / name).read_text())


def weights(result: JDRequirements) -> dict[str, list[str]]:
    return {
        "high": [s.name for s in result.mandatory_skills],
        "medium": [s.name for s in result.preferred_skills],
        "low": [s.name for s in result.soft_skills],
    }


def test_all_four_fixtures_present() -> None:
    assert {f.name for f in FIXTURES.glob("*.txt")} == {
        "backend_engineer.txt",
        "frontend_engineer.txt",
        "ml_engineer.txt",
        "messy.txt",
    }


# --- exact skills and weights per fixture ---------------------------------------


def test_backend_skills_and_weights() -> None:
    assert weights(analyze("backend_engineer.txt")) == {
        "high": [
            "Go", "Python", "PostgreSQL", "Redis", "Database Design", "Query Optimization",
            "Event-Driven Architecture", "Apache Kafka", "REST APIs", "gRPC", "PCI DSS",
            "Code Review", "Backend Development", "AWS", "Amazon ECS", "Amazon RDS",
            "Amazon SQS", "Docker", "Terraform", "Distributed Systems", "Microservices",
            "API Design",
        ],
        # Kubernetes is also mentioned under Benefits, which is ignored.
        "medium": ["Kubernetes", "Helm", "Observability", "Datadog", "Prometheus"],
        "low": ["Mentoring", "Communication", "Ownership"],
    }  # fmt: skip


def test_frontend_skills_and_weights() -> None:
    assert weights(analyze("frontend_engineer.txt")) == {
        "high": [
            "React", "TypeScript", "Figma", "Storybook", "Design Systems", "Web Performance",
            "Next.js", "End-to-End Testing", "Jest", "Testing Library", "Playwright", "GraphQL",
            "JavaScript", "HTML", "CSS", "State Management", "Redux", "Zustand", "Accessibility",
            "Responsive Design", "Git", "Code Review", "CI/CD",
        ],
        "medium": ["Tailwind CSS", "Styled Components", "React Native"],
        "low": ["Collaboration", "Attention to Detail", "Communication"],
    }  # fmt: skip


def test_ml_skills_and_weights() -> None:
    assert weights(analyze("ml_engineer.txt")) == {
        "high": [
            "Machine Learning", "PyTorch", "TensorFlow", "PySpark", "Apache Airflow",
            "Databricks", "Data Lakes", "A/B Testing", "MLflow", "Kubernetes", "Statistics",
            "Python", "SQL", "pandas", "NumPy", "scikit-learn", "Deep Learning",
            "Recommendation Systems",
        ],
        "medium": [
            "Large Language Models", "Retrieval-Augmented Generation", "Feature Stores",
            "Amazon SageMaker", "AWS", "GCP",
        ],
        "low": ["Communication", "Stakeholder Management"],
    }  # fmt: skip


def test_messy_skills_and_weights() -> None:
    assert weights(analyze("messy.txt")) == {
        "high": [
            "Full-Stack Development", "Node.js", "React", "JavaScript", "PostgreSQL",
            "MongoDB", "REST APIs",
        ],
        "medium": ["AWS", "Docker", "Kubernetes", "TypeScript", "GraphQL"],
        "low": ["Communication", "Self-Motivation", "Teamwork"],
    }  # fmt: skip


def test_weight_values_match_buckets() -> None:
    result = analyze("backend_engineer.txt")
    assert {s.weight for s in result.mandatory_skills} == {"high"}
    assert {s.weight for s in result.preferred_skills} == {"medium"}
    assert {s.weight for s in result.soft_skills} == {"low"}
    assert {s.type for s in result.soft_skills} == {"soft"}
    assert result.keyword_weights["Go"] == "high"
    assert result.keyword_weights["Helm"] == "medium"
    assert result.keyword_weights["Ownership"] == "low"


def test_matched_terms_record_aliases() -> None:
    result = analyze("messy.txt")
    by_name = {s.name: s for s in result.all_skills}
    assert by_name["Kubernetes"].matched_terms == ["k8s"]
    assert by_name["PostgreSQL"].matched_terms == ["postgres"]
    assert by_name["Node.js"].matched_terms == ["node", "node.js"]
    assert by_name["Node.js"].occurrences == 2


# --- other extracted fields ----------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "title", "seniority", "source", "years"),
    [
        (
            "backend_engineer.txt",
            "Senior Backend Engineer — Payments Platform",
            "senior",
            "title",
            (5, None),
        ),
        ("frontend_engineer.txt", "Frontend Engineer (React)", "mid", "years", (3, None)),
        ("ml_engineer.txt", "MACHINE LEARNING ENGINEER, RECOMMENDATIONS", "mid", "years", (4, 7)),
        ("messy.txt", None, "mid", "years", (2, None)),
    ],
)
def test_title_seniority_and_years(name, title, seniority, source, years) -> None:
    result = analyze(name)
    assert result.title == title
    assert (result.seniority, result.seniority_source) == (seniority, source)
    assert (result.experience.min_years, result.experience.max_years) == years


@pytest.mark.parametrize(
    ("name", "sections"),
    [
        ("backend_engineer.txt", ["responsibilities", "requirements", "preferred", "other"]),
        ("frontend_engineer.txt", ["responsibilities", "requirements", "preferred", "other"]),
        ("ml_engineer.txt", ["responsibilities", "requirements", "preferred"]),
        ("messy.txt", ["requirements", "preferred", "other"]),  # inline "must have:" etc.
    ],
)
def test_sections_detected(name: str, sections: list[str]) -> None:
    assert analyze(name).sections_found == sections


def test_responsibilities_from_bullets_and_sentences() -> None:
    result = analyze("backend_engineer.txt")
    assert result.responsibilities[0] == (
        "You will design and operate the services that move money for thousands of businesses."
    )
    assert "Mentor engineers through code reviews, design docs and pairing" in (
        result.responsibilities
    )
    assert len(result.responsibilities) == 7


def test_responsibilities_skip_team_intro_sentences() -> None:
    result = analyze("frontend_engineer.txt")
    assert not any(r.startswith("You'll join") for r in result.responsibilities)
    assert len(result.responsibilities) == 5


def test_messy_responsibility_from_you_will_sentence() -> None:
    assert analyze("messy.txt").responsibilities == [
        "you will build our customer dashboard + internal tools, work directly w/ the founders."
    ]


def test_certifications() -> None:
    backend = analyze("backend_engineer.txt").certifications
    assert [(c.name, c.weight) for c in backend] == [
        ("AWS Certified Solutions Architect", "medium")
    ]
    ml = analyze("ml_engineer.txt").certifications
    assert [(c.name, c.weight) for c in ml] == [
        ("Google Cloud Professional Machine Learning Engineer", "medium")
    ]


def test_generic_and_required_certifications() -> None:
    result = analyze_jd("Requirements\n- CKA required\n- Salesforce Administrator certification")
    assert [(c.name, c.weight) for c in result.certifications] == [
        ("Certified Kubernetes Administrator", "high"),
        ("Salesforce Administrator certification", "high"),
    ]


# --- cue words and section rules -------------------------------------------------------


def test_cue_words_override_section() -> None:
    jd = (
        "Requirements\n"
        "- Python\n"
        "- Kubernetes is a plus\n"
        "Nice to have\n"
        "- Terraform\n"
        "- Go is required for this team\n"
        "- Rust (not required)\n"
    )
    result = analyze_jd(jd)
    assert [s.name for s in result.mandatory_skills] == ["Python", "Go"]
    assert [s.name for s in result.preferred_skills] == ["Kubernetes", "Terraform", "Rust"]


def test_skill_in_both_sections_counts_as_mandatory() -> None:
    result = analyze_jd("Nice to have\n- Docker\nRequirements\n- Docker and Linux")
    assert [s.name for s in result.mandatory_skills] == ["Docker", "Linux"]
    assert result.preferred_skills == []


def test_semicolon_splits_cue_scope() -> None:
    result = analyze_jd("Strong Python required; Rust is a plus")
    assert [s.name for s in result.mandatory_skills] == ["Python"]
    assert [s.name for s in result.preferred_skills] == ["Rust"]


def test_header_variants() -> None:
    jd = (
        "## What you'll bring\n- Java\n"
        "**Bonus**\n- Scala\n"
        "Day-to-Day\n- Write Kotlin services\n"
        "Perks\n- Free Snowflake credits\n"
    )
    result = analyze_jd(jd)
    assert result.sections_found == ["requirements", "preferred", "responsibilities", "other"]
    assert [s.name for s in result.mandatory_skills] == ["Java", "Kotlin"]
    assert [s.name for s in result.preferred_skills] == ["Scala"]


def test_years_variants() -> None:
    def years(text: str) -> tuple:
        e = analyze_jd(text).experience
        return e.min_years, e.max_years

    assert years("Requires at least three years of Java") == (3, None)
    assert years("5 or more years with Go") == (5, None)
    assert years("2-4 yrs of React") == (2, 4)
    assert years("We were founded 20 years ago. 3+ years of Python.") == (3, None)
    # The primary requirement is the largest mandatory one.
    assert years("5+ years of backend; 2+ years with Kubernetes") == (5, None)


@pytest.mark.parametrize(
    ("title", "level"),
    [
        ("Staff Software Engineer", "staff"),
        ("Senior Staff Engineer", "staff"),
        ("Sr. Data Engineer", "senior"),
        ("Software Engineer II", "mid"),
        ("Junior Frontend Developer", "junior"),
        ("Engineering Manager, Platform", "manager"),
        ("Machine Learning Intern", "intern"),
    ],
)
def test_seniority_from_title(title: str, level: str) -> None:
    result = analyze_jd(f"{title}\n\nRequirements\n- Python")
    assert (result.seniority, result.seniority_source) == (level, "title")


def test_seniority_from_text_phrase() -> None:
    result = analyze_jd("We're hiring for an entry-level role. Requirements: Python and SQL.")
    assert (result.seniority, result.seniority_source) == ("junior", "text")


# --- messy input never crashes ---------------------------------------------------------


@pytest.mark.parametrize("text", ["", "   \n\t  ", "​﻿"])
def test_empty_input(text: str) -> None:
    result = analyze_jd(text)
    assert result.all_skills == []
    assert result.warnings == ["Job description is empty."]


def test_no_headers_all_one_paragraph() -> None:
    jd = (
        "We need a data engineer with 3+ years of Python and SQL who has built pipelines "
        "in Airflow; dbt is a plus. You will own our Snowflake warehouse."
    )
    result = analyze_jd(jd)
    assert result.sections_found == []
    assert "No section headers found; importance was inferred from cue words." in result.warnings
    assert [s.name for s in result.mandatory_skills] == [
        "Python", "SQL", "Apache Airflow", "Snowflake",
    ]  # fmt: skip
    assert [s.name for s in result.preferred_skills] == ["dbt"]
    assert result.responsibilities == ["You will own our Snowflake warehouse."]
    assert result.experience.min_years == 3


def test_non_english_german() -> None:
    jd = (
        "Senior Backend-Entwickler (m/w/d)\n"
        "Wir suchen eine erfahrene Person mit fundierten Kenntnissen in Python und Kubernetes. "
        "Erfahrung mit Terraform ist wünschenswert."
    )
    result = analyze_jd(jd)
    assert [s.name for s in result.mandatory_skills] == ["Python", "Kubernetes", "Terraform"]
    assert result.seniority == "senior"
    assert "Text may not be English; only taxonomy terms were matched." in result.warnings


def test_non_english_japanese() -> None:
    result = analyze_jd("シニアバックエンドエンジニア\nPythonとAWSの経験が必須です。")
    assert [s.name for s in result.mandatory_skills] == ["Python", "AWS"]
    assert "Text may not be English; only taxonomy terms were matched." in result.warnings


@pytest.mark.parametrize(
    "text",
    [
        "::::",
        "Requirements:",
        "- \n* \n1. ",
        "💼🚀🔥" * 50,
        "a" * 20_000,
        "Requirements\n" + "- Python, Go, Rust, k8s, AWS\n" * 500,
        "C++ C# .NET ;;;; ... !!! ??? (((",
        "Nice to have: " * 100,
    ],
    ids=["colons", "header-only", "empty-bullets", "emoji", "long-word", "long-jd", "punct", "rep"],
)
def test_garbage_input_does_not_crash(text: str) -> None:
    assert isinstance(analyze_jd(text), JDRequirements)
