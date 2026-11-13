"""Layer A: deterministic hard guards for rewrites (M11.1, reworked in M11.2).

Core rule: the tailor may reword, reorder and emphasize, but must never
delete content or add facts. Layer A catches the mechanical violations; the
semantic verifier (Layer B, ``verify.py``) judges meaning. Checks run against
the WHOLE original resume (``ResumeContext``), not just the one bullet, so a
rewrite may use any fact the resume states, while the verifier decides
whether it belongs in that bullet.

Every problem is named by its rule, e.g. ``new number: "45" is not in ...``:

* ``LaTeX``: structural commands, new unescaped special characters, braces.
* ``new number``: a number not anywhere in the original resume.
* ``lost number``: a number of the original bullet missing from the rewrite.
* ``unsupported tool``: a named taxonomy tool/platform the resume does not
  mention, directly or through the hierarchy (PostgreSQL supports
  "relational databases"; it never supports MongoDB). Practices marked
  ``concept`` in the taxonomy ("query optimization" for "tuned slow queries")
  are not tools: when the resume doesn't name them, Layer A passes them on to
  the verifier (``unverified_concepts``), and they are reverted if the
  verifier is unavailable.
* ``vaguer scope``: a named tool or proper noun of the original bullet
  replaced by something vaguer ("Vercel and Render" -> "frontend and backend
  deployments").
* ``inflated claim``: certainty/scope escalation: prevent/reduce ->
  eliminate, some -> all, contributed/assisted -> led/owned.
* ``merge`` (in ``apply.py``): the rewrite absorbed another bullet.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from app.jd.taxonomy import get_taxonomy
from app.latex.parser import latex_to_plain, mask_latex, parse_resume
from app.latex.validator import validate_latex

__all__ = [
    "ResumeContext",
    "bullet_problem",
    "content_words",
    "escalation",
    "merged_into",
    "name_tokens",
    "numbers",
    "plain",
    "skill_items",
    "skills_problem",
    "tech_terms",
    "unverified_concepts",
]

# --------------------------------------------------------------------------
# Text features
# --------------------------------------------------------------------------

_NUMBER_WORDS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9", "ten": "10", "eleven": "11",
    "twelve": "12", "dozen": "12", "hundred": "100", "thousand": "1000",
    "half": "0.5", "double": "2", "twice": "2", "triple": "3",
}  # fmt: skip
_NUMBER_WORD_RE = re.compile(r"\b(" + "|".join(_NUMBER_WORDS) + r")\b", re.IGNORECASE)
# Digits not glued to letters on the left (p95 and EC2 are name tokens instead).
_NUMBER_RE = re.compile(r"(?<![A-Za-z\d.,])\d[\d,]*(?:\.\d+)?")
_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+#/&'-]*[A-Za-z0-9+#]|[A-Za-z0-9]")
_SENTENCE_BREAK_RE = re.compile(r"[.:;!?(\u2013\u2014]\s*$|^\s*$")
_STOPWORDS = frozenset(
    "a an and the of to in on for with by from at as into using via over under that this "
    "these those their its was were be been is are it or not but also our".split()
)
# Generic acronyms that are not names of anything: rewording them away is fine.
_GENERIC_NAMES = frozenset({"api", "apis", "ui", "ux", "id", "ids", "url", "urls", "app", "apps"})


def plain(latex: str) -> str:
    return " ".join(latex_to_plain(latex).split())


def numbers(text: str) -> Counter[str]:
    """Numeric values in plain text, normalised: '2,000+' -> '2000',
    'three' -> '3'. Digits glued to letters (p95, EC2, k8s) are excluded here
    and covered by ``name_tokens``."""
    text = _NUMBER_WORD_RE.sub(lambda m: _NUMBER_WORDS[m.group(1).lower()], text)
    out: Counter[str] = Counter()
    for m in _NUMBER_RE.finditer(text):
        value = m.group(0).rstrip(",").replace(",", "")
        if "." in value:
            value = value.rstrip("0").rstrip(".")
        out[value] += 1
    return out


def name_tokens(text: str) -> set[str]:
    """Words that name something: containing an uppercase letter (not just as
    the first letter of a sentence) or mixing letters and digits. Returned
    case-folded."""
    out: set[str] = set()
    for m in _WORD_RE.finditer(text):
        word = m.group(0)
        has_upper = any(c.isupper() for c in word)
        mixed = any(c.isdigit() for c in word) and any(c.isalpha() for c in word)
        if not (has_upper or mixed):
            continue
        sentence_start = _SENTENCE_BREAK_RE.search(text[: m.start()]) is not None
        title_case = word[0].isupper() and sum(c.isupper() for c in word) == 1
        if sentence_start and title_case and not mixed:
            continue  # "Built", "Reduced": capitalized only because they start a sentence
        out.add(word.casefold())
    return out


def tech_terms(text: str) -> set[str]:
    """Canonical names of taxonomy technologies mentioned in ``text``."""
    taxonomy = get_taxonomy()
    return {m.name for m in taxonomy.find_skills(text) if taxonomy.skills[m.name].type == "tech"}


def content_words(text: str) -> set[str]:
    return {
        w.casefold()
        for w in _WORD_RE.findall(text)
        if len(w) >= 4 and w.casefold() not in _STOPWORDS and not w.isdigit()
    }


def _words(text: str) -> set[str]:
    return {w.casefold() for w in _WORD_RE.findall(text)}


def _singular(word: str) -> str:
    return word[:-1] if word.endswith("s") and len(word) > 3 else word


@dataclass(frozen=True)
class ResumeContext:
    """What the whole original resume supports."""

    text: str
    numbers: frozenset[str]
    tech: frozenset[str]  # mentioned directly (incl. aliases and implied skills)
    supported_tech: frozenset[str]  # tech + hierarchy ancestors
    words: frozenset[str]

    @classmethod
    def from_text(cls, text: str) -> ResumeContext:
        taxonomy = get_taxonomy()
        tech = tech_terms(text)
        supported = set(tech)
        for name in tech:
            supported |= taxonomy.ancestors(name)
        return cls(
            text=text,
            numbers=frozenset(numbers(text)),
            tech=frozenset(tech),
            supported_tech=frozenset(supported),
            words=frozenset(_words(text)),
        )


def _quote(items: set[str] | list[str]) -> str:
    return ", ".join(f'"{i}"' for i in sorted(items))


# --------------------------------------------------------------------------
# Certainty / scope escalation
# --------------------------------------------------------------------------

# (label, strong claim, the weaker wordings it must not replace)
_ESCALATIONS: list[tuple[str, re.Pattern[str], re.Pattern[str]]] = [
    (label, re.compile(strong, re.IGNORECASE), re.compile(weak, re.IGNORECASE))
    for label, strong, weak in [
        ("eliminate", r"\beliminat\w*", r"\b(?:prevent|reduc|cut|lower|minimi|mitigat)\w*"),
        (
            "all",
            r"\b(?:all|every|entire(?:ly)?)\b",
            r"\b(?:some|several|many|most|multiple|part)\b",
        ),
        (
            "led/owned",
            r"\b(?:led|lead|leads|leading|own|owned|owns|owning|spearhead\w*|headed|drove)\b",
            r"\b(?:contribut|assist|help|support|participat)\w*",
        ),
    ]
]


def escalation(original: str, rewritten: str) -> str | None:
    """'"prevent" became "eliminate"' if the rewrite escalates certainty or
    scope from a weaker wording in the original."""
    for _label, strong, weak in _ESCALATIONS:
        if strong.search(original):
            continue
        new = strong.search(rewritten)
        source = weak.search(original)
        if new and source:
            return f'"{source.group(0)}" became "{new.group(0)}"'
    return None


# --------------------------------------------------------------------------
# Bullet checks
# --------------------------------------------------------------------------

_FORBIDDEN_IN_BULLET_RE = re.compile(
    r"\\(?:item|resumeItem|resumeSubheading|resumeSubSubheading|resumeProjectHeading"
    r"|cventry|section|subsection|begin|end|documentclass|usepackage|newcommand"
    r"|renewcommand|providecommand|def|input|include)(?![a-zA-Z])"
    r"|\\[a-zA-Z]*List(?:Start|End)(?![a-zA-Z])"
)
_UNESCAPED_SPECIAL_RE = re.compile(r"(?<!\\)[%&#_]")


def _structural_errors(latex: str) -> int:
    return sum(1 for e in validate_latex(latex).errors if not e.code.startswith("missing_"))


def _latex_problem(original: str, rewritten: str) -> str | None:
    if not rewritten.strip():
        return "LaTeX: the rewrite was empty"
    if _FORBIDDEN_IN_BULLET_RE.search(mask_latex(rewritten)):
        return "LaTeX: the rewrite contained structural commands"
    specials = set(_UNESCAPED_SPECIAL_RE.findall(rewritten))
    if specials - set(_UNESCAPED_SPECIAL_RE.findall(original)):
        return f"LaTeX: unescaped special characters ({' '.join(sorted(specials))})"
    if _structural_errors(rewritten) > _structural_errors(original):
        return "LaTeX: unbalanced braces"
    return None


def fact_problem(original: str, rewritten: str, ctx: ResumeContext) -> str | None:
    """Plain-text Layer A fact checks (see the module docstring)."""
    new_numbers = set(numbers(rewritten)) - ctx.numbers
    if new_numbers:
        return f"new number: {_quote(new_numbers)} is not in the original resume"
    lost = set((numbers(original) - numbers(rewritten)).keys())
    if lost:
        return f"lost number: dropped {_quote(lost)} from the original bullet"

    taxonomy = get_taxonomy()
    unsupported = {
        t for t in tech_terms(rewritten) - ctx.supported_tech if not taxonomy.skills[t].concept
    }
    if unsupported:
        return f"unsupported tool: {_quote(unsupported)} is not in the original resume"

    lost_tech = tech_terms(original) - tech_terms(rewritten)
    if lost_tech:
        return f"vaguer scope: dropped {_quote(lost_tech)} from the original bullet"
    rewritten_words = {_singular(w) for w in _words(rewritten)}
    # Words of a tool mention that survives in another form ("REST" -> "RESTful")
    # are not lost.
    kept = tech_terms(rewritten)
    covered = {
        w.casefold()
        for m in get_taxonomy().find_skills(original)
        if m.name in kept
        for w in _WORD_RE.findall(m.surface)
    }
    lost_names = {
        t
        for t in name_tokens(original) - _GENERIC_NAMES - covered
        if _singular(t) not in rewritten_words
    }
    if lost_names:
        return f"vaguer scope: dropped {_quote(lost_names)} from the original bullet"

    inflated = escalation(original, rewritten)
    if inflated:
        return f"inflated claim: {inflated}"
    return None


def unverified_concepts(rewritten_latex: str, ctx: ResumeContext) -> set[str]:
    """Practices the rewrite names that the resume never names: only the
    semantic verifier can accept them."""
    taxonomy = get_taxonomy()
    return {
        t
        for t in tech_terms(plain(rewritten_latex)) - ctx.supported_tech
        if taxonomy.skills[t].concept
    }


def bullet_problem(original_latex: str, rewritten_latex: str, ctx: ResumeContext) -> str | None:
    """Why a rewritten bullet must be reverted (rule-named), or None."""
    problem = _latex_problem(original_latex, rewritten_latex)
    if problem:
        return problem
    return fact_problem(plain(original_latex), plain(rewritten_latex), ctx)


def merged_into(rewritten: str, own_original: str, other_original: str) -> bool:
    """True if the rewrite absorbed most of another bullet's distinctive content."""
    distinctive = content_words(other_original) - content_words(own_original)
    if len(distinctive) < 3:
        return False
    absorbed = distinctive & content_words(rewritten)
    return len(absorbed) / len(distinctive) >= 0.6


# --------------------------------------------------------------------------
# Skills sections
# --------------------------------------------------------------------------


def skill_items(section_latex: str) -> list[str] | None:
    """Skill items of a standalone skills section (None if it doesn't parse)."""
    doc = parse_resume("\\begin{document}\n" + section_latex.strip() + "\n\\end{document}\n")
    sections = doc.sections
    if len(sections) != 1 or sections[0].content is None:
        return None
    return [s for g in sections[0].content.skill_groups for s in g.skills]


def skills_problem(original_latex: str, tailored_latex: str, ctx: ResumeContext) -> str | None:
    before = skill_items(original_latex)
    after = skill_items(tailored_latex)
    if before is None:
        return None  # nothing measurable; structure checks still apply
    if after is None:
        return "the tailored version could not be parsed"
    after_words = _words(" ".join(after))
    dropped = [s for s in before if not _words(s) <= after_words]
    if dropped:
        return f"it dropped {_quote(dropped)}"
    if len(after) < len(before):
        return f"it lists fewer skills ({len(after)} instead of {len(before)})"
    unsupported = tech_terms(" ".join(after)) - ctx.supported_tech
    if unsupported:
        return f"it added {_quote(unsupported)}, which the original resume does not support"
    added = [
        s
        for s in after
        if not _words(s) <= ctx.words
        and not (tech_terms(s) and tech_terms(s) <= ctx.supported_tech)
    ]
    if added:
        return f"it added {_quote(added)}, which the original resume does not mention"
    return None
