"""Tests for v3 Auth and Settings endpoints."""

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


def _jwt_headers(client: TestClient):
    login_resp = client.post("/api/v1/auth/login", json={"password": settings.admin_password})
    assert login_resp.status_code == 200
    return {"Authorization": f"Bearer {login_resp.json()['access_token']}"}


def test_api_key_issue_use_revoke_cycle(client: TestClient):
    headers = _jwt_headers(client)

    # Issue: plaintext returned once, only a hash is stored server-side.
    issued = client.post(
        "/api/v1/auth/api-keys", headers=headers, json={"name": "ci-cron"})
    assert issued.status_code == 201
    body = issued.json()
    assert body["api_key"].startswith("rk_") and body["prefix"] in body["api_key"]
    assert "secret_hash" not in body and "secret" not in body
    key_id = body["id"]
    plaintext = body["api_key"]

    # The key drives admin endpoints without a browser session ...
    key_headers = {"Authorization": f"Bearer {plaintext}"}
    assert client.get("/api/v1/admin/config", headers=key_headers).status_code == 200
    me = client.get("/api/v1/auth/me", headers=key_headers).json()
    assert me["role"] == "admin" and me["sub"] == f"apikey:{key_id}"

    # ... shows up in listing (prefix only) with last-used tracked ...
    listed = client.get("/api/v1/auth/api-keys", headers=headers).json()
    entry = next(e for e in listed if e["id"] == key_id)
    assert entry["last_used_at"] is not None
    assert all("secret" not in k for e in listed for k in e)

    # ... and stops working once revoked.
    revoked = client.delete(f"/api/v1/auth/api-keys/{key_id}", headers=headers)
    assert revoked.status_code == 200
    assert revoked.json()["revoked_at"] is not None
    assert client.get("/api/v1/admin/config", headers=key_headers).status_code == 401


def test_api_key_rejections(client: TestClient):
    headers = _jwt_headers(client)

    # Unknown prefix, wrong secret, and malformed tokens all 401 alike.
    for bad in ("rk_deadbeef123_" + "a" * 43, "rk_short"):
        res = client.get("/api/v1/admin/config", headers={"Authorization": f"Bearer {bad}"})
        assert res.status_code == 401

    issued = client.post(
        "/api/v1/auth/api-keys", headers=headers, json={"name": "tamper"}).json()
    tampered = issued["api_key"][:-1] + ("A" if not issued["api_key"].endswith("A") else "B")
    assert client.get(
        "/api/v1/admin/config", headers={"Authorization": f"Bearer {tampered}"}
    ).status_code == 401

    # Blank names rejected; unknown ids 404.
    assert client.post(
        "/api/v1/auth/api-keys", headers=headers, json={"name": "  "}).status_code == 422
    assert client.delete("/api/v1/auth/api-keys/999", headers=headers).status_code == 404


def test_api_key_cannot_manage_keys(client: TestClient):
    """Key management needs an interactive JWT session (leak containment)."""
    headers = _jwt_headers(client)
    plaintext = client.post(
        "/api/v1/auth/api-keys", headers=headers, json={"name": "limited"}).json()["api_key"]
    key_headers = {"Authorization": f"Bearer {plaintext}"}

    assert client.post(
        "/api/v1/auth/api-keys", headers=key_headers, json={"name": "sibling"}
    ).status_code == 403
    assert client.get("/api/v1/auth/api-keys", headers=key_headers).status_code == 403
    assert client.delete("/api/v1/auth/api-keys/1", headers=key_headers).status_code == 403
