"""SPA static serving tests (EPIC-02 task 7 / EPIC-08 M2).

The /app mount degrades to nothing without a build (backend CI installs
no node); with one, exact files serve, deep links fall back to index.html
for client-side routing, and traversal escapes 404.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlmodel")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402

from backend.core.config import settings as app_settings  # noqa: E402
from backend.core.db import get_session  # noqa: E402
from backend.main import _is_within, create_app  # noqa: E402


def _spa_dir(tmp_path: Any) -> Any:
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text("<div id=\"root\"></div>", encoding="utf-8")
    (assets / "app.js").write_text("console.log(1)", encoding="utf-8")
    return dist


def _db_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> TestClient:
    """App with tables created (fall-through hits a working resolve path)."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(app_settings, "spa_dir", tmp_path / "nodist")
    app = create_app()

    async def override_get_session():  # type: ignore[no-untyped-def]
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    return TestClient(app)


def test_is_within(tmp_path: Any) -> None:
    root = tmp_path / "dist"
    root.mkdir()
    assert _is_within(root, root / "assets" / "app.js")
    assert _is_within(root, root)
    assert not _is_within(root, tmp_path / "secret")
    assert not _is_within(root, root / ".." / "secret")


def test_spa_serves_files_and_fallback(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_settings, "spa_dir", _spa_dir(tmp_path))
    with TestClient(create_app()) as client:
        shell = client.get("/app/")
        assert shell.status_code == 200
        assert '<div id="root">' in shell.text

        asset = client.get("/app/assets/app.js")
        assert asset.status_code == 200
        assert "javascript" in asset.headers["content-type"]
        assert "console.log(1)" in asset.text

        # Deep link (client-side route): index.html, not 404.
        deep = client.get("/app/upstreams")
        assert deep.status_code == 200
        assert '<div id="root">' in deep.text


def test_spa_missing_mount_falls_through(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    with _db_client(monkeypatch, tmp_path) as client:
        # No build present: /app/* reaches the redirect catch-all (404 JSON),
        # and the API is otherwise unaffected.
        assert client.get("/app/").status_code == 404
        assert client.get("/healthz").status_code == 200
