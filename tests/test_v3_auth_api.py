"""Tests for v3 Auth and Settings endpoints."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jwt")
sqlmodel = pytest.importorskip("sqlmodel")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

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


def test_auth_login_success_and_me(client: TestClient):
    # Attempt with bad password
    bad_resp = client.post("/api/v1/auth/login", json={"password": "wrongpassword"})
    assert bad_resp.status_code == 401

    # Attempt with correct password
    good_resp = client.post("/api/v1/auth/login", json={"password": settings.admin_password})
    assert good_resp.status_code == 200
    token_data = good_resp.json()
    assert "access_token" in token_data
    token = token_data["access_token"]

    # Verify protected /me endpoint with Bearer token
    me_resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_resp.status_code == 200
    assert me_resp.json()["role"] == "admin"


def test_admin_config_protected(client: TestClient):
    # Without token -> 401
    unauth = client.get("/api/v1/admin/config")
    assert unauth.status_code == 401

    # With login token
    login_resp = client.post("/api/v1/auth/login", json={"password": settings.admin_password})
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    get_resp = client.get("/api/v1/admin/config", headers=headers)
    assert get_resp.status_code == 200
    assert "app_version" in get_resp.json()

    # Update config
    patch_resp = client.patch(
        "/api/v1/admin/config",
        headers=headers,
        json={"settings": {"welcome_message": "Hello Redirector v3", "max_limit": 50}},
    )
    assert patch_resp.status_code == 200
    assert "welcome_message" in patch_resp.json()["keys"]

    # Read back
    get_after = client.get("/api/v1/admin/config", headers=headers)
    assert get_after.status_code == 200
    assert get_after.json()["custom"]["welcome_message"] == "Hello Redirector v3"
