"""Query-plan regression tests (EPIC-04 task 6).

Pins the planner shapes the hot paths depend on: exact-pattern lookups
must SEARCH an index (never SCAN the table), the check-log filter must
hit its index, and the jobs newest-first list must avoid a TEMP B-TREE
sort (PK backward walk). A future model refactor that drops an index or
reshapes these queries fails here instead of in production latency.

Substring LIKE scans (suggestions, list search) and small-table scans are
deliberately NOT pinned — reviewed, accepted, documented in the epic.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("sqlmodel")

from sqlmodel import SQLModel, select  # noqa: E402

from backend.models.entities import (  # noqa: E402
    Job,
    Shortcut,
    UpstreamCache,
    UpstreamCheckLog,
)


def _seeded_engine(tmp_path: Any):  # type: ignore[no-untyped-def]
    from sqlalchemy import create_engine as _create_engine

    engine = _create_engine(f"sqlite:///{tmp_path}/plans.db")
    SQLModel.metadata.create_all(engine)
    with engine.begin() as conn:
        for i in range(2000):
            conn.exec_driver_sql(
                "INSERT INTO shortcuts (pattern, type, target, access_count,"
                " created_at, updated_at, tags, visibility)"
                " VALUES (?, 'static', 'https://x.example', 0,"
                " '2026-01-01', '2026-01-01', '[]', 'public')",
                (f"p{i:04d}",))
        for i in range(500):
            conn.exec_driver_sql(
                "INSERT INTO upstream_cache (pattern, upstream_name, resolved_url,"
                " checked_at) VALUES (?, 'wiki', 'https://x.example', '2026-01-01')",
                (f"c{i:04d}",))
        for i in range(1000):
            conn.exec_driver_sql(
                "INSERT INTO upstream_check_log (pattern, upstream_name, result,"
                " tried_at, count, cached) VALUES (?, 'wiki', 'success',"
                " '2026-01-01', 1, 1)",
                (f"l{i:04d}",))
        for _ in range(100):
            conn.exec_driver_sql(
                "INSERT INTO jobs (kind, status, total, done, payload, created_at,"
                " updated_at) VALUES ('upstream_resync', 'succeeded', 1, 1, '{}',"
                " '2026-01-01', '2026-01-01')")
    return engine


def _explain(engine: Any, statement: Any) -> list[str]:
    compiled = statement.compile(compile_kwargs={"literal_binds": True})
    with engine.connect() as conn:
        rows = conn.exec_driver_sql(f"EXPLAIN QUERY PLAN {compiled}").all()
    return [str(r[3]) for r in rows]


def test_shortcut_lookup_searches_index(tmp_path: Any) -> None:
    engine = _seeded_engine(tmp_path)
    try:
        plan = _explain(engine, select(Shortcut).where(Shortcut.pattern == "p0001"))
        assert any("SEARCH" in line for line in plan), plan
        assert not any(line.startswith("SCAN ") for line in plan), plan
    finally:
        engine.dispose()


def test_upstream_cache_lookup_searches_index(tmp_path: Any) -> None:
    engine = _seeded_engine(tmp_path)
    try:
        plan = _explain(engine, select(UpstreamCache).where(UpstreamCache.pattern == "c0001"))
        assert any("SEARCH" in line for line in plan), plan
        assert not any(line.startswith("SCAN ") for line in plan), plan
    finally:
        engine.dispose()


def test_check_log_filter_searches_index(tmp_path: Any) -> None:
    engine = _seeded_engine(tmp_path)
    try:
        plan = _explain(
            engine,
            select(UpstreamCheckLog)
            .where(UpstreamCheckLog.upstream_name == "wiki")
            .order_by(UpstreamCheckLog.tried_at.desc())
            .limit(50),
        )
        assert any("SEARCH" in line for line in plan), plan
    finally:
        engine.dispose()


def test_jobs_newest_first_avoids_sort(tmp_path: Any) -> None:
    engine = _seeded_engine(tmp_path)
    try:
        plan = _explain(engine, select(Job).order_by(Job.id.desc()).limit(50))
        assert not any("TEMP B-TREE" in line for line in plan), plan
    finally:
        engine.dispose()
