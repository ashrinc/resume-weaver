"""Rule-based job description analyzer (PRD FR-03).

Pipeline
--------
1. Normalise the text and split it into *units*: each physical line, further
   split into sentences, so a JD that is one long paragraph still works.
2. Walk the units and detect section headers ("Requirements", "Nice to have",
   "What you'll do", ...). Headers may stand alone or be inline
   ("Must have: Python, Go"). Each content unit is tagged with its section:
   intro, responsibilities, requirements, preferred or other.
3. Split units into clauses on ';' and decide each clause's importance:
   a mandatory cue ("required", "must") wins, then a preferred cue ("a plus",
   "bonus", "nice to have"), otherwise the section default (preferred section
   -> preferred, everything else -> mandatory).
4. Match skills and certifications against the taxonomy. A skill seen as both
   mandatory and preferred counts as mandatory. Tech skills get weight high
   (mandatory) or medium (preferred); soft skills always get low.
5. Extract years of experience, seniority, responsibilities and the job title.

"Other" sections (about us, benefits, pay, how to apply) are ignored for
skills, years and responsibilities. The analyzer never raises on odd input.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.jd.models import (
    CertificationRequirement,
    ExperienceRequirement,
    JDRequirements,
    JDSection,
    Seniority,
    SkillRequirement,
    YearsMention,
)
from app.jd.taxonomy import Taxonomy, get_taxonomy

__all__ = ["analyze_jd"]


# --------------------------------------------------------------------------
# Units and section headers
# --------------------------------------------------------------------------


@dataclass
class _Unit:
    text: str
    is_bullet: bool
    section: JDSection
    index: int = 0


_BULLET_RE = re.compile(r"^\s*(?:[-*•·▪◦‣–—►✓✔]|\d{1,2}[.)])\s+")
_MARKDOWN_RE = re.compile(r"^#+\s*|\*\*|__")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_ABBREVIATIONS = {"e.g.", "i.e.", "etc.", "sr.", "jr.", "vs.", "approx.", "incl.", "a.k.a.", "no."}

_HEADER_RULES: list[tuple[JDSection, re.Pattern[str]]] = [
    (
        "other",
        re.compile(
            r"\b(about (us|the company|the team)|who we are|our (company|mission|story|values)"
            r"|benefits|perks|compensation|salary|pay( range)?|what we offer|why (join|work)"
            r"|equal (opportunity|employment)|eeo|location|how to apply|life at)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "preferred",
        re.compile(
            r"\b(nice[\s-]to[\s-]haves?|preferred|bonus|pluses|desired|desirable"
            r"|good[\s-]to[\s-]haves?|extra credit|ideally|would be (great|nice))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "requirements",
        re.compile(
            r"\b(requirements?|qualifications?|must[\s-]haves?|what you('ll|’ll)? (bring|need)"
            r"|what we('re|’re| are) looking for|who you are|about you|you have|you bring"
            r"|skills|experience|minimum|basic|required|ideal candidate|your (background|profile)"
            r"|what it takes)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "responsibilities",
        re.compile(
            r"\b(responsibilit\w*|what you('ll|’ll| will) (do|work on|be doing|own)"
            r"|(the|your) role|role overview|duties|day[\s-]to[\s-]day|in this role|your impact"
            r"|key (accountabilities|tasks)|job description|the opportunity)\b",
            re.IGNORECASE,
        ),
    ),
]
_EXACT_HEADERS = {
    "requirements", "responsibilities", "qualifications", "nice to have", "nice-to-have",
    "preferred", "preferred qualifications", "what you'll do", "what you will do",
    "what we offer", "benefits", "about us", "about the role", "about you", "bonus points",
    "perks", "skills", "minimum qualifications", "basic qualifications",
}  # fmt: skip
_INLINE_HEADER_RE = re.compile(r"^([^:]{2,40}):\s*(.*)$")
_RESPONSIBILITY_START_RE = re.compile(
    # "You'll join the team" / "You will report to" describe the job, not duties.
    r"^(you will|you'll|you’ll)\b"
    r"(?!\s+(?:join|be joining|be part of|report|have|get|receive|enjoy))",
    re.IGNORECASE,
)


def _classify_header(text: str) -> JDSection | None:
    for section, pattern in _HEADER_RULES:
        if pattern.search(text):
            return section
    return None


def _looks_like_heading(text: str, had_markdown: bool) -> bool:
    if had_markdown:
        return True
    letters = [c for c in text if c.isalpha()]
    if letters and all(c.isupper() for c in letters):
        return True
    words = re.findall(r"[A-Za-z][A-Za-z'’]*", text)
    # Judge title case on words longer than 3 letters ("Nice to Have"); for
    # short-word headings like "Day-to-Day" fall back to the first word.
    significant = [w for w in words if len(w) > 3] or words[:1]
    return bool(significant) and all(w[0].isupper() for w in significant)


def _detect_header(text: str, had_markdown: bool) -> tuple[JDSection, str] | None:
    """Return (section, inline content) if ``text`` is a header, else None."""
    m = _INLINE_HEADER_RE.match(text)
    if m:
        section = _classify_header(m.group(1))
        return (section, m.group(2).strip()) if section else None
    cleaned = text.strip()
    if len(cleaned) > 60 or len(cleaned.split()) > 8 or cleaned.endswith((".", "!", "?")):
        return None
    section = _classify_header(cleaned)
    if section is None:
        return None
    if cleaned.lower() in _EXACT_HEADERS or _looks_like_heading(cleaned, had_markdown):
        return section, ""
    return None


def _split_sentences(line: str) -> list[str]:
    merged: list[str] = []
    for part in _SENTENCE_SPLIT_RE.split(line):
        last_word = merged[-1].split()[-1:] if merged else []
        if last_word and last_word[0].lower() in _ABBREVIATIONS:
            merged[-1] += " " + part
        else:
            merged.append(part)
    return [p.strip() for p in merged if p.strip()]


def _normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    for ch, rep in ((" ", " "), ("​", ""), ("﻿", ""), ("\t", " ")):
        text = text.replace(ch, rep)
    return text


def _to_units(text: str) -> tuple[list[_Unit], list[JDSection]]:
    units: list[_Unit] = []
    found: list[JDSection] = []
    section: JDSection = "intro"
    for raw_line in _normalize(text).split("\n"):
        if not raw_line.strip():
            continue
        is_bullet = bool(_BULLET_RE.match(raw_line))
        line = _BULLET_RE.sub("", raw_line, count=1)
        had_markdown = bool(_MARKDOWN_RE.search(line))
        line = _MARKDOWN_RE.sub("", line).strip()
        for sentence in _split_sentences(line):
            header = (
                None
                if (is_bullet and ":" not in sentence)
                else _detect_header(sentence, had_markdown)
            )
            if header is not None:
                kind, inline = header
                if kind not in found:
                    found.append(kind)
                if not is_bullet:
                    section = kind  # bullets with "Label:" only tag themselves
                if inline:
                    units.append(_Unit(inline, is_bullet, kind))
                continue
            unit_section = section
            if section == "intro" and _RESPONSIBILITY_START_RE.match(sentence):
                unit_section = "responsibilities"
            units.append(_Unit(sentence, is_bullet, unit_section))
    for i, unit in enumerate(units):
        unit.index = i
    return units, found


# --------------------------------------------------------------------------
# Importance cues
# --------------------------------------------------------------------------

_NEGATED_MANDATORY_RE = re.compile(
    r"\bnot (?:required|mandatory|a must|essential)\b", re.IGNORECASE
)
_MANDATORY_CUE_RE = re.compile(
    r"\b(required|must|mandatory|essential|needs? to|minimum|at least)\b", re.IGNORECASE
)
_PREFERRED_CUE_RE = re.compile(
    r"\b(nice[\s-]to[\s-]have|(?:a|big|huge|strong|definite) plus|bonus|preferred|preferably"
    r"|ideally|desired|desirable|good[\s-]to[\s-]have|would be (?:great|nice|helpful)"
    r"|not required|optional|advantageous|an advantage)\b",
    re.IGNORECASE,
)


def _is_mandatory(clause: str, section: JDSection) -> bool:
    if _MANDATORY_CUE_RE.search(_NEGATED_MANDATORY_RE.sub(" ", clause)):
        return True
    if _PREFERRED_CUE_RE.search(clause):
        return False
    return section != "preferred"


def _clauses(unit: _Unit) -> list[tuple[int, str]]:
    out, pos = [], 0
    for part in unit.text.split(";"):
        out.append((pos, part))
        pos += len(part) + 1
    return out


# --------------------------------------------------------------------------
# Years of experience and seniority
# --------------------------------------------------------------------------

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15,
}  # fmt: skip
_NUM = r"(\d{1,2}|" + "|".join(_NUMBER_WORDS) + r")"
_YEARS_UNIT = r"(?:years?|yrs?)\b(?!\s+(?:ago|old|of age))"
_YEARS_RANGE_RE = re.compile(
    rf"\b{_NUM}\s*\+?\s*(?:-|–|—|to)\s*{_NUM}\s*\+?\s*{_YEARS_UNIT}", re.IGNORECASE
)
_YEARS_MIN_RE = re.compile(rf"\b{_NUM}\s*(?:\+|\s*or more|\s*plus)?\s*{_YEARS_UNIT}", re.IGNORECASE)


def _to_int(token: str) -> int:
    return int(token) if token.isdigit() else _NUMBER_WORDS[token.lower()]


def _find_years(clause: str) -> list[tuple[int, int | None]]:
    found: list[tuple[int, int | None]] = []
    covered: list[tuple[int, int]] = []
    for m in _YEARS_RANGE_RE.finditer(clause):
        found.append((_to_int(m.group(1)), _to_int(m.group(2))))
        covered.append(m.span())
    for m in _YEARS_MIN_RE.finditer(clause):
        if not any(a <= m.start() < b for a, b in covered):
            found.append((_to_int(m.group(1)), None))
    return found


_ROLE_LEVEL = r"\b(?:engineer|developer|scientist|analyst)"  # "Engineer II"
_TITLE_SENIORITY: list[tuple[Seniority, re.Pattern[str]]] = [
    (level, re.compile(pattern, re.IGNORECASE))
    for level, pattern in [
        ("intern", r"\bintern(ship)?\b|\bco-?op\b"),
        ("director", r"\bdirector\b|\bvp\b|\bvice president\b|\bhead of\b"),
        ("manager", r"\bmanager\b"),
        ("principal", r"\bprincipal\b|\bdistinguished\b"),
        ("staff", r"\bstaff\b"),
        ("lead", r"\blead\b"),
        ("senior", rf"\bsenior\b|\bsr\.?(?=\s|$)|{_ROLE_LEVEL}\s+(?:iii|3)\b"),
        ("mid", rf"\bmid[\s-]?level\b|\bintermediate\b|{_ROLE_LEVEL}\s+(?:ii|2)\b"),
        ("junior", rf"\bjunior\b|\bjr\.?(?=\s|$)|\bentry[\s-]level\b|\bnew grad(?:uate)?\b"
                   rf"|{_ROLE_LEVEL}\s+i\b"),
    ]
]  # fmt: skip
_TEXT_SENIORITY: list[tuple[Seniority, re.Pattern[str]]] = [
    ("intern", re.compile(r"\binternship\b", re.IGNORECASE)),
    ("junior", re.compile(r"\bentry[\s-]level\b|\bnew grad(?:uate)?s?\b", re.IGNORECASE)),
    ("mid", re.compile(r"\bmid[\s-]level\b", re.IGNORECASE)),
    ("senior", re.compile(r"\bsenior[\s-]level\b", re.IGNORECASE)),
]
_ROLE_NOUN_RE = re.compile(
    r"\b(engineer|developer|dev|programmer|scientist|analyst|architect|manager|designer"
    r"|intern|administrator|specialist|consultant|sre|researcher|director)s?\b",
    re.IGNORECASE,
)


def _match_seniority(text: str, rules: list[tuple[Seniority, re.Pattern[str]]]) -> Seniority | None:
    for level, pattern in rules:
        if pattern.search(text):
            return level
    return None


def _seniority_from_years(years: int) -> Seniority:
    if years < 2:
        return "junior"
    if years < 5:
        return "mid"
    if years < 10:
        return "senior"
    return "staff"


def _find_title(units: list[_Unit]) -> str | None:
    for unit in units[:3]:
        if unit.section != "intro" or unit.is_bullet:
            continue
        text = unit.text.strip()
        if len(text.split()) > 12 or text.endswith((".", "!", "?")):
            continue
        if _ROLE_NOUN_RE.search(text) or _match_seniority(text, _TITLE_SENIORITY):
            return text
    return None


# --------------------------------------------------------------------------
# Certifications
# --------------------------------------------------------------------------

_GENERIC_CERT_RE = re.compile(r"((?:[A-Z][\w+.&/-]*\s+){1,5})[Cc]ertifi(?:cations?|cates?)\b")


def _generic_certs(clause: str, taxonomy: Taxonomy) -> list[tuple[str, int, int]]:
    """Unlisted certs like 'Salesforce Administrator certification'. The
    phrase must contain an acronym or a known tech term to count."""
    out = []
    for m in _GENERIC_CERT_RE.finditer(clause):
        words = m.group(1).split()
        if any(re.fullmatch(r"[A-Z0-9+&/-]{2,}", w) or taxonomy.is_tech_term(w) for w in words):
            out.append((f"{' '.join(words)} certification", m.start(), m.end()))
    return out


# --------------------------------------------------------------------------
# Language check
# --------------------------------------------------------------------------

_ENGLISH_STOPWORDS = {
    "the", "and", "to", "of", "a", "in", "for", "with", "you", "we", "our", "on", "is",
    "are", "will", "be", "as", "or", "your", "an", "this", "that", "at", "by", "from",
    "have", "experience", "work", "team",
}  # fmt: skip


def _looks_non_english(text: str) -> bool:
    words = re.findall(r"[^\W\d_]+", text)
    if not words:
        return False
    latin = sum(1 for w in words if w.isascii())
    if latin / len(words) < 0.5:
        return True
    english = sum(1 for w in words if w.lower() in _ENGLISH_STOPWORDS)
    return len(words) >= 8 and english / len(words) < 0.08


# --------------------------------------------------------------------------
# Main entry point
# --------------------------------------------------------------------------


@dataclass
class _SkillAcc:
    name: str
    first: tuple[int, int]
    terms: list[str] = field(default_factory=list)
    occurrences: int = 0
    mandatory: bool = False


def analyze_jd(text: str, taxonomy: Taxonomy | None = None) -> JDRequirements:
    """Extract categorized, weighted requirements from a job description."""
    taxonomy = taxonomy or get_taxonomy()
    if not text or not _normalize(text).strip():
        return JDRequirements(warnings=["Job description is empty."])

    units, sections_found = _to_units(text)
    relevant = [u for u in units if u.section != "other"]

    skills: dict[str, _SkillAcc] = {}
    certs: dict[str, CertificationRequirement] = {}
    cert_order: dict[str, tuple[int, int]] = {}
    mentions: list[YearsMention] = []

    for unit in relevant:
        for offset, clause in _clauses(unit):
            mandatory = _is_mandatory(clause, unit.section)

            for match in taxonomy.find_skills(clause):
                pos = (unit.index, offset + match.start)
                acc = skills.setdefault(match.name, _SkillAcc(match.name, pos))
                acc.occurrences += 1
                acc.mandatory = acc.mandatory or mandatory
                if match.surface not in acc.terms:
                    acc.terms.append(match.surface)

            known = [(m.name, m.start, m.end) for m in taxonomy.find_certifications(clause)]
            generic = [
                g for g in _generic_certs(clause, taxonomy)
                if not any(g[1] < k_end and k_start < g[2] for _, k_start, k_end in known)
            ]  # fmt: skip
            for name, start, end in known + generic:
                weight = "high" if mandatory else "medium"
                existing = certs.get(name)
                if existing is None:
                    certs[name] = CertificationRequirement(
                        name=name, weight=weight, matched_text=clause[start:end]
                    )
                    cert_order[name] = (unit.index, offset + start)
                elif weight == "high":
                    existing.weight = "high"

            for min_years, max_years in _find_years(clause):
                mentions.append(
                    YearsMention(
                        min_years=min_years,
                        max_years=max_years,
                        weight="high" if mandatory else "medium",
                        text=clause.strip()[:200],
                    )
                )

    ordered = sorted(skills.values(), key=lambda a: a.first)
    mandatory_skills, preferred_skills, soft_skills = [], [], []
    for acc in ordered:
        info = taxonomy.skills[acc.name]
        if info.type == "soft":
            weight, bucket = "low", soft_skills
        elif acc.mandatory:
            weight, bucket = "high", mandatory_skills
        else:
            weight, bucket = "medium", preferred_skills
        bucket.append(
            SkillRequirement(
                name=acc.name,
                category=info.category,
                type=info.type,
                weight=weight,
                matched_terms=acc.terms,
                occurrences=acc.occurrences,
            )
        )

    experience = ExperienceRequirement(mentions=mentions)
    if mentions:
        pool = [m for m in mentions if m.weight == "high"] or mentions
        primary = max(pool, key=lambda m: m.min_years)
        experience.min_years, experience.max_years = primary.min_years, primary.max_years

    title = _find_title(units)
    seniority = _match_seniority(title, _TITLE_SENIORITY) if title else None
    source = "title" if seniority else None
    if seniority is None:
        seniority = _match_seniority(" ".join(u.text for u in relevant), _TEXT_SENIORITY)
        source = "text" if seniority else None
    if seniority is None and experience.min_years is not None:
        seniority, source = _seniority_from_years(experience.min_years), "years"

    responsibilities: list[str] = []
    for unit in relevant:
        if unit.section == "responsibilities":
            item = re.sub(r"\s+", " ", unit.text).strip(" ;")
            if item and item not in responsibilities:
                responsibilities.append(item)

    warnings: list[str] = []
    if not sections_found:
        warnings.append("No section headers found; importance was inferred from cue words.")
    if _looks_non_english(text):
        warnings.append("Text may not be English; only taxonomy terms were matched.")
    if not skills:
        warnings.append("No known skills were found.")

    return JDRequirements(
        title=title,
        mandatory_skills=mandatory_skills,
        preferred_skills=preferred_skills,
        soft_skills=soft_skills,
        responsibilities=responsibilities,
        experience=experience,
        seniority=seniority,
        seniority_source=source,
        certifications=sorted(certs.values(), key=lambda c: cert_order[c.name]),
        sections_found=[s for s in sections_found if s != "intro"],
        warnings=warnings,
    )
