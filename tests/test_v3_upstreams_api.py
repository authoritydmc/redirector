"""Tests for the RESTful Upstreams and Upstream Cache router."""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("sqlmodel")

from fastapi.testclient import TestClient
from sqlmodel import SQLModel
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from backend.main import create_app
from backend.core.db import get_session


@pytest.fixture()
def client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    app = create_app()

    async def override_get_session():
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as tc:
        yield tc


def test_create_and_list_upstreams(client):
    res = client.post("/api/v1/upstreams", json={
        "name": "bitly",
        "base_url": "https://bit.ly",
        "fail_url": "https://bitly.com/404",
        "fail_status_code": 404,
        "verify_ssl": True
    })
    assert res.status_code == 201
    data = res.json()
    assert data["name"] == "bitly"
    assert data["base_url"] == "https://bit.ly"

    # List
    list_res = client.get("/api/v1/upstreams")
    assert list_res.status_code == 200
    assert len(list_res.json()) == 1


def test_duplicate_upstream_conflict(client):
    client.post("/api/v1/upstreams", json={
        "name": "jira-corp",
        "base_url": "https://jira.corp"
    })
    dup = client.post("/api/v1/upstreams", json={
        "name": "jira-corp",
        "base_url": "https://jira.corp.new"
    })
    assert dup.status_code == 409


def test_update_and_delete_upstream(client):
    created = client.post("/api/v1/upstreams", json={
        "name": "deleteme",
        "base_url": "https://old.example"
    }).json()
    uid = created["id"]

    patch_res = client.patch(f"/api/v1/upstreams/{uid}", json={
        "base_url": "https://updated.example"
    })
    assert patch_res.status_code == 200
    assert patch_res.json()["base_url"] == "https://updated.example"

    del_res = client.delete(f"/api/v1/upstreams/{uid}")
    assert del_res.status_code == 204

    # Now verify 404
    assert client.delete(f"/api/v1/upstreams/{uid}").status_code == 404
