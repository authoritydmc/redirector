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
