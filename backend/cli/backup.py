"""Pre-migration backup CLI (EPIC-08 task 4).

Creates a v3 backup archive without needing a running API — the command
the M3 cutover runbook invokes before flipping traffic
(`backup create --label pre-v3-m3`). Same archive format and safety rules
as the API path (traversal-safe naming, secrets hygiene per manifest).

Usage:
  python -m backend.cli.backup create [--label L]
      [--data-dir ./data] [--database-url sqlite+aiosqlite:///./data/v3.db]

Exit codes: 0 created (prints name + size), 1 failure, 2 bad usage.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from backend.core.config import settings  # noqa: E402
from backend.modules.backup.service import (  # noqa: E402
    backup_dir,
    create_backup,
    sanitize_label,
)


async def _create(label: str | None, database_url: str) -> int:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import backend.models.entities  # noqa: F401 (register tables)

    engine = create_async_engine(database_url)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            result = await create_backup(session, backup_dir(), label,
                                         settings.app_version)
        print(f"created {result.name} ({result.size_bytes} bytes, "
              f"{sum(result.tables.values())} rows)")
        return 0
    except Exception as exc:
        print(f"backup failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a v3 backup archive.")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create", help="write a new archive")
    create.add_argument("--label", default=None,
                        help="filename tag, [a-z0-9-] max 32 chars")
    create.add_argument("--data-dir", default=None,
                        help="overrides REDIRECTOR_DATA_DIR for this run")
    create.add_argument("--database-url", default=None,
                        help="overrides REDIRECTOR_DATABASE_URL for this run")
    args = parser.parse_args(argv)

    if args.command == "create":
        label = sanitize_label(args.label)
        if args.label is not None and label is None:
            print("error: label must match [a-z0-9-] (max 32 chars)",
                  file=sys.stderr)
            return 2
        if args.data_dir:
            settings.data_dir = Path(args.data_dir)
        database_url = args.database_url or settings.database_url
        return asyncio.run(_create(label, database_url))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
