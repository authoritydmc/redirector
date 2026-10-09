"""Per-route rate limiting as a FastAPI dependency (EPIC-05 task 11, slice 1).

Why not slowapi: its limiter is import-time global state, so every
TestClient app in one pytest process would share buckets (and all test
clients share one IP) — plus its 429s bypass our RFC 7807 envelope. This
dependency keeps counters on `app.state` (fresh per app, isolated per
test) and denies via `AppError` (`auth:rate-limited`).

Design: sliding-window log per (route, client) key, in-process memory.
Multi-worker deployments need the Redis-backed graduation (same shape,
store behind `REDIRECTOR_REDIS_URL`); until then the budget multiplies
by worker count — documented, accepted for now. Account lockout and the
audit log are later slices of the same task.
"""

from __future__ import annotations

import time
from collections import deque

from fastapi import Request, status

from backend.core.errors import AppError

_PERIODS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}


def _parse_spec(spec: str) -> tuple[int, int]:
    """`"5/minute"` → (max_hits, window_seconds); fail fast on typos."""
    count, sep, period = spec.partition("/")
    period_key = period.strip().lower()
    if period_key.endswith("s"):
        period_key = period_key[:-1]  # "minutes" → "minute"
    try:
        max_hits = int(count.strip())
    except ValueError:
        raise ValueError(f"bad rate-limit spec: {spec!r} (want like '5/minute')") from None
    if not sep or period_key not in _PERIODS or max_hits < 1:
        raise ValueError(f"bad rate-limit spec: {spec!r} (want like '5/minute')")
    return max_hits, _PERIODS[period_key]


def _client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip().lower()
    if request.client is not None:
        return request.client.host
    return "unknown"


class RateLimit:
    """Sliding-window gate used as a FastAPI dependency.

    Usage: `_rl: Annotated[None, Depends(RateLimit("5/minute"))]`.
    Counters live on `app.state.rate_limit_store` (created in lifespan,
    defensively created here), keyed by route path + client IP. In-memory
    by design — see module docstring for the Redis graduation path.
    """

    def __init__(self, spec: str) -> None:
        self.max_hits, self.window_s = _parse_spec(spec)
        self.spec = spec

    def _store(self, request: Request) -> dict[str, deque[float]]:
        store: dict[str, deque[float]] | None = getattr(
            request.app.state, "rate_limit_store", None)
        if store is None:
            store = {}
            request.app.state.rate_limit_store = store
        return store

    async def __call__(self, request: Request) -> None:
        store = self._store(request)
        key = f"{request.url.path}:{_client_key(request)}"
        now = time.monotonic()
        hits = store.get(key)
        if hits is None:
            hits = store[key] = deque()
        while hits and hits[0] <= now - self.window_s:
            hits.popleft()
        if len(hits) >= self.max_hits:
            raise AppError(
                "Rate limit exceeded",
                status=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Too many requests ({self.spec}); retry later",
                code="auth:rate-limited",
            )
        if hits:
            hits.append(now)
        else:
            # Fully expired window (or first hit): replace the deque so
            # the store can't grow across distinct clients over time.
            store[key] = deque([now])
