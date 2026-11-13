"""Single-user access control (deployment).

* ``ACCESS_PASSWORD`` empty: auth is off (local development).
* Set: every ``/api`` route except ``/api/health``, ``/api/auth/login`` and
  ``/api/auth/session`` needs ``Authorization: Bearer <token>``.

``POST /api/auth/login {password}`` returns a stateless token:
``<expiry unix seconds>.<hex HMAC-SHA256(key, expiry)>``, where the key is
derived from the password. Changing the password therefore invalidates every
token; nothing is stored server-side. Comparisons are constant-time, and
login attempts are rate limited per client IP.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.api.ratelimit import client_key
from app.api.schemas import ErrorResponse

__all__ = ["UnauthorizedError", "issue_token", "require_auth", "router", "verify_token"]

TOKEN_TTL_SECONDS = 30 * 24 * 3600

router = APIRouter(prefix="/api/auth", tags=["auth"])


class UnauthorizedError(Exception):
    """Missing, invalid or expired token, or wrong password. Maps to HTTP 401."""


def _key(password: str) -> bytes:
    return hashlib.sha256(b"resume-tailr/session/v1\x00" + password.encode()).digest()


def issue_token(password: str, *, now: float | None = None, ttl: int = TOKEN_TTL_SECONDS) -> str:
    expiry = str(int((now if now is not None else time.time()) + ttl))
    sig = hmac.new(_key(password), expiry.encode(), hashlib.sha256).hexdigest()
    return f"{expiry}.{sig}"


def verify_token(token: str, password: str, *, now: float | None = None) -> bool:
    expiry, _, sig = token.strip().partition(".")
    if not expiry.isdigit() or not sig:
        return False
    expected = hmac.new(_key(password), expiry.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return False
    return int(expiry) > (now if now is not None else time.time())


def _password(request: Request) -> str:
    return request.app.state.access_password


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    return value.strip() if scheme.lower() == "bearer" and value.strip() else None


async def require_auth(request: Request) -> None:
    """Router dependency: enforce the token when a password is configured."""
    password = _password(request)
    if not password:
        return
    token = _bearer(request)
    if token is None or not verify_token(token, password):
        raise UnauthorizedError("Sign in to continue.")


Authenticated = Annotated[None, Depends(require_auth)]


class LoginRequest(BaseModel):
    password: str


class LoginResponse(BaseModel):
    token: str
    expires_at: int


class SessionResponse(BaseModel):
    auth_required: bool
    authenticated: bool


@router.post(
    "/login",
    response_model=LoginResponse,
    responses={401: {"model": ErrorResponse}, 429: {"model": ErrorResponse}},
)
async def login(body: LoginRequest, request: Request) -> LoginResponse:
    request.app.state.login_limiter.hit(client_key(request))
    password = _password(request)
    if not password:
        token = issue_token("")  # auth is off; any token works
    elif hmac.compare_digest(body.password.encode(), password.encode()):
        token = issue_token(password)
    else:
        raise UnauthorizedError("Wrong password.")
    return LoginResponse(token=token, expires_at=int(token.split(".", 1)[0]))


@router.get("/session", response_model=SessionResponse)
async def session(request: Request) -> SessionResponse:
    """Whether the frontend must show the login screen. Never requires auth."""
    password = _password(request)
    if not password:
        return SessionResponse(auth_required=False, authenticated=True)
    token = _bearer(request)
    return SessionResponse(
        auth_required=True, authenticated=token is not None and verify_token(token, password)
    )
