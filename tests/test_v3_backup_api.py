"""Tests for admin backups: job lifecycle, archive contents, safety rules.

Backups land under data_dir/backups, so settings.data_dir is pointed at a
per-test tmp dir. No network, no Redis — the in-process runner executes the
`backup_create` kind through the same shared `execute_job` the arq worker
uses (broker transport already covered in test_v3_jobs_arq.py).
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import time
import zipfile
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlmodel")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402

from backend.core.config import settings as app_settings  # noqa: E402
from backend.core.db import get_session  # noqa: E402
from backend.main import create_app  # noqa: E402
from backend.models.entities import ApiKey, Setting, Shortcut, Upstream  # noqa: E402
from backend.modules.jobs.runner import JobRunner  # noqa: E402

PLAINTEXT_CANARY = "fixture-backup-key-material"


def _api_client(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(app_settings, "data_dir", tmp_path / "data")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/backup-test.db")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app()
    app.state.jobs = JobRunner(factory)

    async def _init() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        await engine.dispose()

    asyncio.run(_init())

    async def override_get_session():  # type: ignore[no-untyped-def]
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    return TestClient(app)


def _jwt_headers(client: TestClient) -> dict[str, str]:
    login = client.post("/api/v1/auth/login",
                        json={"password": app_settings.admin_password})
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _seeded_client(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    client = _api_client(tmp_path, monkeypatch)

    async def _seed() -> None:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/backup-test.db")
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            session.add(Shortcut(pattern="docs", target="https://x.example/docs"))
            session.add(Upstream(name="wiki", base_url="https://wiki.example"))
            session.add(Setting(key="welcome_message", value="hi"))
            session.add(ApiKey(
                prefix="fixturepk01",
                secret_hash=hashlib.sha256(PLAINTEXT_CANARY.encode()).hexdigest(),
                name="backup-test",
            ))
            await session.commit()
        await engine.dispose()

    asyncio.run(_seed())
    return client


def _wait_terminal(client: TestClient, job_id: int, timeout: float = 30.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        body = client.get(f"/api/v1/jobs/{job_id}").json()
        if body["status"] in ("succeeded", "failed", "cancelled"):
            return body
        assert time.monotonic() < deadline, f"job {job_id} stuck in {body['status']}"
        time.sleep(0.05)


def test_backup_job_lifecycle(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    with _seeded_client(tmp_path, monkeypatch) as client:
        headers = _jwt_headers(client)
        enqueued = client.post("/api/v1/admin/backup", headers=headers,
                               json={"label": "nightly"})
        assert enqueued.status_code == 202
        job_id = enqueued.json()["id"]

        final = _wait_terminal(client, job_id)
        assert final["status"] == "succeeded"
        result = final["result"]
        assert result["name"].startswith("redirector-backup-")
        assert result["name"].endswith("-nightly.zip")
        assert result["tables"]["shortcuts"] == 1
        assert result["size_bytes"] > 0

        listed = client.get("/api/v1/admin/backup", headers=headers).json()
        assert [b["name"] for b in listed] == [result["name"]]
        assert listed[0]["tables"]["api_keys"] == 1
        assert listed[0]["created_at"]


def test_backup_download_contents(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    with _seeded_client(tmp_path, monkeypatch) as client:
        headers = _jwt_headers(client)
        job_id = client.post("/api/v1/admin/backup", headers=headers, json={}).json()["id"]
        name = _wait_terminal(client, job_id)["result"]["name"]

        resp = client.get(f"/api/v1/admin/backup/{name}", headers=headers)
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        assert name in resp.headers.get("content-disposition", "")

        archive = zipfile.ZipFile(io.BytesIO(resp.content))
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["kind"] == "redirector-backup"
        assert manifest["format_version"] == 1
        assert manifest["tables"]["shortcuts"] == 1
        secrets_manifest = manifest["secrets_manifest"]
        assert secrets_manifest["api_key_hashes"] == 1
        assert secrets_manifest["plaintext_secret_values"] == []

        shortcuts = json.loads(archive.read("shortcuts.json"))
        assert shortcuts[0]["pattern"] == "docs"
        api_keys = json.loads(archive.read("api_keys.json"))
        assert api_keys[0]["prefix"] == "fixturepk01"
        assert "secret_hash" in api_keys[0]

        # The plaintext canary appears nowhere in the archive bytes.
        assert PLAINTEXT_CANARY.encode() not in resp.content


def test_backup_delete_and_404s(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    with _seeded_client(tmp_path, monkeypatch) as client:
        headers = _jwt_headers(client)
        job_id = client.post("/api/v1/admin/backup", headers=headers, json={}).json()["id"]
        name = _wait_terminal(client, job_id)["result"]["name"]

        deleted = client.delete(f"/api/v1/admin/backup/{name}", headers=headers)
        assert deleted.status_code == 200
        assert deleted.json() == {"success": True, "name": name}
        assert client.get("/api/v1/admin/backup", headers=headers).json() == []

        # Valid shape but gone, and traversal attempts: all 404.
        assert client.get(f"/api/v1/admin/backup/{name}", headers=headers).status_code == 404
        assert client.delete(f"/api/v1/admin/backup/{name}", headers=headers).status_code == 404
        assert client.get(
            "/api/v1/admin/backup/..%2F..%2Fsecret", headers=headers).status_code == 404

        # Bad labels rejected before any job is enqueued.
        bad = client.post("/api/v1/admin/backup", headers=headers,
                          json={"label": "NOT valid!!"})
        assert bad.status_code == 422
        assert bad.json()["code"] == "backup:invalid-label"


def test_backup_requires_admin(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    with _seeded_client(tmp_path, monkeypatch) as client:
        assert client.post("/api/v1/admin/backup", json={}).status_code == 401
        assert client.get("/api/v1/admin/backup").status_code == 401
        assert client.delete(
            "/api/v1/admin/backup/redirector-backup-20260101T000000Z.zip"
        ).status_code == 401


def _restore_and_wait(client: TestClient, name: str) -> dict[str, Any]:
    enqueued = client.post(f"/api/v1/admin/backup/{name}:restore",
                           headers=_jwt_headers(client))
    assert enqueued.status_code == 202
    return _wait_terminal(client, enqueued.json()["id"])


def test_restore_roundtrip(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    with _seeded_client(tmp_path, monkeypatch) as client:
        headers = _jwt_headers(client)
        job_id = client.post("/api/v1/admin/backup", headers=headers, json={}).json()["id"]
        name = _wait_terminal(client, job_id)["result"]["name"]

        # Mutate live state after the backup: delete a row, change a value,
        # add a row the backup never saw.
        assert client.delete("/api/v1/shortcuts/docs").status_code == 204
        assert client.patch(
            "/api/v1/admin/config", headers=headers,
            json={"settings": {"welcome_message": "changed"}},
        ).status_code == 200
        assert client.post(
            "/api/v1/shortcuts",
            json={"pattern": "intruder", "target": "https://evil.example"},
        ).status_code == 201

        final = _restore_and_wait(client, name)
        assert final["status"] == "succeeded"
        assert final["result"]["restored"]["shortcuts"] == 1
        assert final["result"]["safety_backup"].endswith("-pre-restore.zip")

        # Backup state is back; the intruder survives (merge, not wipe).
        assert client.get("/api/v1/shortcuts/docs").status_code == 200
        assert client.get("/api/v1/shortcuts/intruder").status_code == 200
        config = client.get("/api/v1/admin/backup", headers=headers).json()
        assert len(config) == 2  # original + safety backup
        assert any(b["name"].endswith("-pre-restore.zip") for b in config)


def test_restore_missing_and_corrupt(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    with _seeded_client(tmp_path, monkeypatch) as client:
        headers = _jwt_headers(client)

        # Unknown archive and traversal attempts 404 before any job exists.
        assert client.post(
            "/api/v1/admin/backup/redirector-backup-20200101T000000Z.zip:restore",
            headers=headers,
        ).status_code == 404
        assert client.post(
            "/api/v1/admin/backup/..%2F..%2Fsecret:restore", headers=headers
        ).status_code in (404, 405)

        # A corrupt archive enqueues, then fails with a clear error —
        # and live data is untouched (validate-before-apply).
        backups = tmp_path / "data" / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        corrupt = "redirector-backup-20200101T000000Z.zip"
        (backups / corrupt).write_bytes(b"not a zip archive")
        before = client.get("/api/v1/shortcuts/docs").status_code
        final = _restore_and_wait(client, corrupt)
        assert final["status"] == "failed"
        assert "zip" in (final["error"] or "")
        assert client.get("/api/v1/shortcuts/docs").status_code == before
