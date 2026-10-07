"""Tests for UpstreamCheckService branches and the SSE stream endpoint.

All network I/O is faked — these tests pin the httpx contract (notably: no
per-request `verify=` kwarg, which httpx.AsyncClient.get does not accept).
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


def _api_client() -> TestClient:
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
    with _api_client() as client:
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


def _seed_upstream(client: TestClient) -> None:
    created = client.post(
        "/api/v1/upstreams",
        json={
            "name": "wiki",
            "base_url": "https://wiki.example",
            "fail_status_code": 404,
        },
    )
    assert created.status_code == 201


def test_resync_single_found_writes_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("httpx.AsyncClient", RoutingClient)
    with _api_client() as client:
        _seed_upstream(client)
        res = client.post(
            "/api/v1/upstreams/cache/resync",
            json={"upstream": "wiki", "pattern": "good"},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["checked"] == 1
        assert body["updated"] == 1
        assert body["cleared"] == 0
        assert body["results"][0]["status"] == "found"

        cached = client.get("/api/v1/upstreams/cache").json()
        assert len(cached) == 1
        assert cached[0]["pattern"] == "good"
        assert cached[0]["resolved_url"] == "https://wiki.example/good"


def test_resync_single_not_found_stores_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("httpx.AsyncClient", RoutingClient)
    with _api_client() as client:
        _seed_upstream(client)
        assert client.post(
            "/api/v1/upstreams/cache/resync",
            json={"upstream": "wiki", "pattern": "good"},
        ).status_code == 200

        res = client.post(
            "/api/v1/upstreams/cache/resync",
            json={"upstream": "wiki", "pattern": "gone"},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["updated"] == 0
        assert body["cleared"] == 0  # nothing was stored for a failing pattern
        assert body["results"][0]["status"] == "not_found"
        assert len(client.get("/api/v1/upstreams/cache").json()) == 1


def test_resync_clears_previously_cached_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """A row cached earlier is dropped once the upstream 404s it."""
    calls: list[str] = []

    class FlipFlopClient(RoutingClient):
        async def get(self, url: str, **kwargs: Any) -> FakeResp:
            # First request (seed) succeeds, later ones 404.
            calls.append(url)
            if len(calls) == 1:
                return FakeResp(url, 200)
            return FakeResp(url, 404)

    monkeypatch.setattr("httpx.AsyncClient", FlipFlopClient)
    with _api_client() as client:
        _seed_upstream(client)
        assert client.post(
            "/api/v1/upstreams/cache/resync",
            json={"upstream": "wiki", "pattern": "flip"},
        ).json()["updated"] == 1
        assert len(client.get("/api/v1/upstreams/cache").json()) == 1

        res = client.post(
            "/api/v1/upstreams/cache/resync",
            json={"upstream": "wiki", "pattern": "flip"},
        ).json()
        assert res["updated"] == 0
        assert res["cleared"] == 1
        assert client.get("/api/v1/upstreams/cache").json() == []


def test_resync_all_refreshes_cached_patterns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("httpx.AsyncClient", RoutingClient)
    with _api_client() as client:
        _seed_upstream(client)
        for pat in ("good-1", "good-2"):
            assert client.post(
                "/api/v1/upstreams/cache/resync",
                json={"upstream": "wiki", "pattern": pat},
            ).status_code == 200

        res = client.post("/api/v1/upstreams/cache/resync", json={"upstream": "wiki"})
        assert res.status_code == 200
        body = res.json()
        assert body["checked"] == 2
        assert body["updated"] == 2
        assert body["cleared"] == 0


def test_resync_unknown_upstream_404() -> None:
    with _api_client() as client:
        res = client.post(
            "/api/v1/upstreams/cache/resync",
            json={"upstream": "nope", "pattern": "x"},
        )
        assert res.status_code == 404


def test_purge_single_cache_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("httpx.AsyncClient", RoutingClient)
    with _api_client() as client:
        _seed_upstream(client)
        assert client.post(
            "/api/v1/upstreams/cache/resync",
            json={"upstream": "wiki", "pattern": "good"},
        ).status_code == 200

        purged = client.delete("/api/v1/upstreams/cache/wiki/good")
        assert purged.status_code == 200
        assert purged.json() == {"success": True, "purged": 1}
        assert client.get("/api/v1/upstreams/cache").json() == []

        # Idempotent: missing entry purges nothing.
        again = client.delete("/api/v1/upstreams/cache/wiki/good")
        assert again.json() == {"success": True, "purged": 0}


def test_check_logs_list_and_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("httpx.AsyncClient", RoutingClient)
    with _api_client() as client:
        _seed_upstream(client)
        client.post(
            "/api/v1/upstreams/cache/resync",
            json={"upstream": "wiki", "pattern": "good"},
        )
        client.post(
            "/api/v1/upstreams/cache/resync",
            json={"upstream": "wiki", "pattern": "gone"},
        )

        logs = client.get("/api/v1/upstreams/check-logs").json()
        by_pattern = {e["pattern"]: e for e in logs}
        assert by_pattern["good"]["result"] == "success"
        assert by_pattern["gone"]["result"] == "not_found"
        assert all(e["upstream_name"] == "wiki" for e in logs)

        filtered = client.get("/api/v1/upstreams/check-logs?upstream=other").json()
        assert filtered == []

        limited = client.get("/api/v1/upstreams/check-logs?limit=1").json()
        assert len(limited) == 1


def test_clear_check_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("httpx.AsyncClient", RoutingClient)
    with _api_client() as client:
        _seed_upstream(client)
        client.post(
            "/api/v1/upstreams/cache/resync",
            json={"upstream": "wiki", "pattern": "good"},
        )
        assert len(client.get("/api/v1/upstreams/check-logs").json()) > 0

        # Filtered clear leaves other upstreams alone (none here: 0).
        assert client.delete("/api/v1/upstreams/check-logs?upstream=other").json() == {
            "success": True,
            "purged": 0,
        }

        cleared = client.delete("/api/v1/upstreams/check-logs").json()
        assert cleared["success"] is True
        assert cleared["purged"] >= 1
        assert client.get("/api/v1/upstreams/check-logs").json() == []


def test_sso_outcome_is_never_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """SSO/login targets must never land in the cache, whatever the
    upstream's skip_sso_cache flag says (default False here)."""

    class SsoClient(RoutingClient):
        async def get(self, url: str, **kwargs: Any) -> FakeResp:
            self.calls.append(url)
            return FakeResp("https://login.example/sso?x=1", 200)

    monkeypatch.setattr("httpx.AsyncClient", SsoClient)
    with _api_client() as client:
        _seed_upstream(client)
        res = client.post(
            "/api/v1/upstreams/cache/resync",
            json={"upstream": "wiki", "pattern": "sso-page"},
        ).json()
        assert res["updated"] == 0
        assert res["results"][0]["status"] == "sso_required"
        assert client.get("/api/v1/upstreams/cache").json() == []


class SlowClient(FakeClient):
    """Stub upstream with latency: every check takes `delay` seconds."""

    def __init__(self, delay: float = 0.5) -> None:
        super().__init__(FakeResp("https://slow.example/x", 200))
        self.delay = delay

    async def get(self, url: str, **kwargs: Any) -> FakeResp:
        self.calls.append({"url": url, **kwargs})
        await asyncio.sleep(self.delay)
        return self.resp


def test_check_all_fans_out_concurrently() -> None:
    """EPIC-06 acceptance shape: 3 x 500ms stubs must take ~max, not sum.

    Sequential would need >= 1.5 s; concurrent shares one session, so this
    also proves the repo-write lock keeps concurrent checks safe.
    """

    async def _main() -> tuple[list[UpstreamCheckResult], float]:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            service = UpstreamCheckService(UpstreamRepository(session))
            ups = [
                Upstream(name=f"u{i}", base_url="https://slow.example")
                for i in range(3)
            ]
            started = time.perf_counter()
            results = await service.check_all(ups, "x", SlowClient())  # type: ignore[arg-type]
            return results, time.perf_counter() - started

    results, elapsed = asyncio.run(_main())
    assert [r.upstream_name for r in results] == ["u0", "u1", "u2"]
    assert all(r.status == "found" for r in results)
    assert elapsed < 1.4, f"fan-out took {elapsed:.2f}s, expected ~0.5s (max, not sum)"
