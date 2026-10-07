"""Singleflight + negative-cache tests for shortcut lookup (EPIC-06 task 3).

Proves the hot-path stampede guard on SQLAlchemyShortcutRepository.lookup:
concurrent cold lookups share one DB read, total misses park a short-TTL
sentinel (invalidated on create), and SSO targets are served from the DB
but never stored.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest

pytest.importorskip("sqlmodel")

from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402

from backend.core.cache import MemoryCache  # noqa: E402
from backend.models.entities import Shortcut, UpstreamCache  # noqa: E402
from backend.modules.shortcuts.repository import (  # noqa: E402
    CACHE_MISS_SENTINEL,
    SHORTCUT_CACHE_PREFIX,
    SQLAlchemyShortcutRepository,
)


def _engine():
    # StaticPool shares one in-memory DB across per-request sessions,
    # mirroring concurrent requests against the same database.
    return create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )


class SlowRepo(SQLAlchemyShortcutRepository):
    """Counts DB-backed miss loads and adds latency to widen the race."""

    def __init__(self, *args, delay: float = 0.0, calls: list[str] | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.delay = delay
        self.calls: list[str] = calls if calls is not None else []

    async def _miss_load(self, pattern: str):  # type: ignore[no-untyped-def]
        self.calls.append(pattern)
        await asyncio.sleep(self.delay)
        return await super()._miss_load(pattern)


@asynccontextmanager
async def _sessions(factory: async_sessionmaker[AsyncSession], n: int) -> AsyncIterator[list[AsyncSession]]:
    sessions = [factory() for _ in range(n)]
    try:
        yield sessions
    finally:
        for session in sessions:
            await session.close()


def test_concurrent_cold_lookups_share_one_db_read() -> None:
    """5 concurrent cold lookups → 1 DB-backed load, ~max latency not sum."""

    async def _main() -> tuple[list[tuple[object, str]], int, float]:
        engine = _engine()
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            session.add(Shortcut(pattern="docs", target="https://x.example/docs"))
            await session.commit()
        cache = MemoryCache()
        calls: list[str] = []
        async with _sessions(factory, 5) as sessions:
            repos = [SlowRepo(s, cache, delay=0.3, calls=calls) for s in sessions]
            started = time.perf_counter()
            results = await asyncio.gather(*(r.lookup("docs") for r in repos))
            elapsed = time.perf_counter() - started
        return [(e, s) for e, s in results], len(calls), elapsed

    results, db_loads, elapsed = asyncio.run(_main())
    assert db_loads == 1
    assert elapsed < 1.0, f"coalesced lookup took {elapsed:.2f}s, expected ~0.3s (max, not sum)"
    # Leader serves its own DB row (pre-singleflight semantics); followers
    # decode the shared store. Every caller gets the same target.
    assert sum(1 for _, source in results if source == "db") == 1
    assert {source for _, source in results} <= {"db", "memory"}
    assert all(
        isinstance(entity, Shortcut) and entity.target == "https://x.example/docs"
        for entity, _ in results
    )


def test_miss_cached_then_invalidated_on_create() -> None:
    """Unknown pattern parks a sentinel; create() invalidates it."""

    async def _main() -> tuple[str | None, int]:
        engine = _engine()
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        cache = MemoryCache()
        calls: list[str] = []
        async with factory() as session:
            repo = SlowRepo(session, cache, calls=calls)
            assert await repo.lookup("new") == (None, "")
            assert await repo.lookup("new") == (None, "")
            assert calls == ["new"]  # second hit served from the sentinel
            sentinel = await cache.get(f"{SHORTCUT_CACHE_PREFIX}new")
            created = await repo.create(Shortcut(pattern="new", target="https://x.example/new"))
            assert created.pattern == "new"
            entity, source = await repo.lookup("new")
            assert source == "db"
            assert isinstance(entity, Shortcut) and entity.target == "https://x.example/new"
            return sentinel, len(calls)

    sentinel, db_loads = asyncio.run(_main())
    assert sentinel == CACHE_MISS_SENTINEL
    assert db_loads == 2  # miss + post-create reload, nothing more


def test_sso_target_served_from_db_never_cached() -> None:
    """SSO shortcut targets bypass the cache entirely (v2 parity)."""

    async def _main() -> tuple[list[tuple[object, str]], int, str | None]:
        engine = _engine()
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            session.add(Shortcut(pattern="sso", target="https://login.example/sso/x"))
            await session.commit()
        cache = MemoryCache()
        calls: list[str] = []
        async with _sessions(factory, 3) as sessions:
            repos = [SlowRepo(s, cache, delay=0.2, calls=calls) for s in sessions]
            results = await asyncio.gather(*(r.lookup("sso") for r in repos))
            stored = await cache.get(f"{SHORTCUT_CACHE_PREFIX}sso")
        return [(e, s) for e, s in results], len(calls), stored

    results, db_loads, stored = asyncio.run(_main())
    assert stored is None
    assert db_loads == 3  # no coalescing benefit, but correctness holds
    assert all(source == "db" for _, source in results)
    assert all(
        isinstance(entity, Shortcut) and entity.target == "https://login.example/sso/x"
        for entity, _ in results
    )


def test_upstream_row_cached_after_first_hit() -> None:
    async def _main() -> tuple[str, str, int]:
        engine = _engine()
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            session.add(UpstreamCache(
                pattern="bit", upstream_name="bitly", resolved_url="https://bit.ly/x",
            ))
            await session.commit()
        cache = MemoryCache()
        calls: list[str] = []
        async with factory() as session:
            repo = SlowRepo(session, cache, calls=calls)
            first = await repo.lookup("bit")
            second = await repo.lookup("bit")
            return first[1], second[1], len(calls)

    first_source, second_source, db_loads = asyncio.run(_main())
    assert (first_source, second_source) == ("upstream", "memory")
    assert db_loads == 1
