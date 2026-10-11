"""Tests for public-action policy curation (PUT /api/v1/site/policy)."""

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


def test_policy_defaults_to_deny(client: TestClient) -> None:
    body = client.get("/api/v1/site/policy").json()
    assert body["public_actions"] == []
    assert sorted(body["known_actions"]) == ["shortcuts.create", "upstreams.create"]


def test_admin_curates_policy(client: TestClient) -> None:
    headers = _admin_headers(client)
    put = client.put("/api/v1/site/policy",
                     headers=headers, json={"public_actions": ["shortcuts.create"]})
    assert put.status_code == 200
    assert put.json()["public_actions"] == ["shortcuts.create"]
    assert client.get("/api/v1/site/policy").json()["public_actions"] == ["shortcuts.create"]
    clear = client.put("/api/v1/site/policy", headers=headers, json={"public_actions": []})
    assert clear.json()["public_actions"] == []


def test_policy_rejects_unknown_actions(client: TestClient) -> None:
    resp = client.put("/api/v1/site/policy", headers=_admin_headers(client),
                      json={"public_actions": ["shortcuts.delete"]})
    assert resp.status_code == 422
    assert resp.json()["code"] == "policy:unknown-action"


def test_policy_curation_needs_session(client: TestClient) -> None:
    assert client.put("/api/v1/site/policy", json={"public_actions": []}).status_code == 401
    headers = _admin_headers(client)
    issued = client.post("/api/v1/auth/api-keys", headers=headers,
                         json={"name": "policy-probe", "scopes": ["*"]}).json()
    key_headers = {"Authorization": f"Bearer {issued['api_key']}"}
    denied = client.put("/api/v1/site/policy", headers=key_headers,
                        json={"public_actions": ["shortcuts.create"]})
    assert denied.status_code == 403


def test_toggle_opens_anonymous_creation(client: TestClient) -> None:
    payload = {"pattern": "open-link", "target": "https://example.com/open"}
    assert client.post("/api/v1/shortcuts", json=payload).status_code == 401
    client.put("/api/v1/site/policy", headers=_admin_headers(client),
               json={"public_actions": ["shortcuts.create"]})
    created = client.post("/api/v1/shortcuts", json=payload)
    assert created.status_code == 201
