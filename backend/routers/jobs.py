"""Background jobs API under /api/v1/jobs (EPIC-06 task 2).

POST /api/v1/jobs          — enqueue (202 + job row; worker runs async)
GET  /api/v1/jobs/{id}     — status/progress/result poll
GET  /api/v1/jobs/{id}/events — SSE progress stream until terminal state
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.db import get_session
from backend.core.errors import AppError
from backend.models.entities import JobStatus
from backend.modules.jobs.repository import JobRepository
from backend.modules.jobs.runner import JobRunner
from backend.modules.jobs.schemas import JobEnqueue, JobRead

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])

TERMINAL = (JobStatus.SUCCEEDED, JobStatus.FAILED)


def get_runner(request: Request) -> JobRunner:
    runner: JobRunner = request.app.state.jobs
    return runner


async def get_job_repo(session: AsyncSession = Depends(get_session)) -> JobRepository:
    return JobRepository(session)


@router.post("", response_model=JobRead, status_code=status.HTTP_202_ACCEPTED, summary="Enqueue a background job")
async def enqueue_job(body: JobEnqueue, runner: JobRunner = Depends(get_runner)) -> JobRead:
    job = await runner.enqueue(
        body.kind,
        {"upstream": body.upstream, "patterns": body.patterns},
        total=len(body.patterns),
    )
    return JobRead.model_validate(job)


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
        for _ in range(600):  # ~5 min cap
            current = await repo.get(job_id)
            if current is None:  # pragma: no cover - row deleted mid-stream
                break
            yield f"data: {json.dumps({'job_id': job_id, 'status': current.status.value, 'done': current.done, 'total': current.total})}\n\n"
            if current.status in TERMINAL:
                break
            await asyncio.sleep(0.5)
        yield f"data: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
