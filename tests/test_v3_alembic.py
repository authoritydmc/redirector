"""Alembic migration tests: same head boots SQLite + Postgres (EPIC-04 task 1).

Postgres runs when TEST_POSTGRES_URL is set (WSL docker or the CI service);
otherwise only the SQLite leg runs. No app imports beyond entities — these
exercise the migration scripts, not the API.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

pytest.importorskip("alembic")
pytest.importorskip("sqlmodel")

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import inspect  # noqa: E402
from sqlmodel import SQLModel, select  # noqa: E402

EXPECTED_TABLES = set(SQLModel.metadata.tables)


def _upgrade(url: str) -> None:
    cfg = Config("backend/alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")


def _downgrade(url: str, revision: str) -> None:
    cfg = Config("backend/alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    command.downgrade(cfg, revision)


def _table_names(url: str) -> set[str]:
    """Inspector table names via an async engine (no sync drivers needed)."""
    import asyncio

    from sqlalchemy.ext.asyncio import create_async_engine

    async def _main() -> set[str]:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                return set(await conn.run_sync(
                    lambda sc: inspect(sc).get_table_names()))
        finally:
            await engine.dispose()

    return asyncio.run(_main())


def test_upgrade_head_sqlite(tmp_path: Any) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path}/mig-test.db"
    _upgrade(url)
    tables = _table_names(url)
    assert EXPECTED_TABLES <= tables, EXPECTED_TABLES - tables
    assert "alembic_version" in tables


def test_downgrade_base_sqlite(tmp_path: Any) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path}/mig-down.db"
    _upgrade(url)
    _downgrade(url, "base")
    tables = _table_names(url)
    assert not (EXPECTED_TABLES & tables), EXPECTED_TABLES & tables


def _postgres_url() -> str | None:
    return os.environ.get("TEST_POSTGRES_URL")


def test_upgrade_head_postgres() -> None:
    pytest.importorskip("asyncpg")
    url = _postgres_url()
    if not url:
        pytest.skip("needs TEST_POSTGRES_URL (WSL docker or CI service)")
    assert url is not None
    _upgrade(url)
    tables = _table_names(url)
    assert EXPECTED_TABLES <= tables, EXPECTED_TABLES - tables


def test_postgres_row_roundtrip() -> None:
    """Proves the migrated PG schema actually works (enums, JSON, defaults)."""
    pytest.importorskip("asyncpg")
    url = _postgres_url()
    if not url:
        pytest.skip("needs TEST_POSTGRES_URL (WSL docker or CI service)")
    assert url is not None

    import asyncio
    import uuid

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from backend.models.entities import Shortcut

    # Unique pattern: the shared PG database persists across runs.
    pattern = f"pgprobe-{uuid.uuid4().hex[:8]}"

    async def _main() -> tuple[str, str]:
        engine = create_async_engine(url)
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                session.add(Shortcut(pattern=pattern, target="https://x.example/p"))
                await session.commit()
            async with factory() as session:
                row = (await session.execute(
                    select(Shortcut).where(Shortcut.pattern == pattern))).scalar_one()
                return row.type.value, row.target
        finally:
            await engine.dispose()

    kind, target = asyncio.run(_main())
    assert (kind, target) == ("static", "https://x.example/p")
