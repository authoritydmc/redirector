"""Tests for the engine factory (WAL + busy timeout on file SQLite)."""

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import text

pytest.importorskip("sqlmodel")

from backend.core.db import build_engine  # noqa: E402


def _pragma(db_path: Path) -> str:
    async def _main() -> str:
        engine = build_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
        try:
            async with engine.connect() as conn:
                mode = (await conn.execute(text("PRAGMA journal_mode"))).scalar()
                await conn.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)"))
                await conn.execute(text("INSERT INTO t (v) VALUES ('x')"))
                await conn.commit()
                return str(mode)
        finally:
            await engine.dispose()

    return asyncio.run(_main())


def test_file_sqlite_uses_wal(tmp_path: Path) -> None:
    assert _pragma(tmp_path / "wal.db") == "wal"


def test_memory_sqlite_still_works() -> None:
    async def _main() -> str:
        engine = build_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.connect() as conn:
                mode = (await conn.execute(text("PRAGMA journal_mode"))).scalar()
                await conn.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY)"))
                return str(mode)
        finally:
            await engine.dispose()

    # :memory: can't do WAL; the pragma must be a harmless no-op.
    assert asyncio.run(_main()) == "memory"


def test_sync_driver_url_rejected() -> None:
    with pytest.raises(ValueError, match="async driver"):
        build_engine("sqlite:///./data/redirect.db")
