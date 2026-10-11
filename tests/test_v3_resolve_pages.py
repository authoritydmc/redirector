"""Router-level resolve page tests: JSON stays JSON, browsers get a
create-this-link page on unknown patterns."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jwt")
sqlmodel = pytest.importorskip("sqlmodel")

from typing import Any  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E401,E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backend.core.config import settings  # noqa: E402
from backend.core.db import get_session  # noqa: E402
from backend.main import create_app  # noqa: E402
from backend.modules.upstreams.schemas import UpstreamCheckResult  # noqa: E402
from backend.modules.upstreams.service import UpstreamCheckService  # noqa: E402


@pytest.fixture()
def client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    app = create_app()

    async def override_get_session():
        async with engine.begin() as conn:
            await conn.run_sync(sqlmodel.SQLModel.metadata.create_all)
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as tc:
        yield tc
    import asyncio

    asyncio.run(engine.dispose())


def _admin_headers(client: TestClient) -> dict[str, str]:
    login = client.post("/api/v1/auth/login", json={"password": settings.admin_password})
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _open_creation(client: TestClient) -> None:
    put = client.put("/api/v1/site/policy", headers=_admin_headers(client),
                     json={"public_actions": ["shortcuts.create"]})
    assert put.status_code == 200


def test_unknown_pattern_is_json_by_default(client: TestClient) -> None:
    resp = client.get("/no-such-link-xyz")
    assert resp.status_code == 404
    assert "json" in resp.headers["content-type"]
    assert resp.json()["code"] == "resolve:not_found"


def test_unknown_pattern_renders_create_page_for_browsers(client: TestClient) -> None:
    _open_creation(client)
    client.post("/api/v1/shortcuts", headers=_admin_headers(client),
                json={"pattern": "guides", "target": "https://example.com/guides"})
    resp = client.get("/guid", headers={"Accept": "text/html,application/xhtml+xml"})
    assert resp.status_code == 404
    assert "text/html" in resp.headers["content-type"]
    assert "r/guid" in resp.text
    assert "/?new=guid" in resp.text
    assert "guides" in resp.text  # close match suggested
    assert "no upstreams are configured" in resp.text


def test_missing_page_hints_sign_in_when_creation_gated(client: TestClient) -> None:
    resp = client.get("/guid", headers={"Accept": "text/html"})
    assert resp.status_code == 404
    assert "sign in as admin" in resp.text
    assert "/?new=guid" not in resp.text


def test_known_pattern_still_redirects(client: TestClient,
                                       monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIRECTOR_AUTO_REDIRECT_DELAY", "0")
    client.post("/api/v1/shortcuts", headers=_admin_headers(client),
                json={"pattern": "g", "target": "https://example.com/g"})
    resp = client.get("/g", headers={"Accept": "text/html,application/xhtml+xml"},
                      follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "https://example.com/g"


def _seed_upstream(client: TestClient) -> None:
    created = client.post("/api/v1/upstreams", headers=_admin_headers(client),
                          json={"name": "wiki", "base_url": "https://wiki.example"})
    assert created.status_code == 201


def _stub_checks(monkeypatch: pytest.MonkeyPatch, results: list[UpstreamCheckResult]) -> None:
    async def fake_check_all(self: Any, upstreams: Any, pattern: str, client: Any) -> Any:
        return results

    monkeypatch.setattr(UpstreamCheckService, "check_all", fake_check_all)


def test_browser_missing_hit_redirects(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIRECTOR_AUTO_REDIRECT_DELAY", "0")
    _seed_upstream(client)
    _stub_checks(monkeypatch, [UpstreamCheckResult(
        upstream_name="wiki", check_url="https://wiki.example/fb",
        status="found", target_url="https://wiki.example/fb-live")])
    resp = client.get("/fb", headers={"Accept": "text/html"}, follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "https://wiki.example/fb-live"


def test_browser_missing_shows_upstream_rows(client: TestClient,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    _open_creation(client)
    _seed_upstream(client)
    _stub_checks(monkeypatch, [
        UpstreamCheckResult(upstream_name="wiki", check_url="https://wiki.example/fb",
                            status="not_found", message="Shortcut not found in upstream"),
        UpstreamCheckResult(upstream_name="down", check_url="",
                            status="error", message="Connection error: refused"),
    ])
    resp = client.get("/fb", headers={"Accept": "text/html"})
    assert resp.status_code == 404
    assert "wiki" in resp.text and "not found" in resp.text
    assert "down" in resp.text and "Connection error" in resp.text
    assert "checked 2 upstreams live" in resp.text
    assert "Re-check" in resp.text
    assert "/?new=fb" in resp.text
