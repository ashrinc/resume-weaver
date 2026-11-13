"""Tiny in-memory sliding-window rate limiter (per process).

Good enough for a single-user deployment on one instance: it bounds password
guessing and protects the Gemini quota. State is lost on restart, which is
fine for both purposes.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable

from fastapi import Request

__all__ = ["RateLimitedError", "RateLimiter", "client_key"]


class RateLimitedError(Exception):
    """Too many requests. Maps to HTTP 429 ``rate_limited``."""

    def __init__(self, retry_after: float, what: str) -> None:
        super().__init__(f"Too many {what}. Try again in {max(1, round(retry_after))} seconds.")
        self.retry_after = retry_after


class RateLimiter:
    def __init__(
        self,
        limit: int,
        window_seconds: float,
        what: str,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limit = limit
        self.window = window_seconds
        self.what = what
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}

    def hit(self, key: str) -> None:
        """Record one request for ``key``; raise RateLimitedError if over the limit.
        A limit of 0 or less disables the limiter."""
        if self.limit <= 0:
            return
        now = self._clock()
        hits = self._hits.setdefault(key, deque())
        while hits and hits[0] <= now - self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            raise RateLimitedError(hits[0] + self.window - now, self.what)
        hits.append(now)
        if len(self._hits) > 10_000:  # bound memory: drop idle keys
            for k in [k for k, v in self._hits.items() if not v]:
                del self._hits[k]


def client_key(request: Request) -> str:
    """The client's IP. Behind Render's proxy uvicorn runs with --proxy-headers,
    so ``request.client.host`` is the real address from X-Forwarded-For."""
    return request.client.host if request.client else "unknown"
