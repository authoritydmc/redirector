"""Background jobs API under /api/v1/jobs (EPIC-06 task 2).

POST   /api/v1/jobs          — enqueue (202 + job row; worker runs async)
GET    /api/v1/jobs          — list recent jobs, newest first
GET    /api/v1/jobs/{id}     — status/progress/result poll
DELETE /api/v1/jobs/{id}     — cancel a queued/running job
GET    /api/v1/jobs/{id}/events — SSE progress stream until terminal state
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.db import get_session
from backend.core.errors import AppError
from backend.core.security import ADMIN_WRITE, RequireScopes
from backend.modules.backup.service import backup_dir, backup_path, sanitize_label
from backend.modules.jobs.repository import JobRepository
from backend.modules.jobs.runner import TERMINAL, JobRunner
from backend.modules.jobs.schemas import (
    BackupEnqueue,
    BackupRestoreEnqueue,
    JobEnqueue,
    JobRead,
)

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


def get_runner(request: Request) -> JobRunner:
    runner: JobRunner = request.app.state.jobs
    return runner


async def get_job_repo(session: AsyncSession = Depends(get_session)) -> JobRepository:
    return JobRepository(session)


@router.post("", response_model=JobRead, status_code=status.HTTP_202_ACCEPTED, summary="Enqueue a background job")
async def enqueue_job(
    body: JobEnqueue,
    _admin: Annotated[dict[str, Any], Depends(RequireScopes(ADMIN_WRITE))],
    runner: JobRunner = Depends(get_runner),
) -> JobRead:
    if isinstance(body, BackupEnqueue):
        label = sanitize_label(body.label)
        if body.label is not None and label is None:
            raise AppError(
                "Invalid backup label",
                status=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Label must match [a-z0-9-] (max 32 chars)",
                code="jobs:invalid-label",
            )
        job = await runner.enqueue(body.kind, {"label": label}, total=0)
    elif isinstance(body, BackupRestoreEnqueue):
        if backup_path(backup_dir(), body.name) is None:
            raise AppError(
                "Backup not found",
                status=status.HTTP_404_NOT_FOUND,
                detail=f"Backup '{body.name}' not found",
                code="backup:not-found",
            )
        job = await runner.enqueue(body.kind, {"name": body.name}, total=0)
    else:
        job = await runner.enqueue(
            body.kind,
            {"upstream": body.upstream, "patterns": body.patterns},
            total=len(body.patterns),
        )
    return JobRead.model_validate(job)


@router.get("", response_model=list[JobRead], summary="List recent jobs")
async def list_jobs(
    limit: int = Query(50, ge=1, le=200, description="Max jobs, newest first"),
    repo: JobRepository = Depends(get_job_repo),
) -> list[JobRead]:
    return [JobRead.model_validate(j) for j in await repo.list_recent(limit)]


@router.get("/{job_id}", response_model=JobRead, summary="Get job status")
async def get_job(job_id: int, repo: JobRepository = Depends(get_job_repo)) -> JobRead:
    job = await repo.get(job_id)
    if job is None:
        raise AppError(
            "Job not found",
            status=status.HTTP_404_NOT_FOUND,
            detail=f"Job id {job_id} not found",
            code="jobs:not-found",
        )
    return JobRead.model_validate(job)


@router.delete("/{job_id}", response_model=JobRead, summary="Cancel a queued/running job")
async def cancel_job(
    job_id: int,
    _admin: Annotated[dict[str, Any], Depends(RequireScopes(ADMIN_WRITE))],
    repo: JobRepository = Depends(get_job_repo),
    runner: JobRunner = Depends(get_runner),
) -> JobRead:
    job = await repo.get(job_id)
    if job is None:
        raise AppError(
            "Job not found",
            status=status.HTTP_404_NOT_FOUND,
            detail=f"Job id {job_id} not found",
            code="jobs:not-found",
        )
    if job.status in TERMINAL:
        raise AppError(
            "Job already terminal",
            status=status.HTTP_409_CONFLICT,
            detail=f"Job id {job_id} is {job.status.value}; only queued/running jobs cancel",
            code="jobs:already-terminal",
        )
    cancelled = await runner.cancel_job(job_id)
    assert cancelled is not None  # row existed a moment ago; runner never deletes
    return JobRead.model_validate(cancelled)


@router.get("/{job_id}/events", summary="Stream job progress via SSE")
async def stream_job_events(
    job_id: int,
    repo: JobRepository = Depends(get_job_repo),
) -> StreamingResponse:
    job = await repo.get(job_id)
    if job is None:
        raise AppError(
            "Job not found",
            status=status.HTTP_404_NOT_FOUND,
            detail=f"Job id {job_id} not found",
            code="jobs:not-found",
        )

    async def event_generator() -> AsyncIterator[str]:
        # Poll the job row (the runner persists progress there); close on
        # terminal state so the stream is finite like the check stream.
        # Snapshot fields before rolling back: rollback expires ORM
        # attributes, and lazy-refreshing them afterwards has no greenlet
        # context. Never hold a minutes-long read transaction open while
        # job tasks write.
        for _ in range(600):  # ~5 min cap
            current = await repo.get(job_id)
            if current is None:  # pragma: no cover - row deleted mid-stream
                break
            snapshot = (current.status, current.done, current.total)
            await repo.session.rollback()
            job_status, done, total = snapshot
            yield f"data: {json.dumps({'job_id': job_id, 'status': job_status.value, 'done': done, 'total': total})}\n\n"
            if job_status in TERMINAL:
                break
            await asyncio.sleep(0.5)
        yield f"data: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
