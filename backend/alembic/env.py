"""Alembic environment for the v3 backend (EPIC-04 task 1).

Async-first: the app only speaks async drivers (aiosqlite/asyncpg), so env
builds the same async engine and runs migrations through `run_sync` — no
sync drivers (psycopg2) required anywhere. The URL defaults to
REDIRECTOR_DATABASE_URL; `alembic -x url=<URL>` overrides it, which is how
tests target scratch databases without touching settings.

Online upgrades only (no --sql offline mode): review the generated script,
then apply it.
"""

from __future__ import annotations

import asyncio
import sys
from logging.config import fileConfig
from pathlib import Path
from typing import Any

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from backend.models import entities as _entities  # noqa: F401,E402 (register tables)

config = context.config
if config.config_file_name is not None:
    # Never disable existing loggers: pytest's capture handler (and any
    # host-app logging) must survive migration runs in-process.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = SQLModel.metadata


def _database_url() -> str:
    # str() armor: alembic's own annotations are incomplete, and strict
    # mypy must pass with or without them.
    override: Any = context.get_x_argument(as_dictionary=True).get("url")
    if override:
        return str(override)
    configured: Any = config.get_main_option("sqlalchemy.url")
    if configured:
        return str(configured)
    from backend.core.config import settings

    return settings.database_url


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_database_url())
    try:
        async with engine.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        # Always release pooled connections: an error path (e.g. a foreign
        # alembic_version from a v2 database file) must still exit instead
        # of hanging on loop-pinned aiosqlite handles.
        await engine.dispose()


asyncio.run(run_migrations_online())
