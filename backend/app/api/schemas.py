"""Response schemas shared by every API resource (tailoring and archive).

All error responses look like {"error": {code, message, retryable, details,
retry_after_seconds}}. Lives here, not in a feature package, so features
stay independent of each other."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    field: str | None = None
    message: str


class ErrorBody(BaseModel):
    code: str
    message: str
    retryable: bool = False
    details: list[ErrorDetail] = Field(default_factory=list)
    retry_after_seconds: float | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody
