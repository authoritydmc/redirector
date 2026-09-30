"""Best-effort v2 → v3 data import (EPIC-04).

v2 and v3 schemas differ on purpose (clean v3 redesign), so there is no
Alembic upgrade path — this script parses old rows + config JSON and inserts
normalized copies. Safe to re-run: existing patterns/names/keys are skipped.

Reads (stdlib sqlite3 — no dependency on the v2 ORM):
  <data-dir>/redirect.db          tables: redirects, upstream_cache,
                                  upstream_check_log, user_params
                                  (missing tables/columns tolerated)
  <data-dir>/redirect.config.json `upstreams` list → upstreams table

Usage:
  python -m backend.migrations.import_v2 --data-dir ./data \\
      --database-url sqlite:///./data/v3.db [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TARGET_TABLES = ("shortcuts", "upstreams", "upstream_cache",
                 "upstream_check_log", "user_params", "settings")


def parse_dt(raw: object) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(raw, tz=UTC)
    text = str(raw).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def split_tags(raw: object) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(t).strip() for t in raw if str(t).strip()]
    return [t.strip() for t in str(raw).split(",") if t.strip()]


@dataclass()
class ImportStats:
    inserted: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)
    normalized: int = 0

    def add(self, table: str, *, inserted: int = 0, skipped: int = 0) -> None:
        self.inserted[table] = self.inserted.get(table, 0) + inserted
        self.skipped[table] = self.skipped.get(table, 0) + skipped

    def summary(self) -> str:
        lines = []
        for table in TARGET_TABLES:
            lines.append(f"  {table}: +{self.inserted.get(table, 0)} "
                         f"skipped={self.skipped.get(table, 0)}")
        lines.append(f"  normalized values: {self.normalized}")
        return "\n".join(lines)


def read_table(conn: sqlite3.Connection, name: str) -> list[dict[str, Any]]:
    """Return rows as dicts; [] when the table doesn't exist (older v2 DBs)."""
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if name not in tables:
        return []
    cols = [info[1] for info in conn.execute(f"PRAGMA table_info({name})")]
    return [dict(zip(cols, row, strict=False)) for row in conn.execute(f"SELECT * FROM {name}")]


def _to_sync_url(url: str) -> str:
    return url.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg2")


def run_import(data_dir: Path, database_url: str, *, dry_run: bool = False) -> ImportStats:
    from sqlalchemy import create_engine
    from sqlmodel import Session, SQLModel, select

    from backend.models.entities import (
        Shortcut,
        ShortcutType,
        Upstream,
        UpstreamCache,
        UpstreamCheckLog,
        UserParam,
        Visibility,
    )

    stats = ImportStats()
    src = sqlite3.connect(data_dir / "redirect.db")

    engine = create_engine(_to_sync_url(database_url))
    SQLModel.metadata.create_all(engine)

    def known(model: object, column: str, values: set[str]) -> set[str]:
        # exec() yields scalars for single-column selects, Rows otherwise.
        with Session(engine) as s:
            rows = s.exec(select(getattr(model, column))).all()
            return {r[0] if isinstance(r, tuple) else r for r in rows} | values

    with Session(engine) as session:
        seen_patterns = known(Shortcut, "pattern", set())
        for row in read_table(src, "redirects"):
            pattern = (row.get("pattern") or "").strip()
            if not pattern or pattern in seen_patterns:
                stats.add("shortcuts", skipped=1)
                continue
            raw_type = (row.get("type") or "static").strip()
            raw_vis = (row.get("visibility") or "public").strip()
            try:
                stype = ShortcutType(raw_type)
            except ValueError:
                stype, stats.normalized = ShortcutType.STATIC, stats.normalized + 1
            try:
                vis = Visibility(raw_vis)
            except ValueError:
                vis, stats.normalized = Visibility.PUBLIC, stats.normalized + 1
            session.add(Shortcut(
                pattern=pattern,
                type=stype,
                target=row.get("target") or "",
                access_count=row.get("access_count") or 0,
                created_at=parse_dt(row.get("created_at")) or datetime.now(UTC),
                updated_at=parse_dt(row.get("updated_at")) or datetime.now(UTC),
                created_ip=row.get("created_ip"),
                updated_ip=row.get("updated_ip"),
                tags=split_tags(row.get("tags")),
                visibility=vis,
                expires_at=parse_dt(row.get("expires_at")),
                owner_email=row.get("owner_email"),
            ))
            seen_patterns.add(pattern)
            stats.add("shortcuts", inserted=1)

        seen_upstreams = known(Upstream, "name", set())
        config_path = data_dir / "redirect.config.json"
        if config_path.exists():
            try:
                upstreams = json.loads(config_path.read_text(encoding="utf-8")).get("upstreams", [])
            except (json.JSONDecodeError, AttributeError):
                upstreams = []
            for entry in upstreams:
                name = (entry.get("name") or "").strip()
                if not name or name in seen_upstreams:
                    stats.add("upstreams", skipped=1)
                    continue
                try:
                    fail_code = entry.get("fail_status_code")
                    fail_code = int(fail_code) if fail_code is not None else None
                except (TypeError, ValueError):
                    fail_code, stats.normalized = None, stats.normalized + 1
                session.add(Upstream(
                    name=name,
                    base_url=entry.get("base_url") or "",
                    fail_url=entry.get("fail_url"),
                    fail_status_code=fail_code,
                    verify_ssl=entry.get("verify_ssl", True),
                    skip_sso_cache=entry.get("skip_sso_cache", False),
                ))
                seen_upstreams.add(name)
                stats.add("upstreams", inserted=1)

        seen_cache: set[tuple[str, str]] = set()
        seen_logs: set[tuple[str, str]] = set()
        seen_params: set[tuple[str, str]] = set()
        with Session(engine) as probe:
            seen_cache = {(r[0], r[1]) for r in
                          probe.exec(select(UpstreamCache.pattern,
                                            UpstreamCache.upstream_name)).all()}
            seen_logs = {(r[0], r[1]) for r in
                         probe.exec(select(UpstreamCheckLog.pattern,
                                           UpstreamCheckLog.upstream_name)).all()}
            seen_params = {(r[0], r[1]) for r in
                           probe.exec(select(UserParam.shortcut_pattern,
                                             UserParam.param_name)).all()}
        for row in read_table(src, "upstream_cache"):
            key = (row.get("pattern") or "", row.get("upstream_name") or "")
            if not all(key) or key in seen_cache:
                stats.add("upstream_cache", skipped=1)
                continue
            session.add(UpstreamCache(
                pattern=key[0], upstream_name=key[1],
                resolved_url=row.get("resolved_url"),
                checked_at=parse_dt(row.get("checked_at")) or datetime.now(UTC),
            ))
            seen_cache.add(key)
            stats.add("upstream_cache", inserted=1)

        for row in read_table(src, "upstream_check_log"):
            key = ((row.get("pattern") or ""), (row.get("upstream_name") or ""))
            if key in seen_logs:
                stats.add("upstream_check_log", skipped=1)
                continue
            session.add(UpstreamCheckLog(
                pattern=key[0],
                upstream_name=key[1],
                check_url=row.get("check_url"),
                result=row.get("result"),
                detail=row.get("detail"),
                tried_at=parse_dt(row.get("tried_at")) or datetime.now(UTC),
                count=row.get("count") or 1,
                cached=bool(row.get("cached")),
            ))
            seen_logs.add(key)
            stats.add("upstream_check_log", inserted=1)

        for row in read_table(src, "user_params"):
            key = ((row.get("shortcut_pattern") or ""), (row.get("param_name") or ""))
            if not all(key) or key in seen_params:
                stats.add("user_params", skipped=1)
                continue
            session.add(UserParam(
                shortcut_pattern=key[0],
                param_name=key[1],
                description=row.get("description"),
                required=bool(row.get("required", False)),
                created_at=parse_dt(row.get("created_at")) or datetime.now(UTC),
                updated_at=parse_dt(row.get("updated_at")) or datetime.now(UTC),
            ))
            seen_params.add(key)
            stats.add("user_params", inserted=1)

        if dry_run:
            session.rollback()
        else:
            session.commit()
    src.close()
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import v2 data dir into a v3 database.")
    parser.add_argument("--data-dir", required=True, help="v2 data directory")
    parser.add_argument("--database-url", required=True, help="v3 database URL")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    stats = run_import(Path(args.data_dir), args.database_url, dry_run=args.dry_run)
    print(("DRY RUN\n" if args.dry_run else "") + stats.summary())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
