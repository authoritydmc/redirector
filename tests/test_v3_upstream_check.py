"""Tests for UpstreamCheckService branches and the SSE stream endpoint.

All network I/O is faked — these tests pin the httpx contract (notably: no
per-request `verify=` kwarg, which httpx.AsyncClient.get does not accept).
"""

from __future__ import annotations

import asyncio
import json
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
from backend.models.entities import Upstream  # noqa: E402
from backend.modules.upstreams.repository import UpstreamRepository  # noqa: E402
from backend.modules.upstreams.schemas import UpstreamCheckResult  # noqa: E402
from backend.modules.upstreams.service import UpstreamCheckService  # noqa: E402


class FakeResp:
    def __init__(self, url: str, status_code: int = 200) -> None:
        self.url = url
        self.status_code = status_code


class FakeClient:
    """Duck-typed httpx.AsyncClient. Records kwargs to pin the call contract."""

    def __init__(self, resp: FakeResp | None = None, exc: Exception | None = None) -> None:
        self.resp = resp or FakeResp("https://up.example/target")
        self.exc = exc
        self.calls: list[dict[str, Any]] = []

    async def get(self, url: str, **kwargs: Any) -> FakeResp:
        self.calls.append({"url": url, **kwargs})
        if self.exc is not None:
            raise self.exc
        return self.resp


def _run_check(up_kwargs: dict[str, Any], client: FakeClient) -> UpstreamCheckResult:
    """Run check_single on a fresh in-memory DB; return the result."""

    async def _main() -> UpstreamCheckResult:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            service = UpstreamCheckService(UpstreamRepository(session))
            base: dict[str, Any] = {"name": "wiki", "base_url": "https://wiki.example"}
            base.update(up_kwargs)
            return await service.check_single(Upstream(**base), "x", client)  # type: ignore[arg-type]

    return asyncio.run(_main())


def test_check_skipped_without_base_url() -> None:
    res = _run_check({"base_url": ""}, FakeClient())
    assert res.status == "skipped"
    assert res.check_url == ""


def test_check_found() -> None:
    client = FakeClient(FakeResp("https://wiki.example/x", 200))
    res = _run_check({}, client)
    assert res.status == "found"
    assert res.target_url == "https://wiki.example/x"
    assert res.cached is True
    # Contract: timeout budget, no per-request verify kwarg.
    assert client.calls[0]["timeout"] == 3.0
    assert "verify" not in client.calls[0]


def test_check_sso_required() -> None:
    client = FakeClient(FakeResp("https://login.example/sso?x=1", 200))
    res = _run_check({}, client)
    assert res.status == "sso_required"
    assert res.cached is False


def test_check_not_found_via_fail_url() -> None:
    client = FakeClient(FakeResp("https://wiki.example/404", 200))
    res = _run_check({"fail_url": "https://wiki.example/404"}, client)
    assert res.status == "not_found"


def test_check_not_found_via_fail_status() -> None:
    client = FakeClient(FakeResp("https://wiki.example/missing", 404))
    res = _run_check({"fail_status_code": 404}, client)
    assert res.status == "not_found"


def test_check_error_on_connection_failure() -> None:
    client = FakeClient(exc=ConnectionError("boom"))
    res = _run_check({}, client)
    assert res.status == "error"
    assert "boom" in res.message


def test_check_insecure_upstream_uses_dedicated_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    class InsecureClient(FakeClient):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(FakeResp("https://insecure.example/x", 200))
            seen.update(kwargs)

        async def __aenter__(self) -> InsecureClient:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

    monkeypatch.setattr("httpx.AsyncClient", InsecureClient)
    res = _run_check({"verify_ssl": False}, FakeClient())
    assert res.status == "found"
    assert seen.get("verify") is False


def _sse_client() -> TestClient:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app()

    async def override_get_session():  # type: ignore[no-untyped-def]
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    return TestClient(app)


def test_stream_endpoint_emits_result_and_done(monkeypatch: pytest.MonkeyPatch) -> None:
    class StreamClient(FakeClient):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(FakeResp("https://wiki.example/stream-me", 200))

        async def __aenter__(self) -> StreamClient:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

    monkeypatch.setattr("httpx.AsyncClient", StreamClient)
    with _sse_client() as client:
        created = client.post(
            "/api/v1/upstreams", json={"name": "wiki", "base_url": "https://wiki.example"}
        )
        assert created.status_code == 201

        resp = client.get("/api/v1/upstreams/check/stream/stream-me")
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        payloads = [
            json.loads(line[len("data: "):])
            for line in resp.text.splitlines()
            if line.startswith("data: ")
        ]
        assert payloads[0]["message"].startswith("Starting check")
        assert any(p.get("status") == "found" for p in payloads)
        assert payloads[-1] == {"done": True}
