"""Account lockout + audit-log tests (EPIC-05 task 11).

Unit tests drive the security helpers directly (fast, no HTTP); the HTTP
test seeds a lockout and proves even the correct password gets 429.
File DBs per test (proven pattern: create_all + dispose, no cross-loop
sharing); settings thresholds stay at their defaults throughout.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

pytest.importorskip("sqlmodel")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlmodel import SQLModel, select  # noqa: E402

from backend.core.config import settings as app_settings  # noqa: E402
from backend.core.db import get_session  # noqa: E402
from backend.core.security import (  # noqa: E402
    failed_logins_since,
    log_auth_event,
    record_failed_login,
)
from backend.main import create_app  # noqa: E402
from backend.models.entities import AuthEvent, LoginAttempt  # noqa: E402
from backend.modules.jobs.runner import JobRunner  # noqa: E402


def _engine_for(tmp_path: Any):
    from sqlalchemy import event

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/lockout-test.db",
                                 connect_args={"timeout": 30})

    @event.listens_for(engine.sync_engine, "connect")
    def _set_wal(dbapi_conn: Any, _record: Any) -> None:
        cursor = dbapi_conn.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
        finally:
            cursor.close()

    return engine


async def _init_db(engine) -> None:  # type: ignore[no-untyped-def]
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    await engine.dispose()


def _api_client(tmp_path: Any) -> TestClient:
    engine = _engine_for(tmp_path)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    asyncio.run(_init_db(engine))
    app = create_app()
    app.state.jobs = JobRunner(factory)

    async def override_get_session():  # type: ignore[no-untyped-def]
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    return TestClient(app)


def test_lockout_counts_and_prunes(tmp_path: Any) -> None:
    async def _main() -> tuple[int, int]:
        engine = _engine_for(tmp_path)
        await _init_db(engine)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            for _ in range(3):
                await record_failed_login(session, "9.9.9.9")
            # One stale row, far outside any window.
            session.add(LoginAttempt(
                ip="9.9.9.9",
                attempted_at=datetime.now(UTC) - timedelta(days=30)))
            await session.commit()
            fresh = await failed_logins_since(session, "9.9.9.9", 15)
            other = await failed_logins_since(session, "1.1.1.1", 15)
            return fresh, other

    fresh, other = asyncio.run(_main())
    assert fresh == 3  # stale row pruned, not counted
    assert other == 0  # per-IP isolation


def test_audit_log_appends(tmp_path: Any) -> None:
    async def _main() -> list[tuple[str, str | None]]:
        engine = _engine_for(tmp_path)
        await _init_db(engine)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            await log_auth_event(session, "login.failed", "9.9.9.9")
            await log_auth_event(session, "login.success", "9.9.9.9",
                                 {"method": "password"})
            rows = (await session.execute(select(AuthEvent))).scalars().all()
            return [(r.kind, r.ip) for r in rows]

    assert asyncio.run(_main()) == [
        ("login.failed", "9.9.9.9"), ("login.success", "9.9.9.9")]


def test_locked_ip_gets_429_even_with_password(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _seed() -> None:
        engine = _engine_for(tmp_path)
        await _init_db(engine)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            for _ in range(app_settings.auth_lockout_max_attempts):
                await record_failed_login(session, "testclient")

    asyncio.run(_seed())
    with _api_client(tmp_path) as client:
        # Correct password, locked IP: 429, and the lockout itself is audited.
        res = client.post("/api/v1/auth/login",
                          json={"password": app_settings.admin_password})
        assert res.status_code == 429
        assert res.json()["code"] == "auth:locked-out"

        # A different IP is unaffected.
        other = client.post("/api/v1/auth/login",
                            headers={"X-Forwarded-For": "10.0.0.8"},
                            json={"password": "wrong"})
        assert other.status_code == 401
