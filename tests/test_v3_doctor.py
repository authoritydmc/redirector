"""doctor CLI tests: healthy / broken / missing installs (EPIC-08 task 5)."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest

pytest.importorskip("sqlmodel")

from backend.cli.doctor import main, run_checks  # noqa: E402


def _write_db(data_dir: Any, sql: str) -> None:
    conn = sqlite3.connect(data_dir / "redirect.db")
    conn.executescript(sql)
    conn.commit()
    conn.close()


def test_healthy_install(tmp_path: Any, capsys: Any) -> None:
    data_dir = tmp_path / "good"
    data_dir.mkdir()
    _write_db(data_dir, "CREATE TABLE redirects (id INTEGER PRIMARY KEY);")
    (data_dir / "redirect.config.json").write_text(json.dumps({"port": 80}))
    assert main(["--data-dir", str(data_dir)]) == 0
    out = capsys.readouterr().out
    assert "[ok  ] database" in out and "[ok  ] config" in out
    assert "0 problem(s)" in out


def test_broken_install(tmp_path: Any) -> None:
    data_dir = tmp_path / "bad"
    data_dir.mkdir()
    _write_db(data_dir, "CREATE TABLE whatever (id INTEGER PRIMARY KEY);")
    (data_dir / "redirect.config.json").write_text("{not json")
    report = run_checks(data_dir)
    assert report.problems == 2
    kinds = {c.name for c in report.checks if not c.ok and not c.warning}
    assert kinds == {"database", "config"}


def test_missing_everything_warns_only(tmp_path: Any) -> None:
    data_dir = tmp_path / "empty"
    data_dir.mkdir()
    assert main(["--data-dir", str(data_dir)]) == 0
    report = run_checks(data_dir)
    assert report.problems == 0
    assert report.warnings >= 2  # no db, no config at minimum


def test_redis_probe_warns_never_fails(tmp_path: Any) -> None:
    data_dir = tmp_path / "noredis"
    data_dir.mkdir()
    # Nothing listens here: warning, exit still 0.
    assert main(["--data-dir", str(data_dir),
                 "--redis-url", "redis://127.0.0.1:6390/0"]) == 0
    report = run_checks(data_dir, "redis://127.0.0.1:6390/0")
    redis_checks = [c for c in report.checks if c.name == "redis"]
    assert len(redis_checks) == 1 and redis_checks[0].warning
