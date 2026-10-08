#!/usr/bin/env python3
"""Build golden v1/v2 data dirs for importer tests (EPIC-08 task 2).

Regenerate: `python tests/fixtures/build_fixtures.py` from the repo root,
then commit the result. Fixtures are deliberately small but shape-faithful:
v1 is the oldest supported shape (narrow redirects table, no auxiliary
tables); v2 is the full modern shape (all columns, cache/log tables,
settings-bearing config with secrets that must NOT migrate).
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def build_v1(base: Path) -> None:
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True)
    conn = sqlite3.connect(base / "redirect.db")
    conn.execute("""CREATE TABLE redirects (id INTEGER PRIMARY KEY, type TEXT,
        pattern TEXT, target TEXT, access_count INTEGER, created_at TEXT,
        updated_at TEXT)""")
    conn.execute("""INSERT INTO redirects VALUES
        (1, 'static', 'docs', 'https://x.example/docs', 41,
         '2024-05-01 09:00:00', '2024-06-01 09:00:00'),
        (2, 'dynamic', 'jira', 'https://j.example/browse/{ticket}', 7,
         '2024-05-02 09:00:00', '2024-06-02 09:00:00')""")
    conn.commit()
    conn.close()
    (base / "redirect.config.json").write_text(json.dumps({
        "auto_redirect_delay": 2,
        "upstreams": [
            {"name": "go", "base_url": "http://go/", "fail_status_code": 404},
        ],
    }), encoding="utf-8")


def build_v2(base: Path) -> None:
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True)
    conn = sqlite3.connect(base / "redirect.db")
    conn.execute("""CREATE TABLE redirects (id INTEGER PRIMARY KEY, type TEXT,
        pattern TEXT, target TEXT, access_count INTEGER, created_at TEXT,
        updated_at TEXT, created_ip TEXT, updated_ip TEXT, tags TEXT,
        visibility TEXT, expires_at TEXT, owner_email TEXT)""")
    conn.execute("""INSERT INTO redirects VALUES
        (1, 'static', 'docs', 'https://x.example/docs', 7,
         '2026-01-01 10:00:00', '2026-02-01 10:00:00', '1.2.3.4', NULL,
         'eng,onboarding', 'public', NULL, NULL),
        (2, 'static', 'old', 'https://x.example/old', 0,
         '2026-01-02 10:00:00', '2026-02-02 10:00:00', NULL, NULL,
         '', 'private', NULL, 'a@b.c')""")
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
    conn.execute("""CREATE TABLE user_params (id INTEGER PRIMARY KEY,
        shortcut_pattern TEXT, param_name TEXT, description TEXT,
        required INTEGER, created_at TEXT, updated_at TEXT)""")
    conn.commit()
    conn.close()
    (base / "redirect.config.json").write_text(json.dumps({
        "auto_redirect_delay": 0,
        "log_level": "warning",
        "delete_requires_password": True,
        "upstream_cache": {"enabled": True},
        "admin_password": "fixture-password-not-real",
        "session_secret": "fixture-secret-not-real",
        "mfa": {"enabled": False, "secret": None},
        "port": 80,
        "database": "sqlite:///redirect.db",
        "upstreams": [
            {"name": "go", "base_url": "http://go/", "fail_status_code": 404},
        ],
    }), encoding="utf-8")


def main() -> None:
    build_v1(ROOT / "data-v1")
    build_v2(ROOT / "data-v2")
    print("wrote", ROOT / "data-v1", "and", ROOT / "data-v2")


if __name__ == "__main__":
    main()
