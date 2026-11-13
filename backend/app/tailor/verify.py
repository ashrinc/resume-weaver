"""Layer B: semantic verification of rewrites (M11.2, prompt verify-v1).

Only rewrites that passed Layer A are sent, in one extra LLM call, with the
plain text of the original resume body. For each the verifier returns a
verdict and an exact evidence quote. The verdict is never trusted on its own:

* the evidence must occur in the original resume (case- and
  whitespace-insensitive, quotes and dashes normalised), or the rewrite is
  treated as unsupported;
* supported -> keep; partially_supported -> keep only if the unsupported part
  is wording only; unsupported or no verdict -> revert.

Since M11.3 the call only runs when ENABLE_VERIFY_CALL is true (default
false: it roughly doubles latency and quota). ``skip_verification`` handles
the disabled case like an unavailable verifier.

If the call fails (rate limit, timeout, bad output), Layer-A-passing
rewrites are kept and a warning says semantic verification was unavailable,
except rewrites naming a practice the resume never names (taxonomy
``concept``), which only the verifier can accept.
"""

from __future__ import annotations

import logging
import re
from typing import Protocol

from app.llm.errors import GeminiError
from app.llm.models import VerificationResponse, VerificationVerdict
from app.llm.prompts import VerifyItem
from app.tailor.apply import Judgement, resume_body_text

__all__ = [
    "MIN_EVIDENCE_CHARS",
    "Verifier",
    "evidence_in_resume",
    "skip_verification",
    "verify_rewrites",
]

logger = logging.getLogger(__name__)

MIN_EVIDENCE_CHARS = 4

_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"',
                         "–": "-", "—": "-", "−": "-"})  # fmt: skip


class Verifier(Protocol):
    async def verify_rewrites(
        self, resume_text: str, items: list[VerifyItem]
    ) -> VerificationResponse: ...


def _normalise(text: str) -> str:
    text = text.translate(_QUOTES).casefold()
    text = re.sub(r"\\([%&$#_])", r"\1", text)  # tolerate LaTeX escapes in quotes
    return " ".join(text.split())


def evidence_in_resume(evidence: str, resume_text: str) -> bool:
    quote = _normalise(evidence).strip(" .,;:'\"…")
    quote = quote.removesuffix("...").strip()
    return len(quote) >= MIN_EVIDENCE_CHARS and quote in _normalise(resume_text)


def _decide(v: VerificationVerdict | None, resume_text: str) -> str | None:
    """Revert reason, or None to keep the rewrite."""
    if v is None:
        return "semantic check: the verifier returned no verdict"
    problem = v.problem.strip()
    if v.verdict == "unsupported":
        return "semantic check: unsupported" + (f": {problem}" if problem else "")
    if not evidence_in_resume(v.evidence, resume_text):
        return "semantic check: the cited evidence is not in the original resume"
    if v.verdict == "partially_supported" and not v.wording_only:
        return "semantic check: partially supported" + (f": {problem}" if problem else "")
    return None


def _revert_unverified_concepts(j: Judgement, why: str) -> None:
    """Practices the resume never names need the verifier: without it, revert."""
    for o in j.reworded():
        if o.concepts:
            names = ", ".join(f'"{c}"' for c in sorted(o.concepts))
            j.revert(o.unit.id, f"unverified practice: {names} {why}")


def skip_verification(j: Judgement) -> None:
    """ENABLE_VERIFY_CALL is off: no second LLM call. Layer A results stand,
    except practices the resume never names, which only the verifier can accept."""
    if not j.reworded():
        j.verification = "not_needed"
        return
    j.verification = "disabled"
    _revert_unverified_concepts(j, "needs the semantic check (ENABLE_VERIFY_CALL is off)")


async def verify_rewrites(j: Judgement, verifier: Verifier) -> None:
    """Run Layer B on the judgement's reworded bullets, reverting in place."""
    items = j.pending_verification()
    if not items:
        j.verification = "not_needed"
        return
    resume_text = resume_body_text(j.resume)
    try:
        response = await verifier.verify_rewrites(resume_text, items)
    except GeminiError as exc:
        logger.warning("semantic verification unavailable (%s)", type(exc).__name__)
        j.verification = "unavailable"
        j.warnings.append(
            f"Semantic verification unavailable ({type(exc).__name__}); "
            f"{len(items)} rewrite(s) were checked by the deterministic guards only."
        )
        _revert_unverified_concepts(j, "needs the semantic check")
        return

    verdicts: dict[str, VerificationVerdict] = {}
    for v in response.results:
        verdicts.setdefault(v.id.strip(), v)
    j.verification = "ran"
    for item in items:
        reason = _decide(verdicts.get(item.id), resume_text)
        if reason:
            j.revert(item.id, reason)
            j.semantic_reverts += 1
