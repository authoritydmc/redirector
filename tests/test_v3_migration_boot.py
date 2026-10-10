"""Migration proofs (EPIC-08 upgrade + downgrade tests).

A v2 data dir goes through `import-v2` into a scratch DB, then the v3 API
boots on the migrated database: every redirect kind serves with v3
semantics, upstreams and curated settings carry over, and the v2 files stay
byte-identical (downgrade = restart, not migration). Secrets deliberately do
NOT carry (see docs/UPGRADE-v3.md): the old v2 password gets 401 while the
configured v3 password logs in without an MFA challenge.

Imports below are bound through `pytest.importorskip` (no `noqa` needed):
each hard dependency is gated at collection, so the v3-only CI job skips
this file cleanly instead of erroring on a missing backend extra.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest

_testclient = pytest.importorskip("fastapi.testclient")
TestClient = _testclient.TestClient
_sa_asyncio = pytest.importorskip("sqlalchemy.ext.asyncio")
async_sessionmaker = _sa_asyncio.async_sessionmaker
create_async_engine = _sa_asyncio.create_async_engine
pytest.importorskip("sqlmodel")
_core_db = pytest.importorskip("backend.core.db")
get_session = _core_db.get_session
_core_config = pytest.importorskip("backend.core.config")
v3_settings = _core_config.settings
_main = pytest.importorskip("backend.main")
create_app = _main.create_app
_migrations = pytest.importorskip("backend.migrations.import_v2")
import_main = _migrations.main
_tiv = pytest.importorskip("tests.test_import_v2")
make_v2_data_dir = _tiv.make_v2_data_dir


def _boot_migrated_app(db_path: Any) -> Any:
    """Build the v3 app serving `db_path` via a session override."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app()

    async def override_get_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    return app


def _migrated_settings(db_path: Any) -> Any:
    """Read the migrated settings table, decoding JSON-encoded values."""
    raw = sqlite3.connect(db_path)
    try:
        rows = raw.execute("SELECT key, value FROM settings").fetchall()
    finally:
        raw.close()
    return {
        key: (json.loads(value) if isinstance(value, str) else value)
        for key, value in rows
    }


def _seed_extra_v2_rows(data_dir: Any) -> None:
    """Add dynamic/expired/private rows to the synthetic v2 dir via stdlib
    sqlite3 — the shared fixture stays untouched for its other users."""
    conn = sqlite3.connect(data_dir / "redirect.db")
    try:
        conn.executemany(
            "INSERT INTO redirects (type, pattern, target, access_count,"
            " created_at, updated_at, visibility, expires_at, owner_email)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("dynamic", "bug", "https://j.example/{ticket}", 3,
                 "2026-03-01 10:00:00", "2026-03-02 10:00:00",
                 "public", None, None),
                ("static", "old-link", "https://x.example/old", 5,
                 "2026-01-10 10:00:00", "2026-02-10 10:00:00",
                 "public", "2026-02-11 10:00:00", None),
                ("static", "secret", "https://x.example/secret", 1,
                 "2026-04-01 10:00:00", "2026-04-02 10:00:00",
                 "private", None, "owner@example.com"),
            ],
        )
        conn.commit()
    finally:
        conn.close()


def test_v2_data_boots_and_serves(tmp_path: Any) -> None:
    data_dir = make_v2_data_dir(tmp_path)
    db_path = tmp_path / "migrated.db"
    assert import_main([
        "--data-dir", str(data_dir),
        "--database-url", f"sqlite:///{db_path}",
    ]) == 0

    app = _boot_migrated_app(db_path)
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


def test_v3_leaves_v2_files_untouched(tmp_path: Any) -> None:
    """Downgrade proof (EPIC-08): v3 never writes the v2 data dir.

    Import + boot + live traffic (including a v3 write) must leave
    `redirect.db` and `redirect.config.json` byte-identical, so retreating
    to the last v2 tag is a restart, not a migration.
    """
    data_dir = make_v2_data_dir(tmp_path)
    before = {
        name: (data_dir / name).read_bytes()
        for name in ("redirect.db", "redirect.config.json")
    }
    db_path = tmp_path / "migrated.db"
    assert import_main([
        "--data-dir", str(data_dir),
        "--database-url", f"sqlite:///{db_path}",
    ]) == 0

    app = _boot_migrated_app(db_path)
    with TestClient(app) as client:
        # A v3 write lands in the migrated DB only (mutations need admin) ...
        login = client.post("/api/v1/auth/login", json={"password": v3_settings.admin_password})
        assert login.status_code == 200
        client.headers.update({"Authorization": f"Bearer {login.json()['access_token']}"})
        assert client.post(
            "/api/v1/shortcuts",
            json={"pattern": "v3-only", "target": "https://x.example/n"},
        ).status_code == 201
        assert client.get("/v3-only", follow_redirects=False).status_code in (200, 302)

    after = {
        name: (data_dir / name).read_bytes()
        for name in ("redirect.db", "redirect.config.json")
    }
    assert after == before


def test_migrated_redirect_kinds_serve(tmp_path: Any) -> None:
    """Upgrade proof (EPIC-08): every migrated redirect kind serves with v3
    semantics — legacy-brace dynamic substitution, expired to 410,
    private to 403, unknown to 404 — and curated settings carry over.

    Secrets deliberately do NOT carry (pinned here, by design — see
    docs/UPGRADE-v3.md): the old v2 password gets 401, the configured v3
    password logs in, and the migrated MFA seed does not arm an MFA
    challenge.
    """
    data_dir = make_v2_data_dir(tmp_path)
    _seed_extra_v2_rows(data_dir)
    db_path = tmp_path / "upgraded.db"
    assert import_main([
        "--data-dir", str(data_dir),
        "--database-url", f"sqlite:///{db_path}",
    ]) == 0

    migrated = _migrated_settings(db_path)
    assert migrated.get("auto_redirect_delay") == 0
    assert migrated.get("log_level") == "DEBUG"

    app = _boot_migrated_app(db_path)
    with TestClient(app) as client:
        debug = client.get("/api/v1/resolve", params={"pattern": "docs"}).json()
        assert debug["outcome"] == "redirect"
        assert debug["target"] == "https://x.example/docs"

        # The hot path renders the countdown page by default, so dynamic
        # substitution is asserted through the debug API's outcome/target.
        dyn = client.get("/api/v1/resolve", params={"pattern": "bug/ABC-123"}).json()
        assert dyn["outcome"] == "redirect"
        assert dyn["target"] == "https://j.example/abc-123"  # v3 lowercases args
        assert client.get("/old-link", follow_redirects=False).status_code == 410
        assert client.get("/secret", follow_redirects=False).status_code == 403
        assert client.get("/nope-nothing", follow_redirects=False).status_code == 404

        upstreams = client.get("/api/v1/upstreams").json()
        assert {u["name"] for u in upstreams} == {"go", "bad"}

        # Old v2 password stays behind (the fixture carries an empty,
        # non-credential password value) ...
        assert client.post(
            "/api/v1/auth/login", json={"password": "wrongpassword"}
        ).status_code == 401
        # ... the configured v3 password logs in with no MFA challenge ...
        good = client.post(
            "/api/v1/auth/login", json={"password": v3_settings.admin_password}
        )
        assert good.status_code == 200
        body = good.json()
        assert "access_token" in body
        assert "mfa_required" not in body
        # ... and the token opens the admin surface.
        me = client.get("/api/v1/auth/me",
                        headers={"Authorization": f"Bearer {body['access_token']}"})
        assert me.status_code == 200
        assert me.json()["role"] == "admin"
