"""Requirement matching (M11.2): map each JD requirement to the resume
bullets and skills that support it. Deterministic; scikit-learn TF-IDF plus
the M3 taxonomy (aliases, implied skills and the parent hierarchy). No
embeddings, no network.

Skills (mandatory and preferred), in tiers:

==========  ===================  =====================================================
status      match_type           credit
==========  ===================  =====================================================
matched     exact / alias        1.0  canonical name, alias or implied skill
matched     fuzzy                FUZZY_CREDIT (0.8): typo, version suffix, or every
                                 word of a multi-word skill in one resume line
partial     related              HIERARCHY_CREDIT (0.5): a narrower skill in the resume
                                 supports it (PostgreSQL -> Relational Databases)
missing     -                    0
==========  ===================  =====================================================

Responsibilities: TF-IDF cosine between the responsibility and each resume
bullet, after replacing skill mentions with canonical tokens and adding a
token for each hierarchy parent (so a PostgreSQL bullet also counts as
"relational databases"). Credit is min(1, best / ALIGNMENT_FULL_SIMILARITY);
matched at >= ALIGNMENT_FULL_SIMILARITY, partial at >= ALIGNMENT_PARTIAL_SIMILARITY.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from app.jd.models import JDRequirements, SkillRequirement
from app.jd.taxonomy import Taxonomy, get_taxonomy, term_key
from app.latex.models import ParsedResume
from app.latex.parser import latex_to_plain
from app.latex.units import extract_units
from app.matching.models import RequirementKind, RequirementMatch, SkillMatchType

__all__ = [
    "ALIGNMENT_FULL_SIMILARITY",
    "ALIGNMENT_PARTIAL_SIMILARITY",
    "FUZZY_CREDIT",
    "HIERARCHY_CREDIT",
    "TYPO_THRESHOLD",
    "MatchResult",
    "match_requirements",
]

FUZZY_CREDIT = 0.8
HIERARCHY_CREDIT = 0.5
TYPO_THRESHOLD = 90.0
ALIGNMENT_FULL_SIMILARITY = 0.35
"""A responsibility whose best-matching bullet reaches this TF-IDF cosine gets
full credit; below it, credit is proportional. Short texts rarely exceed
~0.5 cosine even when they describe the same work, so 1.0 would be unreachable."""
ALIGNMENT_PARTIAL_SIMILARITY = 0.15


# --------------------------------------------------------------------------
# Resume view
# --------------------------------------------------------------------------


@dataclass
class _Item:
    """A bullet (with its prompt id) or other sentence-like resume text."""

    id: str | None
    text: str
    skills: set[str]


@dataclass
class _Resume:
    text: str
    lines: list[str]
    tokens: list[str]
    items: list[_Item]
    found: dict[str, tuple[SkillMatchType, str]] = field(default_factory=dict)


def _build_resume(resume: ParsedResume, taxonomy: Taxonomy) -> _Resume:
    text = "\n".join(latex_to_plain(seg.text) for seg in resume.segments).strip()
    lines = [line for line in text.splitlines() if line.strip()]
    try:
        units = extract_units(resume)
        bullets = [(b.id, b.text) for e in units.entries for b in e.bullets]
    except ValueError:  # overlapping spans: fall back to the parser's bullets
        bullets = [
            (None, b.text) for s in resume.sections if s.content for b in s.content.all_bullets
        ]
    unit_texts = {t for _, t in bullets}
    summaries = [
        (None, s.content.text)
        for s in resume.sections_of("summary")
        if s.content and s.content.text and s.content.text not in unit_texts
    ]
    pairs = bullets + summaries
    if not pairs:  # unparsed or bullet-less resumes: fall back to sentence-like lines
        pairs = [(None, line) for line in lines if len(line.split()) >= 4]
    items = [_Item(i, t, {m.name for m in taxonomy.find_skills(t)}) for i, t in pairs]

    found: dict[str, tuple[SkillMatchType, str]] = {}
    for m in taxonomy.find_skills(text):
        match_type: SkillMatchType = "exact" if term_key(m.surface) == term_key(m.name) else "alias"
        previous = found.get(m.name)
        if previous is None or (previous[0] == "alias" and match_type == "exact"):
            found[m.name] = (match_type, m.surface)
    return _Resume(
        text=text,
        lines=lines,
        tokens=re.findall(r"[A-Za-z0-9][A-Za-z0-9+#./-]*", text),
        items=items,
        found=found,
    )


# --------------------------------------------------------------------------
# Tokenising / stemming (shared by fuzzy matching and TF-IDF)
# --------------------------------------------------------------------------

_SUFFIXES = ("izations", "ization", "ations", "ation", "izing", "ized", "ing", "ed", "es", "s")
_IRREGULAR = {"built": "build", "led": "lead", "ran": "run", "wrote": "write", "written": "write"}
_STOP_WORDS = ENGLISH_STOP_WORDS | {"etc", "including", "using", "use", "via", "w"}


def stem(word: str) -> str:
    """Tiny truncation stemmer: strip a common suffix, keep at most 6 chars.
    design/designed/designing -> design; service/services -> servic;
    reliable/reliability -> reliab."""
    word = _IRREGULAR.get(word, word)
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            word = word[: -len(suffix)]
            break
    return word[:6]


def content_stems(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return [stem(w) for w in words if w not in _STOP_WORDS and len(w) > 1]


def _skill_token(name: str) -> str:
    return "sk_" + re.sub(r"\W+", "_", name.lower())


def similarity_doc(text: str, taxonomy: Taxonomy) -> str:
    """Stemmed tokens, with skill mentions replaced by one canonical token so
    'k8s' and 'Kubernetes' (or 'Postgres' and 'PostgreSQL') are the same term,
    plus a token per hierarchy parent ('PostgreSQL' also yields
    'relational databases')."""
    parts, last = [], 0
    for m in taxonomy.find_skills(text):
        if m.start < last:
            continue  # implied skill sharing the span
        parts.append(" ".join(content_stems(text[last : m.start])))
        tokens = [_skill_token(m.name)] + [
            _skill_token(p) for p in sorted(taxonomy.ancestors(m.name))
        ]
        parts.append(" ".join(tokens))
        last = m.end
    parts.append(" ".join(content_stems(text[last:])))
    return " ".join(p for p in parts if p)


# --------------------------------------------------------------------------
# Skills
# --------------------------------------------------------------------------


def _fuzzy_match(skill: str, resume: _Resume, taxonomy: Taxonomy) -> str | None:
    """Return resume evidence for a fuzzy match of ``skill``, or None."""
    info = taxonomy.skills.get(skill)
    terms = [skill, *(info.aliases if info else [])]
    if info:
        terms = [t for t in terms if t not in info.case_sensitive]

    token_keys = [(tok, term_key(tok)) for tok in resume.tokens]
    for term in terms:
        key = term_key(term)
        # Version suffix: Python3, Vue3, HTML5, Angular2.
        for tok, tok_key in token_keys:
            if tok_key != key and re.fullmatch(re.escape(key) + r"v?\d+(?:\.\d+)*", tok_key):
                return tok
        # Typo: same number of words, high similarity.
        if len(key) >= 5:
            n = len(term.split())
            words = resume.tokens
            for i in range(len(words) - n + 1):
                gram = " ".join(words[i : i + n])
                if fuzz.ratio(key, term_key(gram)) >= TYPO_THRESHOLD:
                    return gram
        # Token level: every content word of a multi-word term in one line.
        stems = set(content_stems(term))
        if len(stems) >= 2:
            for line in resume.lines:
                if stems <= set(content_stems(line)):
                    return line if len(line) <= 80 else line[:77] + "..."
    return None


def _clip(text: str, limit: int = 160) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _evidence_for(skills: set[str], surface: str, resume: _Resume) -> tuple[str | None, list[str]]:
    """Best quote for a skill: the bullets mentioning it, else the resume line."""
    items = [it for it in resume.items if it.skills & skills]
    if items:
        return _clip(items[0].text), [it.id for it in items if it.id][:3]
    low = surface.lower()
    line = next((ln for ln in resume.lines if low in ln.lower()), None)
    return (_clip(line.strip()) if line else None), []


def _match_skill(
    skill: SkillRequirement, kind: RequirementKind, resume: _Resume, taxonomy: Taxonomy
) -> RequirementMatch:
    name = skill.name
    if name in resume.found:
        match_type, surface = resume.found[name]
        evidence, ids = _evidence_for({name}, surface, resume)
        reason = (
            f"The resume names {name}."
            if match_type == "exact"
            else f"The resume says '{surface}', which counts as {name}."
        )
        return RequirementMatch(
            requirement=name, kind=kind, status="matched", evidence=evidence, reason=reason,
            credit=1.0, bullet_ids=ids, match_type=match_type, resume_term=surface,
        )  # fmt: skip

    fuzzy = _fuzzy_match(name, resume, taxonomy)
    if fuzzy is not None:
        return RequirementMatch(
            requirement=name, kind=kind, status="matched", evidence=fuzzy,
            reason=f"Close variant of {name} in the resume: '{fuzzy}'.",
            credit=FUZZY_CREDIT, match_type="fuzzy", resume_term=fuzzy,
        )  # fmt: skip

    children = taxonomy.supporting(name, set(resume.found))
    if children:
        evidence, ids = _evidence_for(set(children), resume.found[children[0]][1], resume)
        shown = ", ".join(children[:3])
        return RequirementMatch(
            requirement=name, kind=kind, status="partially_matched", evidence=evidence,
            reason=f"Not named, but the resume shows {shown}, which is a kind of {name}.",
            credit=HIERARCHY_CREDIT, bullet_ids=ids, match_type="related", resume_term=shown,
        )  # fmt: skip

    return RequirementMatch(
        requirement=name, kind=kind, status="missing",
        reason=f"No mention of {name} or of anything more specific that implies it.",
        credit=0.0,
    )  # fmt: skip


# --------------------------------------------------------------------------
# Responsibilities
# --------------------------------------------------------------------------


def _readable_overlap(resp_doc: str, item_doc: str, item_text: str) -> list[str]:
    shared = set(resp_doc.split()) & set(item_doc.split())
    words: list[str] = []
    for token in sorted(t for t in shared if t.startswith("sk_")):
        words.append(token[3:].replace("_", " "))
    for word in re.findall(r"[A-Za-z][A-Za-z0-9-]+", item_text):
        if stem(word.lower()) in shared and word.lower() not in _STOP_WORDS:
            if word.lower() not in (w.lower() for w in words):
                words.append(word.lower())
    return words[:6]


def _match_responsibilities(
    responsibilities: list[str], resume: _Resume, taxonomy: Taxonomy
) -> list[RequirementMatch]:
    if not responsibilities:
        return []
    resp_docs = [similarity_doc(r, taxonomy) for r in responsibilities]
    item_docs = [similarity_doc(it.text, taxonomy) for it in resume.items]
    if not any(item_docs) or not any(resp_docs):
        return [
            RequirementMatch(
                requirement=r,
                kind="responsibility",
                status="missing",
                reason="The resume has no bullets to compare with.",
                credit=0.0,
            )  # fmt: skip
            for r in responsibilities
        ]
    vectorizer = TfidfVectorizer(
        tokenizer=str.split, token_pattern=None, lowercase=False,
        ngram_range=(1, 2), sublinear_tf=True,
    )  # fmt: skip
    matrix = vectorizer.fit_transform(resp_docs + item_docs)
    sims = cosine_similarity(matrix[: len(resp_docs)], matrix[len(resp_docs) :])

    out: list[RequirementMatch] = []
    for r_index, responsibility in enumerate(responsibilities):
        row = sims[r_index]
        order = sorted(range(len(resume.items)), key=lambda i: -row[i])
        best = order[0]
        score = float(row[best])
        ids = [
            resume.items[i].id
            for i in order[:3]
            if row[i] >= ALIGNMENT_PARTIAL_SIMILARITY and resume.items[i].id
        ]
        overlap = _readable_overlap(resp_docs[r_index], item_docs[best], resume.items[best].text)
        where = resume.items[best].id or "a resume line"
        if score >= ALIGNMENT_FULL_SIMILARITY:
            status, reason = "matched", f"{where} describes this work"
        elif score >= ALIGNMENT_PARTIAL_SIMILARITY:
            status, reason = "partially_matched", f"{where} is related"
        else:
            status, reason = "missing", "No bullet describes this work"
        if overlap and status != "missing":
            reason += f" (shared terms: {', '.join(overlap)})"
        out.append(
            RequirementMatch(
                requirement=responsibility,
                kind="responsibility",
                status=status,  # type: ignore[arg-type]
                evidence=_clip(resume.items[best].text) if status != "missing" else None,
                reason=reason + f"; similarity {score:.2f}.",
                credit=round(min(1.0, score / ALIGNMENT_FULL_SIMILARITY), 4),
                bullet_ids=ids,
            )
        )
    return out


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


@dataclass
class MatchResult:
    mandatory: list[RequirementMatch]
    preferred: list[RequirementMatch]
    responsibilities: list[RequirementMatch]
    bullet_count: int

    @property
    def requirements(self) -> list[RequirementMatch]:
        return self.mandatory + self.preferred + self.responsibilities


def match_requirements(
    resume: ParsedResume, jd: JDRequirements, taxonomy: Taxonomy | None = None
) -> MatchResult:
    taxonomy = taxonomy or get_taxonomy()
    r = _build_resume(resume, taxonomy)
    return MatchResult(
        mandatory=[_match_skill(s, "mandatory_skill", r, taxonomy) for s in jd.mandatory_skills],
        preferred=[_match_skill(s, "preferred_skill", r, taxonomy) for s in jd.preferred_skills],
        responsibilities=_match_responsibilities(jd.responsibilities, r, taxonomy),
        bullet_count=len(r.items),
    )
