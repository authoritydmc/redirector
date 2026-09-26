"""Async database wiring (production path). Replaces Flask-SQLAlchemy globals.

- Engine + session factory built once from settings (env-first).
- Per-request `AsyncSession` via FastAPI `Depends(get_session)`.
- `init_db()` creates tables for fresh installs/dev/tests. Schema changes
  after v3.0 go through Alembic revisions (EPIC-04), never create_all.
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from backend.core.config import settings

# Imported for side effect: registers all tables on SQLModel.metadata.
from backend.models import entities as _entities  # noqa: F401


def _require_async_driver(url: str) -> str:
    if "+aiosqlite" in url or "+asyncpg" in url:
        return url
    raise ValueError(
        f"DATABASE_URL must use an async driver (sqlite+aiosqlite:// or "
        f"postgresql+asyncpg://), got: {url!r}"
    )


engine = create_async_engine(_require_async_driver(settings.database_url))
SessionFactory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionFactory() as session:
        yield session


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
