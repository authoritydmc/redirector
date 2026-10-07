"""Background job runners (EPIC-06 task 2).

`JobRunner` executes jobs as tracked in-process asyncio tasks (default, no
broker needed). `ArqJobRunner` defers execution to `arq` workers via Redis
while keeping the same DB row + SSE surface — routers only depend on
`JobRunner`, never on the execution backend. Shared execution logic lives
in `execute_job`, used by both backends (and by worker-process entrypoints).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

import httpx
from arq.connections import ArqRedis, RedisSettings, create_pool
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.entities import Job, JobStatus
from backend.modules.jobs.repository import JobRepository
from backend.modules.upstreams.repository import UpstreamRepository
from backend.modules.upstreams.service import UpstreamCheckService

logger = logging.getLogger("redirector")

SessionFactory = Callable[[], AsyncSession]

TERMINAL = (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED)
ACTIVE = (JobStatus.QUEUED, JobStatus.RUNNING)


async def execute_job(session_factory: SessionFactory, job_id: int) -> None:
    """Run one queued job to a terminal row state (both backends funnel here).

    Skips rows that left QUEUED/RUNNING meanwhile (cancelled by an admin, or
    a duplicate delivery) so cancellation is honored even if the worker
    already dequeued the job.
    """
    async with session_factory() as session:
        repo = JobRepository(session)
        job = await repo.get(job_id)
        if job is None:
            logger.error("background job %d vanished before start", job_id)
            return
        if job.status not in ACTIVE:
            logger.info("background job %d already %s, skipping", job_id, job.status.value)
            return
        try:
            if job.kind == "upstream_resync":
                await _resync(session, repo, job)
            else:
                raise ValueError(f"unknown job kind: {job.kind}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await repo.fail(job, f"{type(exc).__name__}: {exc}")


async def _resync(session: AsyncSession, repo: JobRepository, job: Job) -> None:
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
        await execute_job(self._factory, job_id)

    async def shutdown(self) -> None:
        """Cancel tracked tasks (lifespan shutdown); rows keep last progress."""
        async with self._lock:
            tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def cancel_job(self, job_id: int) -> Job | None:
        """Cancel a queued/running job; terminal rows pass through untouched."""
        async with self._lock:
            task = self._tasks.pop(job_id, None)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        async with self._factory() as session:
            repo = JobRepository(session)
            job = await repo.get(job_id)
            if job is None or job.status in TERMINAL:
                return job
            return await repo.cancel(job)

    async def reap_stale(self) -> int:
        """Fail rows left non-terminal by a previous process (boot only)."""
        async with self._factory() as session:
            return await JobRepository(session).fail_stale(
                "server restarted before completion")


class ArqJobRunner(JobRunner):
    """Broker-backed runner: the API persists the row, `arq` workers run it.

    Cancellation of an already-dequeued job is honored by the status guard
    in `execute_job` (the worker skips non-active rows). Disabling the
    worker while the API runs leaves jobs queued — visible, re-triable
    once a worker appears, and reaped as failed only on API restart.
    """

    ARQ_FUNCTION = "run_upstream_resync"

    def __init__(
        self,
        session_factory: SessionFactory,
        redis_settings: RedisSettings,
        queue_name: str = "arq:queue",
    ) -> None:
        super().__init__(session_factory)
        self._redis_settings = redis_settings
        self._queue_name = queue_name
        self._pool: ArqRedis | None = None
        self._pool_lock = asyncio.Lock()

    async def _pool_conn(self) -> ArqRedis:
        async with self._pool_lock:
            if self._pool is None:
                self._pool = await create_pool(self._redis_settings)
            return self._pool

    async def enqueue(self, kind: str, payload: dict[str, Any], total: int) -> Job:
        """Persist a queued job row, then hand it to the Redis broker."""
        async with self._factory() as session:
            job = await JobRepository(session).create(kind, payload, total)
            assert job.id is not None
            job_id = job.id
        pool = await self._pool_conn()
        await pool.enqueue_job(self.ARQ_FUNCTION, job_id, _queue_name=self._queue_name)
        return job

    async def shutdown(self) -> None:
        await super().shutdown()
        async with self._pool_lock:
            if self._pool is not None:
                await self._pool.aclose()
                self._pool = None
