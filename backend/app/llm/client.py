"""Async Gemini client for resume tailoring (PRD FR-06).

* Model from ``GEMINI_MODEL`` (default ``gemini-3.5-flash``), key from
  ``GEMINI_API_KEY``, both via ``app.config.get_settings()``.
* Each request has a 60 s timeout (enforced with ``asyncio.wait_for`` and also
  passed to the SDK).
* HTTP 429 and 5xx are retried with exponential backoff and jitter, up to
  ``max_retries`` (3) retries. A server-provided RetryInfo delay is honoured.
  The SDK's own retries are disabled so this is the only retry policy.
* Gemini is asked for JSON matching a pydantic schema. If the reply is not
  valid JSON or does not match the schema, the client sends one repair
  request; if that also fails, it raises ``GeminiResponseError``.
* Errors are typed (see ``app.llm.errors``). Logs contain only metadata
  (model, attempt, status, sizes); never resume or job description text.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel, ValidationError

from app.config import get_settings
from app.jd.models import JDRequirements
from app.latex.models import ParsedResume
from app.llm.errors import (
    GeminiConfigError,
    GeminiRateLimitError,
    GeminiResponseError,
    GeminiTimeoutError,
)
from app.llm.models import TailoringResponse, VerificationResponse
from app.llm.prompts import (
    VerifyItem,
    build_repair_prompt,
    build_tailoring_prompt,
    build_verify_prompt,
)
from app.matching.models import RequirementMatch

__all__ = ["PROVIDER", "GeminiClient"]

PROVIDER = "Google Gemini"

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_RETRIES = 3
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL | re.IGNORECASE)
_BLOCKING_FINISH_REASONS = {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"}


class _ParseFailure(Exception):
    """The model's text could not be turned into the schema. The message is a
    content-free description that is safe to log and to send back for repair."""


def _parse[T: BaseModel](text: str | None, schema: type[T]) -> T:
    if text is None or not text.strip():
        raise _ParseFailure("the response was empty")
    fenced = _FENCE_RE.match(text)
    if fenced:
        text = fenced.group(1)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise _ParseFailure(
            f"invalid JSON ({exc.msg} at line {exc.lineno}, column {exc.colno})"
        ) from None
    try:
        return schema.model_validate(data)
    except ValidationError as exc:
        problems = [
            f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
            for err in exc.errors()[:10]
        ]
        raise _ParseFailure("JSON does not match the schema: " + "; ".join(problems)) from None


def _retry_info_seconds(exc: genai_errors.APIError) -> float | None:
    """Extract google.rpc.RetryInfo.retryDelay (e.g. '17s') if present."""
    details: Any = getattr(exc, "details", None)
    if isinstance(details, dict):
        details = details.get("error", details).get("details", [])
    if not isinstance(details, list):
        return None
    for item in details:
        delay = item.get("retryDelay") if isinstance(item, dict) else None
        if isinstance(delay, str):
            m = re.fullmatch(r"(\d+(?:\.\d+)?)s", delay.strip())
            if m:
                return float(m.group(1))
    return None


def _response_text(response: types.GenerateContentResponse) -> tuple[str | None, str | None]:
    """Return (text, blocking reason or None)."""
    feedback = getattr(response, "prompt_feedback", None)
    block = getattr(feedback, "block_reason", None) if feedback else None
    if block:
        return None, str(getattr(block, "value", block))
    candidates = getattr(response, "candidates", None) or []
    if candidates:
        reason = getattr(candidates[0], "finish_reason", None)
        reason_name = str(getattr(reason, "value", reason)) if reason else None
        if reason_name in _BLOCKING_FINISH_REASONS:
            return None, reason_name
    try:
        return response.text, None
    except (ValueError, AttributeError):
        return None, None


class GeminiClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
        temperature: float = 0.3,
        sdk_client: Any | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: random.Random | None = None,
    ) -> None:
        settings = get_settings()
        self.model = model or settings.gemini_model
        self.timeout = timeout
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.temperature = temperature
        self._sleep = sleep
        self._rng = rng or random.Random()
        if sdk_client is None:
            key = api_key if api_key is not None else settings.gemini_api_key
            if not key:
                raise GeminiConfigError(
                    "GEMINI_API_KEY is not set. Add it to backend/.env (see .env.example)."
                )
            sdk_client = genai.Client(
                api_key=key,
                http_options=types.HttpOptions(
                    timeout=int(timeout * 1000),  # milliseconds
                    retry_options=types.HttpRetryOptions(attempts=1),  # we own retries
                ),
            )
        self._client = sdk_client
        self.request_count = 0  # HTTP requests sent, including retries and repairs

    # ------------------------------------------------------------------ public

    async def tailor_resume(
        self,
        resume: ParsedResume,
        jd: JDRequirements,
        keywords: list[str] | None = None,
        missing_keywords: list[str] | None = None,
        plan: list[RequirementMatch] | None = None,
    ) -> TailoringResponse:
        """Ask Gemini to tailor the resume bullets to the JD."""
        prompt = build_tailoring_prompt(resume, jd, keywords, missing_keywords, plan)
        return await self.generate_structured(
            system=prompt.system, user=prompt.user, schema=TailoringResponse
        )

    async def verify_rewrites(
        self, resume_text: str, items: list[VerifyItem]
    ) -> VerificationResponse:
        """Second call (verify-v1): is each rewrite supported by the original?"""
        system, user = build_verify_prompt(resume_text, items)
        return await self.generate_structured(system=system, user=user, schema=VerificationResponse)

    async def generate_structured[T: BaseModel](
        self, *, system: str, user: str, schema: type[T]
    ) -> T:
        """Generate JSON matching ``schema``, with one repair attempt."""
        text = await self._generate(system, user, schema)
        try:
            return _parse(text, schema)
        except _ParseFailure as first:
            logger.warning("Gemini returned unusable output (%s); sending repair request", first)
            repaired = await self._generate(
                system, build_repair_prompt(text or "", str(first)), schema
            )
            try:
                return _parse(repaired, schema)
            except _ParseFailure as second:
                raise GeminiResponseError(
                    f"Gemini returned malformed output twice; last problem: {second}"
                ) from None

    # ----------------------------------------------------------------- private

    def _backoff(self, attempt: int, server_hint: float | None) -> float:
        delay = min(self.max_delay, self.base_delay * (2**attempt))
        delay += self._rng.uniform(0, self.base_delay / 2)
        if server_hint is not None:
            delay = max(delay, server_hint)
        return min(delay, self.max_delay)

    async def _generate(self, system: str, user: str, schema: type[BaseModel]) -> str | None:
        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=schema,
            temperature=self.temperature,
        )
        for attempt in range(self.max_retries + 1):
            started = time.monotonic()
            self.request_count += 1
            try:
                response = await asyncio.wait_for(
                    self._client.aio.models.generate_content(
                        model=self.model, contents=user, config=config
                    ),
                    timeout=self.timeout,
                )
            except (TimeoutError, httpx.TimeoutException):
                logger.warning("Gemini timed out after %.0fs (model=%s)", self.timeout, self.model)
                raise GeminiTimeoutError(
                    f"Gemini did not respond within {self.timeout:g} seconds."
                ) from None
            except genai_errors.APIError as exc:
                code = exc.code
                retryable = code == 429 or (code is not None and 500 <= code < 600)
                hint = _retry_info_seconds(exc) if code == 429 else None
                if retryable and attempt < self.max_retries:
                    delay = self._backoff(attempt, hint)
                    logger.info(
                        "Gemini returned %s (attempt %d/%d); retrying in %.1fs",
                        code, attempt + 1, self.max_retries + 1, delay,
                    )  # fmt: skip
                    await self._sleep(delay)
                    continue
                if code == 429:
                    raise GeminiRateLimitError(
                        f"Gemini rate limit (429) persisted after {self.max_retries} retries.",
                        retry_after=hint,
                    ) from None
                if retryable:
                    raise GeminiResponseError(
                        f"Gemini server error {code} persisted after {self.max_retries} retries.",
                        status_code=code,
                        retryable=True,
                    ) from None
                status = getattr(exc, "status", None) or ""
                raise GeminiResponseError(
                    f"Gemini rejected the request with status {code} {status}".strip() + ".",
                    status_code=code,
                ) from None
            except (httpx.TransportError, OSError) as exc:
                raise GeminiResponseError(
                    f"Could not reach Gemini ({type(exc).__name__}).", retryable=True
                ) from None

            text, blocked = _response_text(response)
            logger.info(
                "Gemini responded in %.1fs (model=%s, attempt=%d, chars=%d)",
                time.monotonic() - started, self.model, attempt + 1, len(text or ""),
            )  # fmt: skip
            if blocked:
                raise GeminiResponseError(f"Gemini blocked the response (reason: {blocked}).")
            return text
        raise AssertionError("unreachable")  # pragma: no cover
