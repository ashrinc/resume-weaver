"""Resume tailoring pipeline: orchestrates M1-M5 (PRD FR-02 to FR-08).

1. Parse the LaTeX resume (M2) -> preamble + body segments.
2. Analyze the JD (M3) -> weighted requirements. The original resume is also
   scored (M4) so the response can show before/after.
3. Call the LLM (M5) with the bullets, the JD analysis and the requirement
   matching plan of the original (``app/matching``).
4. Layer A (``apply.judge``): deterministic guards on every rewrite; restore
   omitted bullets. Layer B (``verify.py``): a second LLM call checks the
   surviving rewrites against the original resume; unverifiable ones are
   reverted. Then ``apply.finalize`` splices each bullet into its original
   slot and runs the final count check. The preamble, headings and raw
   segments are never touched. The changes summary is built from this diff.
5. Validate the reassembled document (M1).
6. Score the FINAL resume (after every revert and restore) against the JD,
   including requirement matches and the two suggestion groups.
7. Warn (never trim) if the result probably runs past one page.
8. Return everything as a ``TailorResult``.

Successful results are cached in memory by a hash of PROMPT_VERSION, the
model name, the LaTeX source and the JD, so resubmitting the same inputs does
not call Gemini again. Failures are never cached. Logs record sizes, timings
and cache hits only, never the resume or JD text.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Protocol

from app.config import get_settings
from app.jd.analyzer import analyze_jd
from app.jd.models import JDRequirements
from app.latex.models import ParsedResume
from app.latex.parser import parse_resume
from app.latex.validator import validate_latex
from app.llm.client import PROVIDER
from app.llm.models import TailoringResponse, VerificationResponse
from app.llm.prompts import PROMPT_VERSION, VERIFY_PROMPT_VERSION, VerifyItem
from app.matching.models import RequirementMatch
from app.scoring.scorer import score_resume
from app.tailor.apply import filter_model_notes, finalize, judge
from app.tailor.models import LLMScore, TailorResult
from app.tailor.suggestions import build_suggestions, flatten
from app.tailor.verify import skip_verification, verify_rewrites

__all__ = [
    "PipelineInputError",
    "ResultCache",
    "TailorPipeline",
    "cache_key",
    "estimate_lines",
    "page_length_warning",
]

logger = logging.getLogger(__name__)


class PipelineInputError(Exception):
    """The input cannot be tailored (e.g. no \\begin{document}). Maps to HTTP 400.
    Messages never include the input text."""


class TailoringClient(Protocol):
    async def tailor_resume(
        self,
        resume: ParsedResume,
        jd: JDRequirements,
        keywords: list[str] | None = None,
        missing_keywords: list[str] | None = None,
        plan: list[RequirementMatch] | None = None,
    ) -> TailoringResponse: ...

    async def verify_rewrites(
        self, resume_text: str, items: list[VerifyItem]
    ) -> VerificationResponse: ...


# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------


def cache_key(latex_source: str, job_description: str, *, model: str, prompt_version: str) -> str:
    payload = json.dumps([prompt_version, model, latex_source, job_description])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ResultCache:
    """Small in-memory LRU cache of successful results (per process)."""

    def __init__(self, max_entries: int = 64) -> None:
        self.max_entries = max_entries
        self._items: OrderedDict[str, TailorResult] = OrderedDict()

    def get(self, key: str) -> TailorResult | None:
        item = self._items.get(key)
        if item is not None:
            self._items.move_to_end(key)
        return item

    def set(self, key: str, value: TailorResult) -> None:
        self._items[key] = value
        self._items.move_to_end(key)
        while len(self._items) > self.max_entries:
            self._items.popitem(last=False)

    def __len__(self) -> int:
        return len(self._items)


# --------------------------------------------------------------------------
# Page length (informational only: the tailor never trims content)
# --------------------------------------------------------------------------

# Rough capacity of one page for a Jake's-style resume (11pt, ~0.5in margins,
# \small bullets). Deliberately approximate: it only drives a warning.
PAGE_LINES = 52
_CHARS_PER_LINE = 105


def estimate_lines(resume: ParsedResume) -> int:
    lines = 4  # name + contact header
    for seg in resume.sections:
        lines += 2
        content = seg.content
        if content is None:
            continue
        lines += 2 * len(content.entries)
        lines += len(content.skill_groups)
        for b in content.all_bullets:
            lines += max(1, -(-len(b.text) // _CHARS_PER_LINE))
        if not content.entries and not content.all_bullets and not content.skill_groups:
            lines += max(1, -(-len(content.text) // _CHARS_PER_LINE))
    return lines


def page_length_warning(resume: ParsedResume) -> str | None:
    lines = estimate_lines(resume)
    if lines <= PAGE_LINES:
        return None
    return (
        f"The tailored resume may run past one page (rough estimate: {lines} lines; "
        f"about {PAGE_LINES} fit). Nothing was cut: choose what to trim yourself."
    )


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for item in items:
        key = item.strip().casefold()
        if key and key not in seen:
            seen.add(key)
            out.append(item.strip())
    return out


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------


class TailorPipeline:
    def __init__(
        self,
        client_factory: Callable[[], TailoringClient],
        *,
        model_name: str | None = None,
        cache: ResultCache | None = None,
        enable_verify: bool | None = None,
    ) -> None:
        settings = get_settings()
        self._client_factory = client_factory
        self._client: TailoringClient | None = None
        self.model_name = model_name or settings.gemini_model
        self.cache = cache if cache is not None else ResultCache()
        # ENABLE_VERIFY_CALL: the second (semantic verifier) LLM call. Off by
        # default for latency and quota; Layer A guards always run.
        self.enable_verify = settings.enable_verify_call if enable_verify is None else enable_verify

    def _get_client(self) -> TailoringClient:
        # Created lazily so the app starts (and tests run) without an API key.
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    async def run(self, latex_source: str, job_description: str) -> TailorResult:
        key = cache_key(
            latex_source,
            job_description,
            model=self.model_name,
            prompt_version=(
                f"{PROMPT_VERSION}+{VERIFY_PROMPT_VERSION if self.enable_verify else 'no-verify'}"
            ),
        )
        cached = self.cache.get(key)
        if cached is not None:
            logger.info("tailor: cache hit (resume=%d chars, jd=%d chars)",
                        len(latex_source), len(job_description))  # fmt: skip
            return cached.model_copy(update={"cached": True})

        started = time.monotonic()
        timings: dict[str, float] = {}
        mark = started

        def lap(stage: str) -> None:
            nonlocal mark
            now = time.monotonic()
            timings[stage] = round((now - mark) * 1000, 1)
            mark = now

        logger.info("tailor: start (resume=%d chars, jd=%d chars)",
                    len(latex_source), len(job_description))  # fmt: skip

        # 1. Parse.
        resume = parse_resume(latex_source)
        if not resume.has_document_body:
            raise PipelineInputError(
                "The LaTeX source has no \\begin{document}; paste the complete .tex file."
            )
        if not resume.sections:
            raise PipelineInputError(
                "No \\section headings could be parsed, so there is nothing to tailor."
            )
        warnings: list[str] = []
        input_issues = validate_latex(latex_source).errors
        if input_issues:
            warnings.append(f"The original LaTeX already had {len(input_issues)} syntax issue(s).")
        failed = [s for s in resume.segments if s.kind == "raw" and s.error and s.name]
        if failed:
            names = ", ".join(f"'{s.name}'" for s in failed)
            warnings.append(f"Could not parse {names}; passed through unchanged.")

        # 2. Analyze the JD; score the original for a before/after comparison.
        lap("parse")
        jd = analyze_jd(job_description)
        lap("analyze_jd")
        warnings.extend(f"Job description: {w}" for w in jd.warnings)
        original_score = score_resume(resume, jd)
        lap("score_original")

        # 3. Tailoring call. The original's missing JD skills go to the prompt
        # so the model doesn't invent them, and the matching plan says which
        # bullets support which requirement.
        client = self._get_client()
        requests_before = getattr(client, "request_count", None)
        not_in_resume = [m.skill for m in original_score.missing_keywords]
        response = await client.tailor_resume(
            resume,
            jd,
            missing_keywords=not_in_resume,
            plan=original_score.requirement_matches,
        )
        lap("gemini")

        # 4. Layer A, then Layer B (second call), then splice + final check.
        judgement = judge(resume, response)
        if self.enable_verify:
            await verify_rewrites(judgement, client)
        else:
            skip_verification(judgement)
        lap("verify")
        applied = finalize(judgement)
        latex = applied.latex
        warnings.extend(applied.warnings)
        requests_after = getattr(client, "request_count", None)
        llm_requests = (
            requests_after - requests_before
            if isinstance(requests_before, int) and isinstance(requests_after, int)
            else None
        )

        # 5. Validate. 6. Score.
        validation = validate_latex(latex)
        lap("assemble_validate")
        tailored = parse_resume(latex)
        length_warning = page_length_warning(tailored)
        if length_warning:
            warnings.append(length_warning)
        tailored_score = score_resume(tailored, jd)
        lap("score_tailored")
        timings["total"] = round((time.monotonic() - started) * 1000, 1)

        analysis = response.ats_analysis
        suggestions = build_suggestions(tailored_score)
        result = TailorResult(
            latex=latex,
            validation=validation,
            llm_score=LLMScore(
                score=analysis.score,
                reasoning=analysis.reasoning.strip(),
                matched_keywords=analysis.matched_keywords,
                missing_keywords=analysis.missing_keywords,
            ),
            programmatic_score=tailored_score,
            original_programmatic_score=original_score.total,
            jd_analysis=jd,
            changes_summary=applied.changes,
            model_notes=filter_model_notes(response.notes),
            content_check=applied.check,
            bullet_changes=applied.bullet_changes,
            requirement_matches=tailored_score.requirement_matches,
            suggestions=suggestions,
            improvement_summary=_dedupe(flatten(suggestions)),
            provider=PROVIDER,
            model=self.model_name,
            llm_requests=llm_requests,
            warnings=warnings,
            prompt_version=PROMPT_VERSION,
            timings_ms=timings,
        )
        self.cache.set(key, result)
        logger.info(
            "tailor: done in %.1fs (llm=%d, programmatic=%.1f, before=%.1f, "
            "validation_errors=%d, warnings=%d)",
            time.monotonic() - started, analysis.score, tailored_score.total,
            original_score.total, len(validation.errors), len(warnings),
        )  # fmt: skip
        return result
