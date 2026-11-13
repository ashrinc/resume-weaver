"""Manual end-to-end test against the REAL Gemini API (M11). Not part of pytest.

Runs the full tailoring pipeline (parse -> analyze JD -> Gemini -> reassemble
-> validate -> score) on scripts/fixtures/resume.tex and
scripts/fixtures/job_description.txt, with nothing mocked.

Usage (from backend/, with GEMINI_API_KEY in backend/.env):

    uv run python -I scripts/test_real_gemini.py
    GEMINI_MODEL=gemini-1.5-flash uv run python -I scripts/test_real_gemini.py

Exit codes: 0 success, 1 Gemini/config error. The full tailored LaTeX is
written to scripts/output/tailored_resume.tex (git-ignored). The API key is
never printed.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
# `python -I` puts neither the script's directory nor the cwd on sys.path.
sys.path.insert(0, str(BACKEND_DIR))

from app.config import get_settings  # noqa: E402
from app.llm.client import GeminiClient  # noqa: E402
from app.llm.errors import (  # noqa: E402
    GeminiConfigError,
    GeminiError,
    GeminiRateLimitError,
    GeminiResponseError,
    GeminiTimeoutError,
)
from app.llm.prompts import PROMPT_VERSION  # noqa: E402
from app.tailor.pipeline import PipelineInputError, TailorPipeline  # noqa: E402

FIXTURES = BACKEND_DIR / "scripts" / "fixtures"
OUTPUT = BACKEND_DIR / "scripts" / "output" / "tailored_resume.tex"
STAGES = [
    ("parse", "Parse LaTeX"),
    ("analyze_jd", "Analyze JD"),
    ("score_original", "Score original"),
    ("gemini", "Tailor (LLM call 1)"),
    ("verify", "Verify (call 2, if on)"),
    ("assemble_validate", "Reassemble + validate"),
    ("score_tailored", "Score tailored"),
    ("total", "TOTAL"),
]


def rule(title: str) -> None:
    print(f"\n{'=' * 8} {title} {'=' * max(4, 62 - len(title))}")


def explain(exc: GeminiError, model: str) -> str:
    if isinstance(exc, GeminiConfigError):
        return f"Configuration problem: {exc}"
    if isinstance(exc, GeminiTimeoutError):
        return f"Timeout: {exc}"
    if isinstance(exc, GeminiRateLimitError):
        wait = f" Gemini suggests waiting {exc.retry_after:g}s." if exc.retry_after else ""
        return f"Rate limited after retries (free-tier quota?).{wait}"
    if isinstance(exc, GeminiResponseError):
        hint = {
            400: "Bad request: often an invalid API key (API_KEY_INVALID) or unsupported field.",
            401: "Unauthenticated: check GEMINI_API_KEY in backend/.env.",
            403: "Permission denied: the key may be restricted or the API not enabled.",
            404: f"Model '{model}' is not available to this key (retired or misspelt). "
            "Set GEMINI_MODEL to a current model, e.g. gemini-flash-latest.",
        }.get(exc.status_code or 0, "")
        return f"{exc}{(' ' + hint) if hint else ''}"
    return str(exc)


async def main() -> int:
    settings = get_settings()
    model = settings.gemini_model
    latex = (FIXTURES / "resume.tex").read_text(encoding="utf-8")
    jd = (FIXTURES / "job_description.txt").read_text(encoding="utf-8")

    rule("Setup")
    print(f"model           : {model}")
    print(f"prompt version  : {PROMPT_VERSION}")
    verify = "on" if settings.enable_verify_call else "off"
    print(f"verify call     : {verify} (ENABLE_VERIFY_CALL)")
    print(f"api key present : {bool(settings.gemini_api_key.strip())}")
    print(f"inputs          : resume {len(latex):,} chars, JD {len(jd):,} chars")

    pipeline = TailorPipeline(client_factory=GeminiClient, model_name=model)
    try:
        result = await pipeline.run(latex, jd)
    except GeminiError as exc:
        rule("Gemini error")
        print(f"{type(exc).__name__}: {explain(exc, model)}")
        return 1
    except PipelineInputError as exc:
        rule("Input error")
        print(exc)
        return 1

    rule("Stage timings")
    for key, label in STAGES:
        ms = result.timings_ms.get(key)
        if ms is not None:
            print(f"{label:<24}{ms:>10,.1f} ms")

    rule("Scores")
    print(f"Programmatic ATS: before {result.original_programmatic_score:.1f} "
          f"-> after {result.programmatic_score.total:.1f}")  # fmt: skip
    print(f"LLM score       : {result.llm_score.score}/100")
    print(f"LLM reasoning   : {result.llm_score.reasoning}")
    for c in result.programmatic_score.criteria:
        pts = f"{c.points:.1f}/{c.max_points:g}" if c.applicable else "n/a"
        print(f"  - {c.label:<40}{pts:>10}   {c.details}")
    missing = [m.skill for m in result.programmatic_score.missing_keywords if m.priority == "high"]
    print(f"Missing mandatory: {', '.join(missing) or 'none'}")

    rule("LLM usage")
    print(f"provider / model : {result.provider} / {result.model}")
    print(f"HTTP requests    : {result.llm_requests} (incl. retries and repairs)")
    print(f"verification     : {result.content_check.verification}")

    rule("Content preservation (M11.1)")
    check = result.content_check
    print(f"{'entry':<8}{'label':<34}{'in':>4}{'out':>5}")
    for e in check.entries:
        flag = "" if e.before == e.after else "   <-- MISMATCH"
        print(f"{e.entry_id:<8}{e.label[:33]:<34}{e.before:>4}{e.after:>5}{flag}")
    print(f"skills: {check.skills_before} in -> {check.skills_after} out")
    print(
        f"reworded {check.reworded}, unchanged {check.unchanged}, reverted {check.reverted}, "
        f"restored {check.restored}, discarded ids {check.discarded}, "
        f"entries reordered {check.reordered_entries}"
    )

    rule("Reverts and restores")
    for c in result.bullet_changes:
        if c.status in ("reverted", "restored"):
            print(f"- {c.status} {c.id} ({c.label}): {c.reason}")
            if c.proposed:
                print(f"    rejected: {c.proposed}")
    if not any(c.status in ("reverted", "restored") for c in result.bullet_changes):
        print("(none)")

    rule("Before / after (reworded bullets)")
    for c in [c for c in result.bullet_changes if c.status == "reworded"]:
        print(f"{c.id} ({c.label})  reason: {c.reason}")
        print(f"  before: {c.before}")
        print(f"  after : {c.after}")

    rule("Requirement matches (final resume)")
    for m in result.requirement_matches:
        kind = {"mandatory_skill": "req", "preferred_skill": "pref", "responsibility": "resp"}
        ids = ",".join(m.bullet_ids)
        print(f"{m.status:<18}{kind[m.kind]:<5}{m.requirement[:58]:<60}{ids}")

    rule("Suggestions")
    for g in result.suggestions.surface:
        print(f"surface: {g.requirement}: {g.detail}")
    for g in result.suggestions.missing:
        print(f"missing: {g.requirement}")

    rule("changes_summary (computed by the backend)")
    for item in result.changes_summary or ["(empty)"]:
        print(f"- {item}")

    rule("Model's notes")
    for item in result.model_notes or ["(none)"]:
        print(f"- {item}")

    rule("Validation & warnings")
    print(f"validation errors  : {len(result.validation.errors)}")
    for issue in result.validation.errors + result.validation.warnings:
        print(f"  line {issue.line}: {issue.message}")
    print(f"pipeline warnings  : {len(result.warnings)}")
    for w in result.warnings:
        print(f"  - {w}")

    rule("Tailored body (bullets and skills)")
    body = result.latex[result.latex.index("\\begin{document}") :]
    for line in body.splitlines():
        if "\\resumeItem{" in line or "\\textbf{" in line and "}{:" in line:
            print(f"  {line.strip()}")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(result.latex, encoding="utf-8")
    rule("Done")
    print(f"full output written to {OUTPUT.relative_to(BACKEND_DIR)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
