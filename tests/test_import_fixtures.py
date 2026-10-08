"""Golden-fixture importer tests (EPIC-08 task 2).

Committed v1/v2 data dirs (see tests/fixtures/build_fixtures.py) pin the
importer against fixed real-shaped inputs: v1 is the oldest supported
shape (narrow table, no auxiliary tables), v2 the full modern shape.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlmodel")

from sqlalchemy import create_engine  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

from backend.migrations.import_v2 import main  # noqa: E402
from backend.models.entities import Setting, Shortcut, Upstream  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _run_fixture(name: str, tmp_path: Any) -> Any:
    db_url = f"sqlite:///{tmp_path}/golden.db"
    assert main(["--data-dir", str(FIXTURES / name),
                 "--database-url", db_url]) == 0
    return create_engine(db_url)


def test_import_v1_oldest_shape(tmp_path: Any) -> None:
    engine = _run_fixture("data-v1", tmp_path)
    with Session(engine) as s:
        rows = s.exec(select(Shortcut)).all()
        assert {r.pattern for r in rows} == {"docs", "jira"}
        # Narrow v1 rows normalize to modern defaults.
        docs = next(r for r in rows if r.pattern == "docs")
        assert docs.visibility == "public" and docs.tags == []
        assert docs.access_count == 41
        assert {u.name for u in s.exec(select(Upstream)).all()} == {"go"}
        settings = {r.key: r.value for r in s.exec(select(Setting)).all()}
        assert settings["auto_redirect_delay"] == 2

    # Idempotent re-run.
    engine = _run_fixture("data-v1", tmp_path)
    with Session(engine) as s:
        assert len(s.exec(select(Shortcut)).all()) == 2


def test_import_v2_full_shape(tmp_path: Any) -> None:
    from backend.models.entities import UpstreamCache, UpstreamCheckLog

    engine = _run_fixture("data-v2", tmp_path)
    with Session(engine) as s:
        assert len(s.exec(select(Shortcut)).all()) == 2
        assert len(s.exec(select(UpstreamCache)).all()) == 1
        assert len(s.exec(select(UpstreamCheckLog)).all()) == 1
        settings = {r.key: r.value for r in s.exec(select(Setting)).all()}
        assert settings == {
            "auto_redirect_delay": 0,
            "log_level": "WARNING",
            "delete_requires_password": True,
            "upstream_cache.enabled": True,
        }
