"""Structured JSON error responses: {"error": {code, message, retryable, ...}}.

| Exception                  | HTTP | code                   | retryable        |
|----------------------------|------|------------------------|------------------|
| RequestValidationError     | 422  | invalid_request        | no               |
| PipelineInputError         | 400  | invalid_input          | no               |
| GeminiTimeoutError         | 502  | gemini_timeout         | yes              |
| GeminiRateLimitError       | 502  | gemini_rate_limited    | yes              |
| GeminiResponseError        | 502  | gemini_bad_response    | per error        |
| GeminiConfigError          | 500  | gemini_not_configured  | no               |
| TailorDeadlineError        | 504  | gemini_deadline        | yes              |
| UnauthorizedError          | 401  | unauthorized           | no               |
| RateLimitedError           | 429  | rate_limited           | yes              |
| InternalPipelineError      | 500  | internal_error         | yes              |
| ArchiveValidationError     | 422  | invalid_upload         | no               |
| ArchiveNotFoundError       | 404  | not_found              | no               |

Responses never echo the submitted resume or job description (FastAPI's
default 422 body includes the input, so it is replaced here).
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.auth import UnauthorizedError
from app.api.ratelimit import RateLimitedError
from app.api.schemas import ErrorBody, ErrorDetail, ErrorResponse
from app.archive.service import ArchiveNotFoundError, ArchiveValidationError
from app.llm.errors import (
    GeminiConfigError,
    GeminiError,
    GeminiRateLimitError,
    GeminiResponseError,
    GeminiTimeoutError,
)
from app.tailor.pipeline import PipelineInputError


class TailorDeadlineError(Exception):
    """The whole tailoring run exceeded TAILOR_DEADLINE_SECONDS and was cancelled."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        shown = f"{seconds:g}"
        super().__init__(f"Tailoring took longer than {shown} seconds and was stopped. Try again.")


class InternalPipelineError(Exception):
    """Wraps an unexpected exception so only a generic message reaches the client."""


def error_response(status: int, body: ErrorBody) -> JSONResponse:
    return JSONResponse(status_code=status, content=ErrorResponse(error=body).model_dump())


def _validation_details(exc: RequestValidationError) -> list[ErrorDetail]:
    details = []
    for err in exc.errors():
        loc = [str(p) for p in err.get("loc", ()) if p != "body"]
        message = str(err.get("msg", "invalid value")).removeprefix("Value error, ")
        details.append(ErrorDetail(field=".".join(loc) or None, message=message))
    return details


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = _validation_details(exc)
        fields = ", ".join(d.field for d in details if d.field) or "request body"
        return error_response(
            422,
            ErrorBody(
                code="invalid_request",
                message=f"Invalid request: check {fields}.",
                details=details,
            ),
        )

    @app.exception_handler(PipelineInputError)
    async def _input(_: Request, exc: PipelineInputError) -> JSONResponse:
        return error_response(400, ErrorBody(code="invalid_input", message=str(exc)))

    @app.exception_handler(GeminiError)
    async def _gemini(_: Request, exc: GeminiError) -> JSONResponse:
        if isinstance(exc, GeminiConfigError):
            return error_response(500, ErrorBody(code="gemini_not_configured", message=str(exc)))
        if isinstance(exc, GeminiTimeoutError):
            code, message = "gemini_timeout", f"{exc} Please try again."
        elif isinstance(exc, GeminiRateLimitError):
            code = "gemini_rate_limited"
            message = "Gemini's free-tier rate limit was reached. Wait a moment and try again."
        elif isinstance(exc, GeminiResponseError):
            code, message = "gemini_bad_response", str(exc)
        else:
            code, message = "gemini_error", str(exc)
        return error_response(
            502,
            ErrorBody(
                code=code,
                message=message,
                retryable=exc.retryable,
                retry_after_seconds=getattr(exc, "retry_after", None),
            ),
        )

    @app.exception_handler(UnauthorizedError)
    async def _unauthorized(_: Request, exc: UnauthorizedError) -> JSONResponse:
        response = error_response(401, ErrorBody(code="unauthorized", message=str(exc)))
        response.headers["WWW-Authenticate"] = "Bearer"
        return response

    @app.exception_handler(RateLimitedError)
    async def _rate_limited(_: Request, exc: RateLimitedError) -> JSONResponse:
        response = error_response(
            429,
            ErrorBody(
                code="rate_limited",
                message=str(exc),
                retryable=True,
                retry_after_seconds=round(exc.retry_after, 1),
            ),
        )
        response.headers["Retry-After"] = str(max(1, round(exc.retry_after)))
        return response

    @app.exception_handler(TailorDeadlineError)
    async def _deadline(_: Request, exc: TailorDeadlineError) -> JSONResponse:
        return error_response(
            504, ErrorBody(code="gemini_deadline", message=str(exc), retryable=True)
        )

    @app.exception_handler(ArchiveValidationError)
    async def _archive_invalid(_: Request, exc: ArchiveValidationError) -> JSONResponse:
        return error_response(
            422,
            ErrorBody(
                code="invalid_upload",
                message=exc.message,
                details=[ErrorDetail(field=exc.field, message=exc.message)],
            ),
        )

    @app.exception_handler(ArchiveNotFoundError)
    async def _archive_not_found(_: Request, exc: ArchiveNotFoundError) -> JSONResponse:
        return error_response(404, ErrorBody(code="not_found", message=str(exc)))

    @app.exception_handler(InternalPipelineError)
    async def _internal(_: Request, __: InternalPipelineError) -> JSONResponse:
        return error_response(
            500,
            ErrorBody(
                code="internal_error",
                message="An unexpected error occurred while tailoring the resume.",
                retryable=True,
            ),
        )
