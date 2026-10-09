"""backup CLI tests: pre-migration archives without a running API (EPIC-08 task 4)."""

from __future__ import annotations

import asyncio
import json
import zipfile
from typing import Any

import pytest

pytest.importorskip("sqlmodel")

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402

from backend.cli.backup import main  # noqa: E402
from backend.core.config import settings as app_settings  # noqa: E402
from backend.models.entities import Shortcut  # noqa: E402


def _db_with_tables(tmp_path: Any) -> str:
    async def _main() -> str:
        url = f"sqlite+aiosqlite:///{tmp_path}/cli-test.db"
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            session.add(Shortcut(pattern="docs", target="https://x.example/docs"))
            await session.commit()
        await engine.dispose()
        return url

    return asyncio.run(_main())


def test_cli_create(tmp_path: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(app_settings, "data_dir", data_dir)
    db_url = _db_with_tables(tmp_path)
    assert main(["create", "--label", "pre-v3-m3", "--data-dir", str(data_dir),
                 "--database-url", db_url]) == 0
    out = capsys.readouterr().out
    assert "pre-v3-m3.zip" in out
    archives = list((data_dir / "backups").glob("redirector-backup-*-pre-v3-m3.zip"))
    assert len(archives) == 1
    with zipfile.ZipFile(archives[0]) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["tables"]["shortcuts"] == 1
        shortcuts = json.loads(archive.read("shortcuts.json"))
        assert shortcuts[0]["pattern"] == "docs"


def test_cli_bad_label(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_settings, "data_dir", tmp_path / "data")
    assert main(["create", "--label", "NOT valid!!"]) == 2
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2  # no subcommand


def test_cli_missing_tables(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (tmp_path / "empty.db").touch()
    monkeypatch.setattr(app_settings, "data_dir", data_dir)
    assert main(["create", "--data-dir", str(data_dir),
                 "--database-url", f"sqlite+aiosqlite:///{tmp_path}/empty.db"]) == 1
