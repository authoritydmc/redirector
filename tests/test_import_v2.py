"""import-v2 tests: synthetic v2 data dir (old string-typed schema) → v3 DB.

Sync sqlite3 + sync engine only — gevent-safe in the full suite.
Skipped when SQLModel isn't installed (main-branch CI).
"""

import json
import sqlite3

import pytest

pytest.importorskip("sqlmodel")

from sqlalchemy import create_engine  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

from backend.migrations.import_v2 import (  # noqa: E402
    main,
    parse_dt,
    read_table,
    split_tags,
)
from backend.models.entities import Setting, Shortcut, Upstream  # noqa: E402


def make_v2_data_dir(tmp_path):
    data_dir = tmp_path / "v2data"
    data_dir.mkdir()
    conn = sqlite3.connect(data_dir / "redirect.db")
    conn.execute("""CREATE TABLE redirects (id INTEGER PRIMARY KEY, type TEXT,
        pattern TEXT, target TEXT, access_count INTEGER, created_at TEXT,
        updated_at TEXT, created_ip TEXT, updated_ip TEXT, tags TEXT,
        visibility TEXT, expires_at TEXT, owner_email TEXT)""")
    conn.execute("""INSERT INTO redirects VALUES
        (1, 'static', 'docs', 'https://x.example/docs', 7,
         '2026-01-01 10:00:00', '2026-02-01 10:00:00', '1.2.3.4', NULL,
         'eng,onboarding', 'public', NULL, NULL),
        (2, 'weird-type', 'odd', 'https://x.example/odd', 0,
         'not-a-date', NULL, NULL, NULL, '', 'weird-vis', NULL, 'a@b.c'),
        (3, 'dynamic', 'jira', 'https://j.example/{ticket}', 3,
         '2026-03-01 10:00:00+00:00', '2026-03-02 10:00:00+00:00', NULL, NULL,
         NULL, 'team', NULL, NULL)""")
    conn.execute("""CREATE TABLE upstream_cache (pattern TEXT, upstream_name TEXT,
        resolved_url TEXT, checked_at TEXT)""")
    conn.execute("""INSERT INTO upstream_cache VALUES
        ('docs', 'go', 'http://go/docs', '2026-03-01 10:00:00')""")
    conn.execute("""CREATE TABLE upstream_check_log (id INTEGER PRIMARY KEY,
        pattern TEXT, upstream_name TEXT, check_url TEXT, result TEXT,
        detail TEXT, tried_at TEXT, count INTEGER, cached INTEGER)""")
    conn.execute("""INSERT INTO upstream_check_log VALUES
        (1, 'docs', 'go', 'http://go/docs', 'hit', 'ok',
         '2026-03-01 10:00:00', 2, 1)""")
    # user_params table deliberately absent (older v2 DB) — must be tolerated.
    conn.commit()
    conn.close()
    (data_dir / "redirect.config.json").write_text(json.dumps({
        "upstreams": [
            {"name": "go", "base_url": "http://go/", "fail_status_code": 404},
            {"name": "bad", "base_url": "http://bad/", "fail_status_code": "NaN"},
        ],
        "auto_redirect_delay": "0",
        "log_level": "debug",
        "delete_requires_password": "yes",
        "upstream_cache": {"enabled": False},
        "admin_password": "fixture-password-not-real",
        "session_secret": "fixture-secret-not-real",
        "mfa": {"enabled": True, "secret": "fixture-mfa-seed-not-real"},
        "port": 80,
        "database": "sqlite:///redirect.db",
    }), encoding="utf-8")
    return data_dir


def test_import_converts_and_normalizes(tmp_path):
    data_dir = make_v2_data_dir(tmp_path)
    db_url = f"sqlite:///{tmp_path}/v3.db"
    assert main(["--data-dir", str(data_dir), "--database-url", db_url]) == 0

    engine = create_engine(db_url)
    with Session(engine) as s:
        docs = s.exec(select(Shortcut).where(Shortcut.pattern == "docs")).one()
        assert docs.access_count == 7
        assert docs.tags == ["eng", "onboarding"]
        assert docs.created_ip == "1.2.3.4"
        assert docs.created_at.year == 2026
        odd = s.exec(select(Shortcut).where(Shortcut.pattern == "odd")).one()
        assert odd.type == "static" and odd.visibility == "public"  # normalized
        assert odd.owner_email == "a@b.c"
        assert {u.name for u in s.exec(select(Upstream)).all()} == {"go", "bad"}
        bad = s.exec(select(Upstream).where(Upstream.name == "bad")).one()
        assert bad.fail_status_code is None  # normalized

    # re-run is idempotent: everything skipped, counts unchanged
    assert main(["--data-dir", str(data_dir), "--database-url", db_url]) == 0
    with Session(engine) as s:
        assert len(s.exec(select(Shortcut)).all()) == 3


def test_dry_run_writes_nothing(tmp_path):
    data_dir = make_v2_data_dir(tmp_path)
    db_path = tmp_path / "v3dry.db"
    db_url = f"sqlite:///{db_path}"
    assert main(["--data-dir", str(data_dir), "--database-url", db_url, "--dry-run"]) == 0
    with Session(create_engine(db_url)) as s:
        assert s.exec(select(Shortcut)).all() == []


def _settings_map(engine):
    with Session(engine) as s:
        return {row.key: row.value for row in s.exec(select(Setting)).all()}


def test_import_migrates_config_settings(tmp_path):
    data_dir = make_v2_data_dir(tmp_path)
    db_url = f"sqlite:///{tmp_path}/v3settings.db"
    assert main(["--data-dir", str(data_dir), "--database-url", db_url]) == 0

    engine = create_engine(db_url)
    got = _settings_map(engine)
    # Curated allowlist, coerced + normalized ...
    assert got["auto_redirect_delay"] == 0
    assert got["log_level"] == "DEBUG"
    assert got["delete_requires_password"] is True
    assert got["upstream_cache.enabled"] is False
    # ... secrets never land in the table, deployment-owned keys are skipped.
    for forbidden in ("admin_password", "session_secret", "mfa",
                      "port", "database", "upstreams"):
        assert forbidden not in got

    # Idempotent re-run skips everything, and admin edits win over re-import.
    with Session(engine) as s:
        row = s.exec(select(Setting).where(Setting.key == "auto_redirect_delay")).one()
        row.value = 5
        s.add(row)
        s.commit()
    assert main(["--data-dir", str(data_dir), "--database-url", db_url]) == 0
    got = _settings_map(engine)
    assert got["auto_redirect_delay"] == 5
    assert len(got) == 4


def test_helpers():
    assert split_tags("a, b,,c ") == ["a", "b", "c"]
    assert split_tags(None) == []
    assert parse_dt(None) is None
    assert parse_dt("garbage") is None
    assert parse_dt("2026-01-01 10:00:00").tzinfo is not None
    assert read_table(sqlite3.connect(":memory:"), "nope") == []
