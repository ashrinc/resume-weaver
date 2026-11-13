"""Unit tests for the Layer A guards (M11.1, reworked in M11.2)."""

import pytest

from app.tailor.guards import (
    ResumeContext,
    bullet_problem,
    escalation,
    merged_into,
    name_tokens,
    numbers,
)

CTX = ResumeContext.from_text(
    "Built APIs in Python and Flask serving 40K daily requests. Exposed REST endpoints for "
    "partners. Containerized three legacy services with Docker and deployed them to AWS ECS. "
    "Cut p95 latency by 35%. Used Redis for caching. Improved query performance of reports "
    "in PostgreSQL. Raised test coverage from 52% to 84%."
)


def test_numbers_are_normalised() -> None:
    assert numbers("2,000+ shipments, 35% faster, $1.20M, three services") == {
        "2000": 1, "35": 1, "1.2": 1, "3": 1,
    }  # fmt: skip
    assert numbers("p95 latency on EC2 and k8s") == {}  # glued digits are names


def test_name_tokens_skip_sentence_initial_capitals() -> None:
    assert name_tokens("Built APIs on AWS ECS. Reduced p95 latency") == {
        "apis", "aws", "ecs", "p95",
    }  # fmt: skip


def test_context_includes_hierarchy_parents_but_not_siblings() -> None:
    assert {"PostgreSQL", "Relational Databases", "SQL", "Caching"} <= CTX.supported_tech
    assert "MongoDB" not in CTX.supported_tech and "NoSQL" not in CTX.supported_tech


# --- what passes -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("original", "rewritten"),
    [
        # Synonym from the taxonomy (query performance = query optimization).
        (
            "Improved query performance of reports in PostgreSQL",
            "Drove query optimization for reports in \\textbf{PostgreSQL}",
        ),
        # Alias: REST endpoints -> RESTful API development.
        ("Exposed REST endpoints for partners", "Led RESTful API development for partners"),
        # Hierarchy: PostgreSQL supports "relational database".
        (
            "Improved query performance of reports in PostgreSQL",
            "Improved query performance of reports in a PostgreSQL relational database",
        ),
        # A number that exists elsewhere in the resume is allowed by Layer A.
        ("Cut p95 latency by 35\\%", "Cut p95 latency by 35\\% for 40K daily requests"),
        # Honest restructuring with a stronger verb.
        (
            "Built APIs in \\textbf{Python} and \\textbf{Flask}, serving 40K daily requests",
            "Engineered \\textbf{Flask} APIs in \\textbf{Python} serving 40K daily requests",
        ),
        (
            "Containerized three legacy services with Docker",
            "Containerized 3 legacy services using Docker",
        ),
    ],
)
def test_supported_rewrites_pass(original: str, rewritten: str) -> None:
    assert bullet_problem(original, rewritten, CTX) is None


# --- what is reverted ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("original", "rewritten", "expected"),
    [
        (
            "Improved query performance of reports in PostgreSQL",
            "Improved query performance of reports in PostgreSQL and MongoDB",
            'unsupported tool: "MongoDB"',
        ),
        ("Cut p95 latency by 35\\%", "Cut p95 latency by 45\\%", 'new number: "45"'),
        (
            "Raised test coverage from 52\\% to 84\\%",
            "Raised test coverage to 84\\%",
            'lost number: dropped "52"',
        ),
        ("Cut p95 latency by 35\\%", "Cut latency by 35\\%", 'vaguer scope: dropped "p95"'),
        (
            "Built the dashboard, deployed using Vercel and Render",
            "Built the dashboard and shipped frontend and backend deployments",
            'vaguer scope: dropped "Vercel"',
        ),
        (
            "Containerized three legacy services",
            "Containerized legacy microservices",
            "lost number",
        ),
    ],
)
def test_fact_changes_are_reverted(original: str, rewritten: str, expected: str) -> None:
    problem = bullet_problem(original, rewritten, CTX)
    assert problem is not None and expected in problem


@pytest.mark.parametrize(
    ("original", "rewritten", "expected"),
    [
        (
            "rate limits to prevent abuse",
            "rate limits to eliminate abuse",
            '"prevent" became "eliminate"',
        ),
        ("Reduced failed logins", "Eliminated failed logins", '"Reduced" became "Eliminated"'),
        ("Tested some endpoints", "Tested all endpoints", '"some" became "all"'),
        (
            "Contributed to the billing rewrite",
            "Led the billing rewrite",
            '"Contributed" became "Led"',
        ),
        (
            "Assisted with the schema migration",
            "Owned the schema migration",
            '"Assisted" became "Owned"',
        ),
    ],
)
def test_certainty_and_scope_escalations(original: str, rewritten: str, expected: str) -> None:
    result = escalation(original, rewritten)
    assert result is not None and expected in result
    problem = bullet_problem(original, rewritten, ResumeContext.from_text(original))
    assert problem is not None and problem.startswith("inflated claim")


@pytest.mark.parametrize(
    ("original", "rewritten"),
    [
        ("Developed internal tools in Django", "Built internal tools in Django"),
        ("Used Redis for caching", "Built a Redis caching layer"),  # verifier's call, not A's
        ("Led a team of 3", "Led a 3-person team"),
        ("Reduced latency by 35%", "Cut latency by 35%"),
    ],
)
def test_generic_verb_changes_are_not_layer_a_violations(original: str, rewritten: str) -> None:
    assert escalation(original, rewritten) is None


def test_merge_detection() -> None:
    own = "Wrote PostgreSQL migrations"
    other = "Tuned slow queries with indexes and EXPLAIN plans"
    assert merged_into(own + " and tuned slow queries with indexes and EXPLAIN plans", own, other)
    assert not merged_into("Wrote PostgreSQL schema migrations", own, other)


def test_practices_are_not_tools() -> None:
    from app.tailor.guards import unverified_concepts

    # "tuned slow queries" -> "query optimization": not a Layer A violation...
    original = "Wrote migrations and tuned slow queries with indexes"
    rewrite = "Wrote migrations and drove query optimization with indexes"
    ctx = ResumeContext.from_text(original)
    assert bullet_problem(original, rewrite, ctx) is None
    # ...but flagged so that only the semantic verifier can accept it.
    assert unverified_concepts(rewrite, ctx) == {"Query Optimization"}
    # A named tool is still a hard Layer A failure.
    tool = bullet_problem(original, rewrite + " on Kubernetes", ctx)
    assert tool is not None and "Kubernetes" in tool
