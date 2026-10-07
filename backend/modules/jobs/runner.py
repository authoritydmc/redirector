"""In-process background job runner (EPIC-06 task 2, first slice).

Runs jobs as tracked asyncio tasks with per-job sessions; status and
progress live on the job row, so polling/SSE works uniformly. A future
arq + Redis runner implements the same enqueue/status surface — routers
only depend on `JobRunner`, never on the execution backend.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.entities import Job
from backend.modules.jobs.repository import JobRepository
from backend.modules.upstreams.repository import UpstreamRepository
from backend.modules.upstreams.service import UpstreamCheckService

logger = logging.getLogger("redirector")

SessionFactory = Callable[[], AsyncSession]


class JobRunner:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._factory = session_factory
        self._tasks: dict[int, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    async def enqueue(self, kind: str, payload: dict[str, Any], total: int) -> Job:
        """Persist a queued job row, then hand it to a tracked task."""
        async with self._factory() as session:
            job = await JobRepository(session).create(kind, payload, total)
            assert job.id is not None
            job_id = job.id
        task = asyncio.get_running_loop().create_task(self._run(job_id))
        async with self._lock:
            self._tasks[job_id] = task

        def _done(finished: asyncio.Task[None]) -> None:
            self._forget(job_id, finished)

        task.add_done_callback(_done)
        return job

    def _forget(self, job_id: int, task: asyncio.Task[None]) -> None:
        if not task.cancelled() and task.exception() is not None:
            logger.exception("background job %d crashed", job_id)
        # Best-effort cleanup; the loop may already be closing on shutdown.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._tasks.pop(job_id, None)
        else:
            loop.call_soon(self._tasks.pop, job_id, None)

    async def _run(self, job_id: int) -> None:
        async with self._factory() as session:
            repo = JobRepository(session)
            job = await repo.get(job_id)
            if job is None:
                logger.error("background job %d vanished before start", job_id)
                return
            try:
                if job.kind == "upstream_resync":
                    await self._resync(session, job)
                else:
                    raise ValueError(f"unknown job kind: {job.kind}")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await repo.fail(job, f"{type(exc).__name__}: {exc}")

    async def _resync(self, session: AsyncSession, job: Job) -> None:
        repo = JobRepository(session)
        payload = job.payload
        upstream_name = payload.get("upstream")
        patterns = payload.get("patterns")
        if not isinstance(upstream_name, str) or not isinstance(patterns, list):
            await repo.fail(job, "upstream_resync payload needs {upstream: str, patterns: list}")
            return
        up_repo = UpstreamRepository(session)
        upstream = await up_repo.get_by_name(upstream_name)
        if upstream is None:
            await repo.fail(job, f"Upstream '{upstream_name}' not found")
            return
        await repo.mark_running(job, len(patterns))
        service = UpstreamCheckService(up_repo)

        async def _progress(done: int, total: int) -> None:
            job.done = done
            job.total = total
            await session.commit()

        timeout = httpx.Timeout(3.0, connect=3.0)
        limits = httpx.Limits(max_connections=100, max_keepalive_connections=20)
        async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
            _, updated, cleared = await service.refresh_patterns(
                upstream, [str(p) for p in patterns], client, on_progress=_progress,
            )
        await repo.succeed(job, {"checked": len(patterns), "updated": updated, "cleared": cleared})

    async def shutdown(self) -> None:
        """Cancel tracked tasks (lifespan shutdown); rows keep last progress."""
        async with self._lock:
            tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
