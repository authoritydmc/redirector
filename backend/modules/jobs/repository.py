"""Persistence for background job records (one row per job)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.entities import Job, JobStatus


class JobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, kind: str, payload: dict[str, Any], total: int) -> Job:
        job = Job(kind=kind, status=JobStatus.QUEUED, total=total, payload=payload)
        self.session.add(job)
        await self.session.commit()
        await self.session.refresh(job)
        return job

    async def get(self, job_id: int) -> Job | None:
        return (await self.session.execute(
            select(Job).where(Job.id == job_id))).scalar_one_or_none()

    async def list_recent(self, limit: int = 50) -> list[Job]:
        """Newest jobs first (admin UI polling surface)."""
        # Table-column form (not Job.id.desc()): newest-first over the PK
        # stays well-typed across SQLAlchemy versions (see CI drift note in
        # PR #114 — unpinned mypy/SQLAlchemy disagree on Optional .desc()).
        rows = await self.session.execute(
            select(Job).order_by(Job.__table__.c.id.desc()).limit(limit))
        return list(rows.scalars().all())

    async def _save(self, job: Job) -> Job:
        job.updated_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(job)
        return job

    async def mark_running(self, job: Job, total: int) -> Job:
        job.status = JobStatus.RUNNING
        job.total = total
        job.done = 0
        return await self._save(job)

    async def update_progress(self, job: Job, done: int) -> Job:
        job.done = done
        return await self._save(job)

    async def succeed(self, job: Job, result: dict[str, Any]) -> Job:
        job.status = JobStatus.SUCCEEDED
        job.done = job.total
        job.result = result
        return await self._save(job)

    async def fail(self, job: Job, error: str) -> Job:
        job.status = JobStatus.FAILED
        job.error = error
        return await self._save(job)

    async def cancel(self, job: Job) -> Job:
        job.status = JobStatus.CANCELLED
        job.error = "cancelled"
        return await self._save(job)

    async def fail_stale(self, error: str) -> int:
        """Fail jobs left non-terminal by a previous process (boot reaping).

        The in-process runner cannot resume them; a durable broker (arq,
        EPIC-06 task 2 remainder) will requeue instead of failing.
        """
        rows = (await self.session.execute(
            select(Job).where(Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]))
        )).scalars().all()
        now = datetime.now(UTC)
        for job in rows:
            job.status = JobStatus.FAILED
            job.error = error
            job.updated_at = now
        await self.session.commit()
        return len(rows)
