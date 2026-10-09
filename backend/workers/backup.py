"""arq task function for backup jobs (EPIC-04 task 7)."""

from __future__ import annotations

from typing import Any

from backend.core.db import SessionFactory as ProductionSessionFactory
from backend.modules.jobs.runner import SessionFactory, execute_job


async def run_backup_create(ctx: dict[str, Any], job_id: int) -> None:
    """Execute one `backup_create` job row to completion (see upstream.py)."""
    factory: SessionFactory = ctx.get("session_factory") or ProductionSessionFactory
    await execute_job(factory, job_id)


async def run_backup_restore(ctx: dict[str, Any], job_id: int) -> None:
    """Execute one `backup_restore` job row to completion (see upstream.py)."""
    factory: SessionFactory = ctx.get("session_factory") or ProductionSessionFactory
    await execute_job(factory, job_id)
