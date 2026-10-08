"""Cache abstraction (EPIC-04/06). Routes/services depend on the Protocol,
never on Redis directly. Implementations: MemoryCache (dev/tests/M1),
RedisCache (shared across processes/workers, EPIC-04 task 4).
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import redis.asyncio as redis_asyncio
from redis.exceptions import RedisError

logger = logging.getLogger("redirector")

# Singleflight factory: returns (value, ttl). value None means
# "explicitly uncacheable" (e.g. SSO target served from DB) — shared with
# waiting callers but never stored.
MissFactory = Callable[[], Awaitable[tuple[str | None, int]]]


class Cache(Protocol):
    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, ttl: int = 300) -> None: ...
    async def delete(self, key: str) -> None: ...
    async def clear_prefix(self, prefix: str) -> int: ...
    def cache_stats(self) -> dict[str, int]:
        """Hit/miss counters (sync: plain attribute reads, no I/O). ..."""
    async def get_or_compute(self, key: str, factory: MissFactory) -> str | None:
        """Singleflight miss coalescing (EPIC-06): concurrent callers share
        one factory run. Stores non-None results; None is shared but not
        stored (callers fall back to a direct read). ..."""


class MemoryCache:
    """Thread-safe-enough in-memory cache with TTL. No I/O, no threads."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[str, float | None]] = {}
        self._inflight: dict[str, asyncio.Future[str | None]] = {}
        self.hits = 0
        self.misses = 0

    def cache_stats(self) -> dict[str, int]:
        return {"hits": self.hits, "misses": self.misses}

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
            self.misses += 1
            return None
        value, expires = entry
        if expires is not None and expires <= time.monotonic():
            self._store.pop(key, None)
            self.misses += 1
            return None
        self.hits += 1
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


_RELEASE_LOCK = (
    "if redis.call('GET', KEYS[1]) == ARGV[1] "
    "then return redis.call('DEL', KEYS[1]) else return 0 end"
)

# Tombstone a leader parks on its lock when the verdict is explicitly
# uncacheable (SSO): followers see it and compute immediately instead of
# waiting out the follower window for a value that will never be stored.
_UNCACHEABLE = "uncacheable"
_TOMBSTONE_TTL_S = 5


class RedisCache:
    """Shared Redis cache with distributed singleflight (EPIC-04/06).

    Miss coalescing works across processes via a `lock:{key}` mutex
    (SET NX PX + token-checked Lua release): the leader runs the factory
    while followers poll for the stored value. A crashed leader's lock
    expires on its own; followers that outwait it compute directly.
    `None` factory results are shared but never stored (SSO parity) — the
    leader parks a short-lived `uncacheable` tombstone on its lock so
    followers compute immediately instead of waiting out their window.
    Follows v2's degrade philosophy: Redis outages never 500 the hot
    path — reads fall through as misses, writes become best-effort, and
    factories run directly. (A circuit breaker would cut the per-request
    timeout cost; not yet wired.)
    """

    def __init__(
        self,
        url: str,
        *,
        lock_ttl_s: float = 10.0,
        follower_timeout_s: float = 5.0,
        poll_interval_s: float = 0.05,
    ) -> None:
        self._redis = redis_asyncio.Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        self._lock_ttl_s = lock_ttl_s
        self._follower_timeout_s = follower_timeout_s
        self._poll_interval_s = poll_interval_s
        self.hits = 0
        self.misses = 0

    async def aclose(self) -> None:
        await self._redis.aclose()

    def cache_stats(self) -> dict[str, int]:
        return {"hits": self.hits, "misses": self.misses}

    async def get(self, key: str) -> str | None:
        try:
            value: str | None = await self._redis.get(key)
            if value is None:
                self.misses += 1
            else:
                self.hits += 1
            return value
        except RedisError as exc:
            logger.warning("cache get failed, treating as miss: %s", exc)
            self.misses += 1
            return None

    async def set(self, key: str, value: str, ttl: int = 300) -> None:
        try:
            await self._redis.set(key, value, ex=ttl if ttl > 0 else None)
        except RedisError as exc:
            logger.warning("cache set failed, continuing uncached: %s", exc)

    async def delete(self, key: str) -> None:
        try:
            await self._redis.delete(key)
        except RedisError as exc:
            logger.warning("cache delete failed: %s", exc)

    async def clear_prefix(self, prefix: str) -> int:
        try:
            count = 0
            async for key in self._redis.scan_iter(match=f"{prefix}*", count=500):
                await self._redis.delete(key)
                count += 1
            return count
        except RedisError as exc:
            logger.warning("cache clear_prefix failed: %s", exc)
            return 0

    async def _release(self, lock_key: str, token: str) -> None:
        try:
            await self._redis.eval(_RELEASE_LOCK, 1, lock_key, token)
        except RedisError:
            pass  # PX expiry backstops a failed release

    async def get_or_compute(self, key: str, factory: MissFactory) -> str | None:
        hit: str | None
        try:
            hit = await self._redis.get(key)
        except RedisError:
            hit = None
        if hit is not None:
            return hit
        # Locks live under their own prefix so prefix purges never reap
        # a live flight (a stale purge just means one extra DB read).
        lock_key = f"lock:{key}"
        token = uuid.uuid4().hex
        try:
            acquired = await self._redis.set(
                lock_key, token, nx=True, px=int(self._lock_ttl_s * 1000))
        except RedisError:
            value, _ = await factory()
            return value
        if acquired:
            try:
                value, ttl = await factory()
            except BaseException:
                await self._release(lock_key, token)
                raise
            if value is not None:
                try:
                    await self._redis.set(key, value, ex=ttl if ttl > 0 else None)
                except RedisError:
                    pass
                await self._release(lock_key, token)
            else:
                # Uncacheable verdict: park a tombstone so followers stop
                # waiting and compute directly (the value key stays empty).
                try:
                    await self._redis.set(lock_key, _UNCACHEABLE, ex=_TOMBSTONE_TTL_S)
                except RedisError:
                    pass
            return value
        deadline = time.monotonic() + self._follower_timeout_s
        while time.monotonic() < deadline:
            waiting: str | None
            try:
                waiting = await self._redis.get(key)
                if waiting is not None:
                    return waiting
                if await self._redis.get(lock_key) == _UNCACHEABLE:
                    break  # leader finished uncacheable: no value is coming
            except RedisError:
                break  # outage: compute directly below
            await asyncio.sleep(self._poll_interval_s)
        # Leader crashed or was too slow (uncacheable verdicts exit early
        # via tombstone): compute directly; NX keeps a racing leader's
        # write if it lands.
        value, ttl = await factory()
        if value is not None:
            try:
                await self._redis.set(key, value, ex=ttl if ttl > 0 else None, nx=True)
            except RedisError:
                pass
        return value


_CACHE_INSTANCES: list[Cache] = []


def _register(cache: Cache) -> Cache:
    """Track process-wide caches so /metrics can aggregate hit rates."""
    _CACHE_INSTANCES.append(cache)
    return cache


def build_cache(backend: str, redis_url: str) -> Cache:
    """Select the cache implementation from settings (fail fast on typos)."""
    if backend == "redis":
        return _register(RedisCache(redis_url))
    if backend == "memory":
        return _register(MemoryCache())
    raise ValueError(f"unknown cache backend: {backend!r} (want 'memory' or 'redis')")


def aggregate_cache_stats() -> dict[str, Any]:
    """Summed hits/misses plus hit_rate (None when nothing has been read)."""
    hits = sum(cache.cache_stats()["hits"] for cache in _CACHE_INSTANCES)
    misses = sum(cache.cache_stats()["misses"] for cache in _CACHE_INSTANCES)
    total = hits + misses
    return {
        "hits": hits,
        "misses": misses,
        "hit_rate": round(hits / total, 3) if total else None,
    }
