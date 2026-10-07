"""Cache abstraction (EPIC-04/06). Routes/services depend on the Protocol,
never on Redis directly. Implementations: MemoryCache (dev/tests/M1),
RedisCache (EPIC-04, drop-in via same interface).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Protocol

# Singleflight factory: returns (value, ttl). value None means
# "explicitly uncacheable" (e.g. SSO target served from DB) — shared with
# waiting callers but never stored.
MissFactory = Callable[[], Awaitable[tuple[str | None, int]]]


class Cache(Protocol):
    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, ttl: int = 300) -> None: ...
    async def delete(self, key: str) -> None: ...
    async def clear_prefix(self, prefix: str) -> int: ...
    async def get_or_compute(self, key: str, factory: MissFactory) -> str | None:
        """Singleflight miss coalescing (EPIC-06): concurrent callers share
        one factory run. Stores non-None results; None is shared but not
        stored (callers fall back to a direct read). ..."""


class MemoryCache:
    """Thread-safe-enough in-memory cache with TTL. No I/O, no threads."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[str, float | None]] = {}
        self._inflight: dict[str, asyncio.Future[str | None]] = {}

    async def get_or_compute(self, key: str, factory: MissFactory) -> str | None:
        """Coalesce concurrent misses for `key` onto one factory run.

        Fast path returns a stored hit. Otherwise the first caller (leader)
        runs the factory while followers await the same future. The result
        is stored (unless None) before waiters are released; entries are
        removed from the flight map on every path so a slow/failed leader
        never blocks later callers. setdefault-style get-then-set needs no
        extra guard: no await sits between the map check and insert.
        """
        hit = await self.get(key)
        if hit is not None:
            return hit
        fut = self._inflight.get(key)
        if fut is None:
            fut = asyncio.get_running_loop().create_future()
            self._inflight[key] = fut
            try:
                value, ttl = await factory()
            except BaseException as exc:
                self._inflight.pop(key, None)
                if not fut.done():
                    fut.set_exception(exc)
                raise
            if value is not None:
                await self.set(key, value, ttl)
            self._inflight.pop(key, None)
            if not fut.done():
                fut.set_result(value)
            return value
        shared = await fut
        if shared is not None:
            return shared
        # Leader's result was explicitly uncacheable (never stored):
        # load directly in our own session.
        value, _ = await factory()
        return value

    async def get(self, key: str) -> str | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expires = entry
        if expires is not None and expires <= time.monotonic():
            self._store.pop(key, None)
            return None
        return value

    async def set(self, key: str, value: str, ttl: int = 300) -> None:
        self._store[key] = (value, time.monotonic() + ttl if ttl > 0 else None)

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)

    async def clear_prefix(self, prefix: str) -> int:
        doomed = [k for k in self._store if k.startswith(prefix)]
        for k in doomed:
            del self._store[k]
        return len(doomed)
