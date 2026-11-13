"""Typed exceptions raised by the Gemini client.

Every error carries ``retryable`` so the API layer (M6) can tell the UI
whether offering a "Retry" button makes sense. Messages never include resume
or job description text.
"""

from __future__ import annotations


class GeminiError(Exception):
    """Base class for all Gemini client errors."""

    retryable: bool = False

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class GeminiConfigError(GeminiError):
    """Missing or invalid configuration (e.g. GEMINI_API_KEY not set)."""


class GeminiTimeoutError(GeminiError):
    """A request did not complete within the timeout."""

    retryable = True


class GeminiRateLimitError(GeminiError):
    """HTTP 429 persisted after all retries."""

    retryable = True

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message, status_code=429)
        self.retry_after = retry_after


class GeminiResponseError(GeminiError):
    """Gemini returned an error status, an empty/blocked response, or output
    that could not be parsed into the expected JSON even after a repair retry."""

    def __init__(
        self, message: str, *, status_code: int | None = None, retryable: bool = False
    ) -> None:
        super().__init__(message, status_code=status_code)
        self.retryable = retryable
