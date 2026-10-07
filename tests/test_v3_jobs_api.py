"""Tests for background jobs: enqueue → run → poll/SSE (EPIC-06 task 2).

All network I/O is faked. Each test gets a scratch file DB (shared by API
requests and job tasks) — no shared-memory/StaticPool cross-thread sharing.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("sqlmodel")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402

from backend.core.db import get_session  # noqa: E402
from backend.main import create_app  # noqa: E402
from backend.modules.jobs.runner import JobRunner  # noqa: E402


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


def _api_client(tmp_path: Any) -> TestClient:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/jobs-test.db")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app()
    app.state.jobs = JobRunner(factory)

    async def _init() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        # Drop the init-loop connection: pooled aiosqlite connections are
        # loop-pinned, and reusing this one from the TestClient portal loop
        # hangs interpreter exit. Serving opens fresh connections instead.
        await engine.dispose()

    asyncio.run(_init())

    async def override_get_session():  # type: ignore[no-untyped-def]
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    return TestClient(app)


def _seed_upstream(client: TestClient) -> None:
    created = client.post(
        "/api/v1/upstreams",
        json={"name": "wiki", "base_url": "https://wiki.example", "fail_status_code": 404},
    )
    assert created.status_code == 201


def _wait_terminal(client: TestClient, job_id: int, timeout: float = 10.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        body = client.get(f"/api/v1/jobs/{job_id}").json()
        if body["status"] in ("succeeded", "failed"):
            return body
        assert time.monotonic() < deadline, f"job {job_id} stuck in {body['status']}"
        time.sleep(0.05)


def test_enqueue_resync_job_completes(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setattr("httpx.AsyncClient", RoutingClient)
    with _api_client(tmp_path) as client:
        _seed_upstream(client)
        enqueued = client.post(
            "/api/v1/jobs",
            json={"kind": "upstream_resync", "upstream": "wiki", "patterns": ["good", "gone"]},
        )
        assert enqueued.status_code == 202
        job_id = enqueued.json()["id"]
        assert enqueued.json()["status"] in ("queued", "running")

        final = _wait_terminal(client, job_id)
        assert final["status"] == "succeeded"
        assert final["result"] == {"checked": 2, "updated": 1, "cleared": 0}

        cached = client.get("/api/v1/upstreams/cache").json()
        assert [r["pattern"] for r in cached] == ["good"]


def test_job_status_404(tmp_path: Any) -> None:
    with _api_client(tmp_path) as client:
        res = client.get("/api/v1/jobs/999")
        assert res.status_code == 404
        assert res.json()["code"] == "jobs:not-found"


def test_enqueue_unknown_upstream_fails_job(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setattr("httpx.AsyncClient", RoutingClient)
    with _api_client(tmp_path) as client:
        enqueued = client.post(
            "/api/v1/jobs",
            json={"kind": "upstream_resync", "upstream": "nope", "patterns": ["x"]},
        )
        assert enqueued.status_code == 202
        final = _wait_terminal(client, enqueued.json()["id"])
        assert final["status"] == "failed"
        assert "nope" in (final["error"] or "")


def test_enqueue_validation(tmp_path: Any) -> None:
    with _api_client(tmp_path) as client:
        assert client.post(
            "/api/v1/jobs",
            json={"kind": "bogus", "upstream": "wiki", "patterns": ["x"]},
        ).status_code == 422
        assert client.post(
            "/api/v1/jobs",
            json={"kind": "upstream_resync", "upstream": "wiki", "patterns": []},
        ).status_code == 422


def test_events_stream_ends_terminal(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setattr("httpx.AsyncClient", RoutingClient)
    with _api_client(tmp_path) as client:
        _seed_upstream(client)
        job_id = client.post(
            "/api/v1/jobs",
            json={"kind": "upstream_resync", "upstream": "wiki", "patterns": ["good"]},
        ).json()["id"]

        resp = client.get(f"/api/v1/jobs/{job_id}/events")
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        payloads = [
            json.loads(line[len("data: "):])
            for line in resp.text.splitlines()
            if line.startswith("data: ")
        ]
        assert payloads[-1] == {"done": True}
        statuses = {p.get("status") for p in payloads if "status" in p}
        assert "succeeded" in statuses
        assert all(p.get("job_id") == job_id for p in payloads if "status" in p)

        assert client.get("/api/v1/jobs/999/events").status_code == 404
