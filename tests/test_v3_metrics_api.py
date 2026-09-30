"""Tests for v3 Metrics API endpoints."""

from __future__ import annotations

import pytest

sqlmodel = pytest.importorskip("sqlmodel")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

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


def test_metrics_kpi_endpoint(client):
    # Seed via shortcut API
    client.post("/api/v1/shortcuts", json={
        "pattern": "wiki",
        "target": "https://wiki.example.com",
        "type": "static",
        "visibility": "public",
        "tags": ["docs", "infra"]
    })
    client.post("/api/v1/shortcuts", json={
        "pattern": "gh",
        "target": "https://github.com",
        "type": "static",
        "visibility": "private",
        "tags": ["code"]
    })

    resp = client.get("/api/v1/metrics/kpi")
    assert resp.status_code == 200
    data = resp.json()
    assert "overview" in data
    assert "breakdowns" in data
    assert "upstreams" in data

    overview = data["overview"]
    assert overview["total_shortcuts"] == 2
    assert overview["zero_hit_count"] == 2

    # Breakdown verification
    breakdowns = data["breakdowns"]
    assert any(item["visibility"] == "public" for item in breakdowns["by_visibility"])
    assert any(item["tag"] == "docs" for item in breakdowns["top_tags"])


def test_metrics_live_endpoint(client):
    resp = client.get("/api/v1/metrics/live")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert "process" in data
    assert "counts" in data
    assert "total_shortcuts" in data["counts"]
