"""Async database wiring (production path). Replaces Flask-SQLAlchemy globals.

- Engine + session factory built once from settings (env-first).
- Per-request `AsyncSession` via FastAPI `Depends(get_session)`.
- `init_db()` creates tables for fresh installs/dev/tests. Schema changes
  after v3.0 go through Alembic revisions (EPIC-04), never create_all.
"""

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel

from backend.core.config import settings

# Imported for side effect: registers all tables on SQLModel.metadata.
from backend.models import entities as _entities  # noqa: F401

# Wait out transient write locks instead of failing with SQLITE_BUSY
# under concurrent redirect writes.
SQLITE_BUSY_TIMEOUT_S = 30


def _require_async_driver(url: str) -> str:
    if "+aiosqlite" in url or "+asyncpg" in url:
        return url
    raise ValueError(
        f"DATABASE_URL must use an async driver (sqlite+aiosqlite:// or "
        f"postgresql+asyncpg://), got: {url!r}"
    )


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite+aiosqlite://")


def build_engine(url: str) -> AsyncEngine:
    """Create the app engine. File SQLite gets WAL + a busy timeout so
    concurrent redirect writes (access_count) don't SQLITE_BUSY; other
    URLs (asyncpg) use driver defaults."""
    engine = create_async_engine(
        _require_async_driver(url),
        connect_args={"timeout": SQLITE_BUSY_TIMEOUT_S} if _is_sqlite(url) else {},
    )
    if _is_sqlite(url):

        @event.listens_for(engine.sync_engine, "connect")
        def _set_sqlite_pragmas(dbapi_conn: Any, _record: Any) -> None:
            cursor = dbapi_conn.cursor()
            try:
                cursor.execute("PRAGMA journal_mode=WAL")
            finally:
                cursor.close()

    return engine


engine = build_engine(settings.database_url)
SessionFactory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionFactory() as session:
        yield session


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
