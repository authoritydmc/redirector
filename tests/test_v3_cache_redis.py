"""RedisCache tests: protocol ops + distributed singleflight (EPIC-04 task 4).

Needs Redis on 127.0.0.1:6379 (WSL `docker run redis:8-alpine`, or the CI
`redis` service); skipped otherwise. Keys live on Redis DB 2 under a unique
prefix per test, so a shared dev broker is never flushed.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

import pytest

pytest.importorskip("redis")

from backend.core.cache import RedisCache, build_cache  # noqa: E402


def _redis_available() -> bool:
    import socket

    sock = socket.socket()
    sock.settimeout(1)
    try:
        return sock.connect_ex(("127.0.0.1", 6379)) == 0
    except OSError:
        return False
    finally:
        sock.close()


pytestmark = pytest.mark.skipif(
    not _redis_available(),
    reason="needs Redis on 127.0.0.1:6379 (WSL docker or CI service)",
)


def _cache(**overrides: Any) -> RedisCache:
    # Generous follower windows keep success-path tests deterministic; the
    # failure-path test below passes an explicitly short one.
    params = {"lock_ttl_s": 10.0, "follower_timeout_s": 2.0, "poll_interval_s": 0.02}
    params.update(overrides)
    return RedisCache("redis://127.0.0.1:6379/2", **params)  # type: ignore[arg-type]


def _prefix() -> str:
    return f"t:{uuid.uuid4().hex[:8]}:"


def test_roundtrip_expiry_and_delete() -> None:
    async def _main() -> None:
        cache = _cache()
        try:
            p = _prefix()
            assert await cache.get(f"{p}missing") is None
            await cache.set(f"{p}k", "v", ttl=60)
            assert await cache.get(f"{p}k") == "v"
            await cache.set(f"{p}short", "v", ttl=1)
            # Poll (don't wall-sleep): expiry timing under load is jittery.
            deadline = time.perf_counter() + 5.0
            while await cache.get(f"{p}short") is not None:
                assert time.perf_counter() < deadline, "ttl=1 key never expired"
                await asyncio.sleep(0.05)
            await cache.delete(f"{p}k")
            assert await cache.get(f"{p}k") is None
        finally:
            await cache.aclose()

    asyncio.run(_main())


def test_clear_prefix() -> None:
    async def _main() -> None:
        cache = _cache()
        try:
            p = _prefix()
            other = f"o:{uuid.uuid4().hex[:8]}:"
            await cache.set(f"{p}a", "1", ttl=60)
            await cache.set(f"{p}b", "2", ttl=60)
            await cache.set(f"{other}c", "3", ttl=60)
            assert await cache.clear_prefix(p) == 2
            assert await cache.get(f"{p}a") is None
            assert await cache.get(f"{other}c") == "3"
            await cache.delete(f"{other}c")
        finally:
            await cache.aclose()

    asyncio.run(_main())


def test_get_or_compute_stores_once() -> None:
    async def _main() -> None:
        cache = _cache()
        try:
            p = _prefix()
            calls: list[str] = []

            async def factory() -> tuple[str | None, int]:
                calls.append("x")
                return "computed", 60

            assert await cache.get_or_compute(f"{p}k", factory) == "computed"
            assert await cache.get_or_compute(f"{p}k", factory) == "computed"
            assert calls == ["x"]  # second hit served from Redis
            await cache.delete(f"{p}k")
        finally:
            await cache.aclose()

    asyncio.run(_main())


def test_concurrent_misses_share_one_factory_run() -> None:
    """5 concurrent cold misses → 1 factory run, ~max latency not sum."""

    async def _main() -> tuple[list[str | None], int, float]:
        cache = _cache()
        try:
            p = _prefix()
            calls: list[str] = []

            async def factory() -> tuple[str | None, int]:
                calls.append("x")
                await asyncio.sleep(0.2)
                return "shared", 60

            started = time.perf_counter()
            results = await asyncio.gather(
                *(cache.get_or_compute(f"{p}k", factory) for _ in range(5)))
            await cache.delete(f"{p}k")
            return list(results), len(calls), time.perf_counter() - started
        finally:
            await cache.aclose()

    results, factory_calls, elapsed = asyncio.run(_main())
    assert results == ["shared"] * 5
    assert factory_calls == 1
    assert elapsed < 0.9, f"coalesced run took {elapsed:.2f}s, expected ~0.2s"


def test_uncacheable_none_shared_but_never_stored() -> None:
    async def _main() -> tuple[list[str | None], int, float]:
        cache = _cache()
        try:
            p = _prefix()
            calls: list[str] = []

            async def factory() -> tuple[str | None, int]:
                calls.append("x")
                return None, 0

            started = time.perf_counter()
            results = await asyncio.gather(
                *(cache.get_or_compute(f"{p}k", factory) for _ in range(3)))
            stored = await cache.get(f"{p}k")
            assert stored is None
            return list(results), len(calls), time.perf_counter() - started
        finally:
            await cache.aclose()

    results, factory_calls, elapsed = asyncio.run(_main())
    assert results == [None] * 3
    # First caller wins the lock with an uncacheable verdict; the tombstone
    # releases followers at once instead of making them wait out the window.
    assert factory_calls == 3
    assert elapsed < 1.0, f"tombstone release took {elapsed:.2f}s"


def test_failed_leader_releases_followers() -> None:
    """Leader crash → followers stop waiting and compute themselves."""

    async def _main() -> tuple[int, int]:
        cache = _cache(follower_timeout_s=0.3)
        try:
            p = _prefix()
            calls = 0
            failures = 0

            async def factory() -> tuple[str | None, int]:
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise ConnectionError("leader boom")
                return "recovered", 60

            async def caller() -> str | None:
                try:
                    return await cache.get_or_compute(f"{p}k", factory)
                except ConnectionError:
                    nonlocal failures
                    failures += 1
                    return None

            results = await asyncio.gather(*(caller() for _ in range(3)))
            await cache.delete(f"{p}k")
            assert sorted(r or "" for r in results) == ["", "recovered", "recovered"]
            return calls, failures
        finally:
            await cache.aclose()

    factory_calls, failures = asyncio.run(_main())
    assert failures == 1  # exactly the leader surfaces its own crash
    # Leader + at least one follower recompute; a second follower may read
    # the recomputed value instead — coalescing working as intended.
    assert 2 <= factory_calls <= 3


def test_outage_degrades_to_direct_compute() -> None:
    """Nothing listening: factory still runs, nothing stored, no raise."""

    async def _main() -> str | None:
        cache = RedisCache("redis://127.0.0.1:6390/0")
        try:
            async def factory() -> tuple[str | None, int]:
                return "direct", 60

            return await cache.get_or_compute("any:key", factory)
        finally:
            await cache.aclose()

    assert asyncio.run(_main()) == "direct"


def test_build_cache_selection() -> None:
    assert type(build_cache("memory", "redis://127.0.0.1:6379/0")).__name__ == "MemoryCache"
    assert type(build_cache("redis", "redis://127.0.0.1:6379/0")).__name__ == "RedisCache"
    with pytest.raises(ValueError):
        build_cache("memcached", "redis://127.0.0.1:6379/0")


def test_repo_lookup_roundtrip_through_redis(tmp_path: Any) -> None:
    """Repository proof: cold lookup stores, warm lookup serves from Redis."""
    pytest.importorskip("sqlmodel")

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlmodel import SQLModel

    from backend.models.entities import Shortcut
    from backend.modules.shortcuts.repository import SQLAlchemyShortcutRepository

    async def _main() -> tuple[str, str, str | None]:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/lookup-redis.db")
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        await engine.dispose()
        factory = async_sessionmaker(engine, expire_on_commit=False)
        cache = RedisCache("redis://127.0.0.1:6379/2")
        # Unique pattern: the shared dev broker persists keys across runs.
        pattern = f"rcached-{uuid.uuid4().hex[:8]}"
        try:
            async with factory() as session:
                session.add(Shortcut(pattern=pattern, target="https://x.example/r"))
                await session.commit()
            async with factory() as session:
                repo = SQLAlchemyShortcutRepository(session, cache)
                first = await repo.lookup(pattern)
                second = await repo.lookup(pattern)
                stored = await cache.get(f"shortcut:{pattern}")
                assert isinstance(first[0], Shortcut)
                return first[1], second[1], stored
        finally:
            await cache.delete(f"shortcut:{pattern}")
            await cache.aclose()
            await engine.dispose()

    first_source, second_source, stored = asyncio.run(_main())
    assert (first_source, second_source) == ("db", "memory")
    assert stored is not None
