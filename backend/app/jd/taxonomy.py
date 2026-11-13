"""Skill taxonomy: loading, validation and term matching.

``skills_taxonomy.json`` holds canonical skill names, aliases, a tech/soft
flag, optional case-sensitive terms for ambiguous words (``Go``, ``R``,
``Swift``, ``Spark``, ``REST``) and optional implied skills (``Amazon S3``
implies ``AWS``). It also lists well-known certifications.

Matching rules
--------------
* Terms are matched on word-ish boundaries that understand tech punctuation:
  ``C++``, ``C#``, ``.NET``, ``Node.js`` and ``CI/CD`` all work, and ``Java``
  does not match inside ``JavaScript``.
* Longest match wins (``React Native`` beats ``React``, ``AWS Lambda`` beats
  ``AWS``); implied skills are then added.
* Spaces and hyphens in terms are interchangeable (``front-end`` = ``front end``).
* Lower-case or multi-word terms also match a plural ``s``/``es``
  (``microservices``, ``REST APIs``). Capitalised single words do not, so
  ``Node`` does not match ``nodes``.
* Case-sensitive terms match exactly; single letters (``C``, ``R``) also
  reject ``C-level`` and ``R&D``.

Hierarchy (M11.2)
-----------------
``parents`` lists broader skills a skill is evidence for: PostgreSQL ->
Relational Databases and SQL, Redis -> Caching, FastAPI -> Backend Frameworks.
It only goes upward, so a child never supports an unrelated sibling
(PostgreSQL does not support MongoDB). ``implies`` stays a full-credit
equivalence (Amazon S3 -> AWS); a parent is a related, partial match.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

TAXONOMY_PATH = Path(__file__).with_name("skills_taxonomy.json")

_BEFORE = r"(?<![A-Za-z0-9_+#@.])"
_AFTER = r"(?![A-Za-z0-9_+#@])(?!\.[A-Za-z0-9])"


class TaxonomySkill(BaseModel):
    name: str
    category: str
    type: Literal["tech", "soft"]
    aliases: list[str] = Field(default_factory=list)
    case_sensitive: list[str] = Field(default_factory=list)
    implies: list[str] = Field(default_factory=list)
    concept: bool = Field(
        default=False,
        description="A practice or activity (Query Optimization, Code Review), not a named "
        "tool or platform. Layer A leaves unsupported concepts to the semantic verifier.",
    )
    parents: list[str] = Field(
        default_factory=list,
        description="Broader skills this one is evidence for (PostgreSQL -> Relational "
        "Databases). Unlike implies, a parent is only a related (partial) match.",
    )


class TaxonomyCertification(BaseModel):
    name: str
    aliases: list[str] = Field(default_factory=list)


class TaxonomyFile(BaseModel):
    version: int
    skills: list[TaxonomySkill]
    certifications: list[TaxonomyCertification] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_references(self) -> TaxonomyFile:
        names = [s.name for s in self.skills]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise ValueError(f"duplicate skill names: {sorted(dupes)}")
        known = set(names)
        for skill in self.skills:
            missing = set(skill.implies) - known
            if missing:
                raise ValueError(f"{skill.name} implies unknown skills: {sorted(missing)}")
            missing = set(skill.parents) - known
            if missing:
                raise ValueError(f"{skill.name} has unknown parents: {sorted(missing)}")
            if skill.name in skill.parents:
                raise ValueError(f"{skill.name} lists itself as a parent")
        return self


@dataclass(frozen=True)
class TermMatch:
    name: str  # canonical name
    start: int
    end: int
    surface: str  # text as it appeared


def term_key(term: str) -> str:
    """Normalise a term for lookup: case-folded, spaces and hyphens removed."""
    return re.sub(r"[\s\-]+", "", term.casefold())


def _term_pattern(term: str, plural: bool) -> str:
    parts = [re.escape(p) for p in re.split(r"[\s\-]+", term)]
    pattern = r"[\s\-]*".join(parts)
    if len(term) == 1:
        pattern += r"(?![-&'’])"  # C-level, R&D, C's
    if plural:
        pattern += r"(?:e?s)?"
    return pattern


def _allows_plural(term: str) -> bool:
    return term == term.lower() or (" " in term and not term.isupper())


class _TermIndex:
    """Maps surface terms to canonical names and finds them in text."""

    def __init__(self, entries: list[tuple[str, list[str], list[str]]]) -> None:
        # entries: (canonical name, case-insensitive terms, case-sensitive terms)
        self.ci: dict[str, str] = {}
        self.cs: dict[str, str] = {}
        ci_patterns: list[tuple[int, str]] = []
        cs_patterns: list[tuple[int, str]] = []
        for name, ci_terms, cs_terms in entries:
            for term in ci_terms:
                key = term_key(term)
                owner = self.ci.setdefault(key, name)
                if owner != name:
                    raise ValueError(f"term {term!r} maps to both {owner!r} and {name!r}")
                ci_patterns.append((len(term), _term_pattern(term, _allows_plural(term))))
            for term in cs_terms:
                owner = self.cs.setdefault(term, name)
                if owner != name:
                    raise ValueError(f"term {term!r} maps to both {owner!r} and {name!r}")
                cs_patterns.append((len(term), _term_pattern(term, plural=False)))

        def build(patterns: list[tuple[int, str]], flags: int) -> re.Pattern[str] | None:
            if not patterns:
                return None
            # Longest first so alternation prefers 'React Native' over 'React'.
            alts = [p for _, p in sorted(set(patterns), key=lambda x: (-x[0], x[1]))]
            return re.compile(f"{_BEFORE}(?:{'|'.join(alts)}){_AFTER}", flags)

        self._ci_re = build(ci_patterns, re.IGNORECASE)
        self._cs_re = build(cs_patterns, 0)

    def lookup(self, surface: str) -> str | None:
        if surface in self.cs:
            return self.cs[surface]
        key = term_key(surface)
        for candidate in (key, key[:-2] if key.endswith("es") else None, key[:-1]):
            if candidate and candidate in self.ci:
                return self.ci[candidate]
        return None

    def find(self, text: str) -> list[TermMatch]:
        raw: list[TermMatch] = []
        for regex in (self._ci_re, self._cs_re):
            if regex is None:
                continue
            for m in regex.finditer(text):
                name = self.lookup(m.group(0))
                if name is not None:
                    raw.append(TermMatch(name, m.start(), m.end(), m.group(0)))
        # Resolve overlaps between the two passes: earliest, then longest wins.
        raw.sort(key=lambda t: (t.start, -(t.end - t.start)))
        result: list[TermMatch] = []
        last_end = -1
        for match in raw:
            if match.start >= last_end:
                result.append(match)
                last_end = match.end
        return result


class Taxonomy:
    def __init__(self, data: TaxonomyFile) -> None:
        self.data = data
        self.skills: dict[str, TaxonomySkill] = {s.name: s for s in data.skills}
        self._skills = _TermIndex(
            [
                (
                    s.name,
                    [t for t in (s.name, *s.aliases) if t not in s.case_sensitive],
                    s.case_sensitive,
                )
                for s in data.skills
            ]
        )
        self._certs = _TermIndex([(c.name, [c.name, *c.aliases], []) for c in data.certifications])

    def find_skills(self, text: str) -> list[TermMatch]:
        """All skill mentions in ``text``, plus implied skills (same span)."""
        matches: list[TermMatch] = []
        for m in self._skills.find(text):
            matches.append(m)
            for implied in self.skills[m.name].implies:
                matches.append(TermMatch(implied, m.start, m.end, m.surface))
        return matches

    def find_certifications(self, text: str) -> list[TermMatch]:
        return self._certs.find(text)

    def ancestors(self, name: str) -> set[str]:
        """Every broader skill ``name`` is evidence for, transitively (not itself)."""
        seen: set[str] = set()
        stack = list(self.skills[name].parents) if name in self.skills else []
        while stack:
            parent = stack.pop()
            if parent not in seen:
                seen.add(parent)
                stack.extend(self.skills[parent].parents)
        return seen

    def supporting(self, name: str, present: set[str]) -> list[str]:
        """Skills in ``present`` that are evidence for the broader skill ``name``
        through the hierarchy. Only upward: PostgreSQL supports Relational
        Databases, but Relational Databases never supports MongoDB."""
        return sorted(child for child in present if name in self.ancestors(child))

    def is_tech_term(self, phrase: str) -> bool:
        name = self._skills.lookup(phrase.strip())
        return name is not None and self.skills[name].type == "tech"


@lru_cache
def get_taxonomy() -> Taxonomy:
    data = TaxonomyFile.model_validate(json.loads(TAXONOMY_PATH.read_text(encoding="utf-8")))
    return Taxonomy(data)
