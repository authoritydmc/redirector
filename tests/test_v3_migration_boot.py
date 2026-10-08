"""Zero-manual-step migration proof (master acceptance, EPIC-08 upgrade test).

A synthetic v2 data dir goes through `import-v2` into a scratch DB, then the
API boots on the migrated database and serves a redirect — no operator
steps between. Secrets are deliberately NOT carried (admin login keeps
working via the configured password, verified separately).
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlmodel")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from backend.core.db import get_session  # noqa: E402
from backend.main import create_app  # noqa: E402
from backend.migrations.import_v2 import main as import_main  # noqa: E402
from tests.test_import_v2 import make_v2_data_dir  # noqa: E402


def test_v2_data_boots_and_serves(tmp_path: Any) -> None:
    data_dir = make_v2_data_dir(tmp_path)
    db_path = tmp_path / "migrated.db"
    assert import_main([
        "--data-dir", str(data_dir),
        "--database-url", f"sqlite:///{db_path}",
    ]) == 0

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app()

    async def override_get_session():  # type: ignore[no-untyped-def]
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as client:
        # v2 static shortcut resolves through the v3 hot path ...
        debug = client.get("/api/v1/resolve", params={"pattern": "docs"}).json()
        assert debug["outcome"] == "redirect"
        assert debug["target"] == "https://x.example/docs"
        # ... and the legacy redirect URL serves (countdown page by default).
        assert client.get("/docs").status_code == 200
        # Upstream config came over too.
        upstreams = client.get("/api/v1/upstreams").json()
        assert {u["name"] for u in upstreams} == {"go", "bad"}
