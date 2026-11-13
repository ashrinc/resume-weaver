import json
from collections import Counter

import pytest

from app.jd.taxonomy import TAXONOMY_PATH, TaxonomyFile, get_taxonomy, term_key

RAW = json.loads(TAXONOMY_PATH.read_text())


def names(text: str) -> list[str]:
    return [m.name for m in get_taxonomy().find_skills(text)]


def test_taxonomy_is_broad() -> None:
    skills = RAW["skills"]
    assert len(skills) >= 300
    categories = Counter(s["category"] for s in skills)
    for category in (
        "language", "frontend", "backend", "cloud", "devops", "database",
        "data", "ml", "testing", "tools", "methodology", "soft",
    ):  # fmt: skip
        assert categories[category] >= 10, category
    assert {s["type"] for s in skills} == {"tech", "soft"}
    assert all(s["type"] == "soft" for s in skills if s["category"] == "soft")


def test_taxonomy_validates_and_has_no_conflicting_terms() -> None:
    TaxonomyFile.model_validate(RAW)  # unique names, valid implies
    owners: dict[str, str] = {}
    for skill in RAW["skills"]:
        cs = set(skill.get("case_sensitive", []))
        for term in [skill["name"], *skill["aliases"]]:
            if term in cs:
                continue
            owner = owners.setdefault(term_key(term), skill["name"])
            assert owner == skill["name"], f"{term!r}: {owner} vs {skill['name']}"


@pytest.mark.parametrize(
    ("alias", "canonical"),
    [
        ("JS", "JavaScript"),
        ("k8s", "Kubernetes"),
        ("Postgres", "PostgreSQL"),
        ("golang", "Go"),
        ("Node", "Node.js"),
        ("sklearn", "scikit-learn"),
        ("NLP", "Natural Language Processing"),
        ("TDD", "Test-Driven Development"),
        ("GKE", "Google Kubernetes Engine"),
        ("self-starter", "Self-Motivation"),
    ],
)
def test_aliases_resolve_to_canonical_names(alias: str, canonical: str) -> None:
    assert names(f"Experience with {alias} required") == [canonical] + (
        list(get_taxonomy().skills[canonical].implies)
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Java and JavaScript", ["Java", "JavaScript"]),
        ("C++, C#, .NET and ASP.NET Core", ["C++", "C#", ".NET", "ASP.NET"]),
        ("React Native and React", ["React Native", "React"]),
        ("AWS Lambda", ["AWS Lambda", "AWS"]),  # implied
        ("Python/Django and React-based UIs", ["Python", "Django", "React"]),
        ("REST APIs and microservices", ["REST APIs", "Microservices"]),
        ("front-end development", ["Frontend Development"]),
    ],
)
def test_matching_boundaries_and_longest_match(text: str, expected: list[str]) -> None:
    assert names(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "we go fast and the rest of the team goes home",  # Go, REST are case-sensitive
        "worker nodes in the cluster",  # Node does not pluralise
        "R&D budget for C-level execs",  # single letters
        "this will spark joy",  # Spark is case-sensitive
    ],
)
def test_ambiguous_words_do_not_match(text: str) -> None:
    assert names(text) == []


def test_is_tech_term() -> None:
    tx = get_taxonomy()
    assert tx.is_tech_term("Python") and tx.is_tech_term("Spring Boot") and tx.is_tech_term("Go")
    assert not tx.is_tech_term("go")
    assert not tx.is_tech_term("Mentored")
    assert not tx.is_tech_term("communication")  # soft skills are not tech terms


def test_certification_matching() -> None:
    found = [m.name for m in get_taxonomy().find_certifications("CKA, Security+ and PMP")]
    assert found == ["Certified Kubernetes Administrator", "CompTIA Security+", "PMP"]
