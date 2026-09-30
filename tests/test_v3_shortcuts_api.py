"""Tests for the RESTful Shortcuts CRUD router under /api/v1/shortcuts."""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("sqlmodel")

from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from backend.main import create_app
from backend.core.db import get_session
from backend.models.entities import Shortcut, ShortcutType, Visibility


@pytest.fixture()
def client():
    # In-memory async SQLite engine for clean test isolation
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    app = create_app()

    async def override_get_session():
        # Setup tables
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as tc:
        yield tc


def test_create_and_get_shortcut(client):
    res = client.post("/api/v1/shortcuts", json={
        "pattern": "team/docs",
        "target": "https://company.example/docs",
        "type": "static",
        "tags": ["eng", "docs"]
    })
    assert res.status_code == 201
    data = res.json()
    assert data["pattern"] == "team/docs"
    assert data["target"] == "https://company.example/docs"
    assert data["tags"] == ["eng", "docs"]

    # Fetch it back
    get_res = client.get("/api/v1/shortcuts/team/docs")
    assert get_res.status_code == 200
    assert get_res.json()["pattern"] == "team/docs"


def test_create_duplicate_conflict(client):
    client.post("/api/v1/shortcuts", json={
        "pattern": "unique-link",
        "target": "https://example.com"
    })
    dup = client.post("/api/v1/shortcuts", json={
        "pattern": "unique-link",
        "target": "https://other.com"
    })
    assert dup.status_code == 409


def test_list_shortcuts_pagination_and_search(client):
    for i in range(15):
        client.post("/api/v1/shortcuts", json={
            "pattern": f"link-{i:02d}",
            "target": f"https://example.com/{i}",
            "tags": ["batch"] if i % 2 == 0 else []
        })

    # Page 1
    p1 = client.get("/api/v1/shortcuts?page=1&pageSize=10")
    assert p1.status_code == 200
    d1 = p1.json()
    assert len(d1["data"]) == 10
    assert d1["meta"]["total"] == 15

    # Filter by tag
    p_tag = client.get("/api/v1/shortcuts?tag=batch")
    assert p_tag.status_code == 200
    assert len(p_tag.json()["data"]) == 8


def test_patch_shortcut(client):
    client.post("/api/v1/shortcuts", json={
        "pattern": "updateme",
        "target": "https://old.example"
    })
    patch_res = client.patch("/api/v1/shortcuts/updateme", json={
        "target": "https://new.example",
        "visibility": "private"
    })
    assert patch_res.status_code == 200
    assert patch_res.json()["target"] == "https://new.example"
    assert patch_res.json()["visibility"] == "private"


def test_delete_shortcut(client):
    client.post("/api/v1/shortcuts", json={
        "pattern": "deleteme",
        "target": "https://example.com"
    })
    del_res = client.delete("/api/v1/shortcuts/deleteme")
    assert del_res.status_code == 204

    # Verify 404
    assert client.get("/api/v1/shortcuts/deleteme").status_code == 404


def test_bulk_delete_shortcuts(client):
    client.post("/api/v1/shortcuts", json={"pattern": "bulk-1", "target": "https://1.com"})
    client.post("/api/v1/shortcuts", json={"pattern": "bulk-2", "target": "https://2.com"})

    res = client.post("/api/v1/shortcuts/bulk-delete", json={"patterns": ["bulk-1", "bulk-2", "nonexistent"]})
    assert res.status_code == 200
    data = res.json()
    assert data["count"] == 2
    assert "bulk-1" in data["deleted"]
    assert "bulk-2" in data["deleted"]
    assert "nonexistent" in data["not_found"]


def test_bulk_delete_empty_list_is_noop(client):
    res = client.post("/api/v1/shortcuts/bulk-delete", json={"patterns": []})
    assert res.status_code == 200
    assert res.json() == {"deleted": [], "not_found": [], "count": 0}


def test_bulk_delete_invalid_patterns_reported_not_found(client):
    res = client.post("/api/v1/shortcuts/bulk-delete", json={"patterns": ["!!!", ""]})
    assert res.status_code == 200
    data = res.json()
    assert data["count"] == 0
    assert len(data["not_found"]) == 2


def test_bulk_delete_requires_patterns_field(client):
    res = client.post("/api/v1/shortcuts/bulk-delete", json={})
    assert res.status_code == 422
