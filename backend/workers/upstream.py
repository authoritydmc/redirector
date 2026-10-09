"""arq task functions for upstream jobs (resync; purge lands here next)."""

from __future__ import annotations

from typing import Any

from backend.core.db import SessionFactory as ProductionSessionFactory
from backend.modules.jobs.runner import SessionFactory, execute_job


async def run_upstream_resync(ctx: dict[str, Any], job_id: int) -> None:
    """Execute one `upstream_resync` job row to completion.

    The session factory rides the arq context so tests can point the
    worker at a scratch DB; worker processes default to production settings.
    """
    factory: SessionFactory = ctx.get("session_factory") or ProductionSessionFactory
    await execute_job(factory, job_id)
