"""Router-level resolve page tests: JSON stays JSON, browsers get a
create-this-link page on unknown patterns."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jwt")
sqlmodel = pytest.importorskip("sqlmodel")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backend.core.config import settings  # noqa: E402
from backend.core.db import get_session  # noqa: E402
from backend.main import create_app  # noqa: E402


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


def test_unknown_pattern_is_json_by_default(client: TestClient) -> None:
    resp = client.get("/no-such-link-xyz")
    assert resp.status_code == 404
    assert "json" in resp.headers["content-type"]
    assert resp.json()["code"] == "resolve:not_found"


def test_unknown_pattern_renders_create_page_for_browsers(client: TestClient) -> None:
    client.post("/api/v1/shortcuts", headers=_admin_headers(client),
                json={"pattern": "guides", "target": "https://example.com/guides"})
    resp = client.get("/guid", headers={"Accept": "text/html,application/xhtml+xml"})
    assert resp.status_code == 404
    assert "text/html" in resp.headers["content-type"]
    assert "r/guid" in resp.text
    assert "/?new=guid" in resp.text
    assert "guides" in resp.text  # close match suggested


def test_known_pattern_still_redirects(client: TestClient,
                                       monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIRECTOR_AUTO_REDIRECT_DELAY", "0")
    client.post("/api/v1/shortcuts", headers=_admin_headers(client),
                json={"pattern": "g", "target": "https://example.com/g"})
    resp = client.get("/g", headers={"Accept": "text/html,application/xhtml+xml"},
                      follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "https://example.com/g"
