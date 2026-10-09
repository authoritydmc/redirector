"""arq broker tests: Redis-backed enqueue → burst worker → row (EPIC-06 task 2).

Needs Redis on 127.0.0.1:6379 (WSL `docker run redis:8-alpine`, or the CI
`redis` service); skipped otherwise. Test traffic stays on Redis DB 1 with
a unique queue per test, so a shared dev broker is never flushed.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("sqlmodel")
pytest.importorskip("arq")

from arq.connections import RedisSettings  # noqa: E402
from arq.worker import Worker  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import event  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402

from backend.core.db import get_session  # noqa: E402
from backend.main import create_app  # noqa: E402
from backend.models.entities import Upstream  # noqa: E402
from backend.modules.jobs.redis import redis_settings_from_url  # noqa: E402
from backend.modules.jobs.repository import JobRepository  # noqa: E402
from backend.modules.jobs.runner import ArqJobRunner  # noqa: E402
from backend.modules.upstreams.repository import UpstreamRepository  # noqa: E402
from backend.workers.upstream import run_upstream_resync  # noqa: E402


def _redis_available() -> bool:
    import socket

    sock = socket.socket()
    sock.settimeout(1)
    try:
        return sock.connect_ex(("127.0.0.1", 6379)) == 0
    except OSError:
        return False
    finally:
        sock.close()


pytestmark = pytest.mark.skipif(
    not _redis_available(),
    reason="needs Redis on 127.0.0.1:6379 (WSL docker or CI service)",
)


def _test_redis_settings(queue: str) -> tuple[RedisSettings, str]:
    return RedisSettings(host="127.0.0.1", port=6379, database=1), queue


def _new_queue() -> str:
    return f"test-arq-{uuid.uuid4().hex[:8]}"


class FakeResp:
    def __init__(self, url: str, status_code: int = 200) -> None:
        self.url = url
        self.status_code = status_code


class RoutingClient:
    """Fake httpx.AsyncClient resolving per-pattern: /gone/* 404s, rest 200."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.calls: list[str] = []

    async def __aenter__(self) -> RoutingClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def get(self, url: str, **kwargs: Any) -> FakeResp:
        self.calls.append(url)
        if "/gone" in url:
            return FakeResp(url, 404)
        return FakeResp(url, 200)


def _engine_for(tmp_path: Any):
    # Production-like pragmas (WAL + busy timeout): API sessions and worker
    # sessions read/write concurrently.
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/jobs-arq-test.db",
        connect_args={"timeout": 30},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _set_wal(dbapi_conn: Any, _record: Any) -> None:
        cursor = dbapi_conn.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
        finally:
            cursor.close()

    return engine


async def _init_db(engine) -> None:  # type: ignore[no-untyped-def]
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    # Drop the init-loop connection: pooled aiosqlite connections are
    # loop-pinned; serving opens fresh ones instead.
    await engine.dispose()


def _api_client(tmp_path: Any) -> tuple[TestClient, Any]:
    engine = _engine_for(tmp_path)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    asyncio.run(_init_db(engine))
    app = create_app()
    queue = _new_queue()
    redis_settings, _ = _test_redis_settings(queue)
    app.state.jobs = ArqJobRunner(factory, redis_settings, queue_name=queue)

    async def override_get_session():  # type: ignore[no-untyped-def]
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    return TestClient(app), (factory, queue)


async def _drain(factory, queue: str) -> None:  # type: ignore[no-untyped-def]
    """Run a burst worker until the test queue is empty (fails on timeout)."""
    redis_settings, _ = _test_redis_settings(queue)
    worker = Worker(
        [run_upstream_resync],
        redis_settings=redis_settings,
        queue_name=queue,
        burst=True,
        ctx={"session_factory": factory},
    )
    await asyncio.wait_for(worker.async_run(), timeout=60)


def _wait_terminal(client: TestClient, job_id: int, timeout: float = 30.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        body = client.get(f"/api/v1/jobs/{job_id}").json()
        if body["status"] in ("succeeded", "failed", "cancelled"):
            return body
        assert time.monotonic() < deadline, f"job {job_id} stuck in {body['status']}"
        time.sleep(0.1)


def _seed_upstream(client: TestClient) -> None:
    created = client.post(
        "/api/v1/upstreams",
        json={"name": "wiki", "base_url": "https://wiki.example", "fail_status_code": 404},
    )
    assert created.status_code == 201


def test_redis_settings_parsing() -> None:
    assert redis_settings_from_url("redis://localhost:6379/0").database == 0
    parsed = redis_settings_from_url("redis://:fixture-redis-pw@jobs.internal:6380/2")
    assert (parsed.host, parsed.port, parsed.database, parsed.password) == (
        "jobs.internal", 6380, 2, "fixture-redis-pw")
    assert redis_settings_from_url("rediss://h:6379/0").ssl is True
    with pytest.raises(ValueError):
        redis_settings_from_url("http://h:6379/0")


def test_worker_function_direct(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """Worker logic without a broker: queued row → succeeded + cache write."""
    monkeypatch.setattr("httpx.AsyncClient", RoutingClient)
    engine = _engine_for(tmp_path)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _main() -> tuple[str, list[str]]:
        await _init_db(engine)
        async with factory() as session:
            session.add(Upstream(name="wiki", base_url="https://wiki.example",
                                 fail_status_code=404))
            job = await JobRepository(session).create(
                "upstream_resync", {"upstream": "wiki", "patterns": ["good", "gone"]},
                total=2)
            assert job.id is not None
            job_id = job.id
        await run_upstream_resync({"session_factory": factory}, job_id)
        async with factory() as session:
            final = await JobRepository(session).get(job_id)
            assert final is not None
            cached = [r.pattern for r in
                      await UpstreamRepository(session).list_cache(upstream_name="wiki")]
            return final.status.value, cached

    status_value, cached = asyncio.run(_main())
    assert status_value == "succeeded"
    assert cached == ["good"]


def test_arq_full_loop(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """API(enqueue) → Redis → burst worker → row + cache (same surface)."""
    monkeypatch.setattr("httpx.AsyncClient", RoutingClient)
    client, (factory, queue) = _api_client(tmp_path)
    with client:
        _seed_upstream(client)
        enqueued = client.post(
            "/api/v1/jobs",
            json={"kind": "upstream_resync", "upstream": "wiki", "patterns": ["good", "gone"]},
        )
        assert enqueued.status_code == 202
        job_id = enqueued.json()["id"]
        assert enqueued.json()["status"] == "queued"

        asyncio.run(_drain(factory, queue))

        final = _wait_terminal(client, job_id)
        assert final["status"] == "succeeded"
        assert final["result"] == {"checked": 2, "updated": 1, "cleared": 0}
        cached = client.get("/api/v1/upstreams/cache").json()
        assert [r["pattern"] for r in cached] == ["good"]


def test_arq_cancel_guard(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """Cancelled-while-queued jobs stay cancelled when the worker gets them."""
    monkeypatch.setattr("httpx.AsyncClient", RoutingClient)
    client, (factory, queue) = _api_client(tmp_path)
    with client:
        _seed_upstream(client)
        job_id = client.post(
            "/api/v1/jobs",
            json={"kind": "upstream_resync", "upstream": "wiki", "patterns": ["good"]},
        ).json()["id"]

        cancelled = client.delete(f"/api/v1/jobs/{job_id}")
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"

        asyncio.run(_drain(factory, queue))

        assert client.get(f"/api/v1/jobs/{job_id}").json()["status"] == "cancelled"
        assert client.get("/api/v1/upstreams/cache").json() == []


def test_resync_scales_without_blocking_http(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any,
) -> None:
    """EPIC-06 resync criterion: enqueue returns at once, the job streams
    to done, and the payload is durable in Redis + the row before any
    worker runs. Sized at 100 patterns (property-identical to 1000, bounded
    for CI time); the 1000-scale soak belongs to the nightly load job."""
    monkeypatch.setattr("httpx.AsyncClient", RoutingClient)
    client, (factory, queue) = _api_client(tmp_path)
    with client:
        _seed_upstream(client)
        patterns = [f"bulk-{i:03d}" for i in range(100)]
        started = time.perf_counter()
        enqueued = client.post(
            "/api/v1/jobs",
            json={"kind": "upstream_resync", "upstream": "wiki", "patterns": patterns},
        )
        enqueue_s = time.perf_counter() - started
        assert enqueued.status_code == 202
        assert enqueue_s < 2.0, f"enqueue blocked HTTP for {enqueue_s:.2f}s"
        job_id = enqueued.json()["id"]
        # Durable before any worker runs: persisted row, still queued.
        assert client.get(f"/api/v1/jobs/{job_id}").json()["status"] == "queued"

        asyncio.run(_drain(factory, queue))

        final = _wait_terminal(client, job_id, timeout=120.0)
        assert final["status"] == "succeeded"
        assert final["result"] == {"checked": 100, "updated": 100, "cleared": 0}
