"""Tests for local users, teams, membership, and ownership enforcement."""

from __future__ import annotations

from typing import Any

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


def _signup(client: TestClient, email: str, password: str = "password123") -> dict[str, Any]:
    resp = client.post("/api/v1/auth/signup",
                       json={"email": email, "name": email.split("@")[0], "password": password})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert "password_hash" not in body and "password" not in body
    return body


def _user_headers(client: TestClient, email: str, password: str = "password123") -> dict[str, str]:
    login = client.post("/api/v1/auth/user/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    assert login.json()["role"] == "user"
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_signup_validation(client: TestClient) -> None:
    assert client.post("/api/v1/auth/signup",
                       json={"email": "not-an-email", "password": "password123"}).status_code == 422
    assert client.post("/api/v1/auth/signup",
                       json={"email": "a@x.com", "password": "short"}).status_code == 422
    _signup(client, "a@x.com")
    dup = client.post("/api/v1/auth/signup",
                      json={"email": "A@X.COM", "password": "password123"})
    assert dup.status_code == 409
    assert dup.json()["code"] == "users:exists"


def test_user_login_and_disable(client: TestClient) -> None:
    _signup(client, "lu@x.com")
    assert client.post("/api/v1/auth/user/login",
                       json={"email": "lu@x.com", "password": "wrongpass"}).status_code == 401
    headers = _user_headers(client, "lu@x.com")
    me = client.get("/api/v1/auth/me", headers=headers).json()
    assert me == {"role": "user", "sub": "lu@x.com"}
    # Admin-only surfaces stay closed to users.
    assert client.get("/api/v1/auth/users", headers=headers).status_code == 403
    assert client.get("/api/v1/auth/api-keys", headers=headers).status_code == 403
    # Disable blocks login; enable restores it.
    admin = _admin_headers(client)
    assert client.post("/api/v1/auth/users/lu@x.com/disable", headers=admin).status_code == 200
    blocked = client.post("/api/v1/auth/user/login",
                          json={"email": "lu@x.com", "password": "password123"})
    assert blocked.status_code == 403
    assert blocked.json()["code"] == "users:disabled"
    assert client.post("/api/v1/auth/users/lu@x.com/enable", headers=admin).status_code == 200
    _user_headers(client, "lu@x.com")


def test_owner_enforcement_and_creator_stamp(client: TestClient) -> None:
    admin = _admin_headers(client)
    _signup(client, "amy@x.com")
    _signup(client, "bob@x.com")
    amy = _user_headers(client, "amy@x.com")
    bob = _user_headers(client, "bob@x.com")

    created = client.post("/api/v1/shortcuts", headers=amy,
                          json={"pattern": "amys", "target": "https://a.example"})
    assert created.status_code == 201
    assert created.json()["owner_email"] == "amy@x.com"  # stamped, not typed

    anon = client.post("/api/v1/shortcuts",
                       json={"pattern": "noone", "target": "https://n.example"})
    assert anon.status_code in (201, 401)  # depends on public policy (deny here)
    if anon.status_code == 201:
        assert anon.json()["owner_email"] is None

    assert client.patch("/api/v1/shortcuts/amys", headers=amy,
                        json={"target": "https://a2.example"}).status_code == 200
    assert client.patch("/api/v1/shortcuts/amys", headers=bob,
                        json={"target": "https://evil.example"}).status_code == 403
    assert client.delete("/api/v1/shortcuts/amys", headers=bob).status_code == 403
    assert client.patch("/api/v1/shortcuts/amys", headers=admin,
                        json={"target": "https://admin.example"}).status_code == 200
    assert client.delete("/api/v1/shortcuts/amys", headers=admin).status_code == 204


def test_team_crud_and_roster(client: TestClient) -> None:
    admin = _admin_headers(client)
    _signup(client, "tamy@x.com")
    _signup(client, "tbob@x.com")
    _signup(client, "tcat@x.com")
    amy = _user_headers(client, "tamy@x.com")
    bob = _user_headers(client, "tbob@x.com")

    made = client.post("/api/v1/teams", headers=amy, json={"name": "red"})
    assert made.status_code == 201
    team = made.json()
    assert team["members"][0]["email"] == "tamy@x.com"
    assert team["members"][0]["role"] == "admin"
    team_id = team["id"]

    assert client.get(f"/api/v1/teams/{team_id}", headers=bob).status_code == 404
    assert client.post(f"/api/v1/teams/{team_id}/members", headers=bob,
                       json={"email": "tcat@x.com"}).status_code == 404

    added = client.post(f"/api/v1/teams/{team_id}/members", headers=amy,
                        json={"email": "tbob@x.com", "role": "member"})
    assert added.status_code == 201
    assert client.get(f"/api/v1/teams/{team_id}", headers=bob).status_code == 200
    assert any(t["id"] == team_id for t in client.get("/api/v1/teams", headers=bob).json())

    dup = client.post(f"/api/v1/teams/{team_id}/members", headers=amy,
                      json={"email": "tbob@x.com"})
    assert dup.status_code == 409
    assert client.post(f"/api/v1/teams/{team_id}/members", headers=amy,
                       json={"email": "ghost@x.com"}).status_code == 404
    assert client.post(f"/api/v1/teams/{team_id}/members", headers=amy,
                       json={"email": "tcat@x.com", "role": "owner"}).status_code == 422

    # Last admin cannot leave.
    assert client.delete(f"/api/v1/teams/{team_id}/members/tamy@x.com",
                         headers=amy).status_code == 409
    assert client.post(f"/api/v1/teams/{team_id}/members", headers=admin,
                       json={"email": "tcat@x.com", "role": "admin"}).status_code == 201
    assert client.delete(f"/api/v1/teams/{team_id}/members/tamy@x.com",
                         headers=admin).status_code == 200

    # API keys (even *) cannot rewire teams.
    issued = client.post("/api/v1/auth/api-keys", headers=admin,
                         json={"name": "t", "scopes": ["*"]}).json()
    key_headers = {"Authorization": f"Bearer {issued['api_key']}"}
    assert client.post("/api/v1/teams", headers=key_headers, json={"name": "x"}).status_code == 403


def test_team_links_resolve_for_members(client: TestClient) -> None:
    admin = _admin_headers(client)
    _signup(client, "mamy@x.com")
    _signup(client, "mbob@x.com")
    amy = _user_headers(client, "mamy@x.com")
    bob = _user_headers(client, "mbob@x.com")

    team_id = client.post("/api/v1/teams", headers=amy, json={"name": "blue"}).json()["id"]

    missing = client.post("/api/v1/shortcuts", headers=amy,
                          json={"pattern": "bluelink", "target": "https://b.example",
                                "visibility": "team"})
    assert missing.status_code == 422
    assert missing.json()["code"] == "teams:link-needs-team"

    made = client.post("/api/v1/shortcuts", headers=amy,
                       json={"pattern": "bluelink", "target": "https://b.example",
                             "visibility": "team", "team_id": team_id})
    assert made.status_code == 201
    assert made.json()["team_id"] == team_id

    assert client.get("/bluelink").status_code == 403  # anon
    assert client.get("/bluelink", headers=bob).status_code == 403  # outsider
    assert client.get("/bluelink", headers=amy).status_code in (200, 302)  # owner-member
    added = client.post(f"/api/v1/teams/{team_id}/members", headers=amy,
                      json={"email": "mbob@x.com"})
    assert added.status_code == 201, added.text
    assert client.get("/bluelink", headers=bob).status_code in (200, 302)  # member now
    assert client.get("/bluelink", headers=admin).status_code in (200, 302)  # site admin

    blocked = client.delete(f"/api/v1/teams/{team_id}", headers=amy)
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "teams:links-exist"
