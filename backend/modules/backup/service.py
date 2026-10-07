"""Backup creation/listing over the data-dir archive store (EPIC-04 task 7).

Archive layout (`redirector-backup-<UTC>-<label?>.zip`):
  manifest.json   kind/format/app_version/created_at, per-table row counts,
                  and a secrets manifest (counts + env-owned names only)
  <table>.json    row lists via `model_dump(mode="json")` (datetimes ISO,
                  enums as values — shaped for a future upsert restore)

Covered tables are domain state only: operational rows (check logs, jobs)
are ephemeral by design and excluded. Secrets hygiene: the only
secret-bearing table is `api_keys`, stored as sha256 (restorable, never
plaintext); env-owned secrets (admin password, JWT secret) are the
operator's to back up and are named — never valued — in the manifest.
"""

from __future__ import annotations

import asyncio
import io
import json
import re
import zipfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import SQLModel

from backend.core.config import settings
from backend.models.entities import (
    ApiKey,
    Setting,
    Shortcut,
    Upstream,
    UpstreamCache,
    UserParam,
)

TABLE_MODELS: dict[str, type[SQLModel]] = {
    "shortcuts": Shortcut,
    "upstreams": Upstream,
    "upstream_cache": UpstreamCache,
    "user_params": UserParam,
    "settings": Setting,
    "api_keys": ApiKey,
}

FILENAME_RE = re.compile(r"^redirector-backup-\d{8}T\d{6}Z(?:-[a-z0-9-]+)?(?:-\d+)?\.zip$")
_LABEL_RE = re.compile(r"^[a-z0-9-]{1,32}$")
FORMAT_VERSION = 1


@dataclass(frozen=True)
class BackupResult:
    name: str
    size_bytes: int
    created_at: str
    tables: dict[str, int]


def sanitize_label(label: str | None) -> str | None:
    """Normalize an operator label, or None when absent/invalid."""
    if label is None:
        return None
    clean = label.strip().lower()
    return clean if _LABEL_RE.fullmatch(clean) else None


def backup_dir() -> Path:
    path = settings.data_dir / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _manifest(app_version: str, created_at: str, tables: dict[str, int]) -> dict[str, Any]:
    return {
        "kind": "redirector-backup",
        "format_version": FORMAT_VERSION,
        "app_version": app_version,
        "created_at": created_at,
        "tables": tables,
        "secrets_manifest": {
            "api_key_hashes": tables.get("api_keys", 0),
            "plaintext_secret_values": [],
            "env_owned": ["REDIRECTOR_ADMIN_PASSWORD", "REDIRECTOR_JWT_SECRET"],
        },
    }


def _build_zip_bytes(
    tables_data: dict[str, list[dict[str, Any]]],
    manifest: dict[str, Any],
) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
        for table, rows in tables_data.items():
            archive.writestr(f"{table}.json", json.dumps(rows, indent=2, sort_keys=True))
    return buf.getvalue()


async def create_backup(
    session: AsyncSession,
    directory: Path,
    label: str | None,
    app_version: str,
    on_progress: Callable[[int, int], Awaitable[None]] | None = None,
) -> BackupResult:
    """Dump domain tables into a new archive; returns its descriptor."""
    directory.mkdir(parents=True, exist_ok=True)
    tables_data: dict[str, list[dict[str, Any]]] = {}
    names = list(TABLE_MODELS)
    for i, table in enumerate(names, start=1):
        rows = (await session.execute(select(TABLE_MODELS[table]))).scalars().all()
        tables_data[table] = [row.model_dump(mode="json") for row in rows]
        if on_progress is not None:
            await on_progress(i, len(names))
    created_at = datetime.now(UTC).isoformat()
    tables = {table: len(rows) for table, rows in tables_data.items()}
    manifest = _manifest(app_version, created_at, tables)

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    stem = f"redirector-backup-{stamp}" + (f"-{label}" if label else "")
    candidate = directory / f"{stem}.zip"
    suffix = 1
    while candidate.exists():
        suffix += 1
        candidate = directory / f"{stem}-{suffix}.zip"

    payload = await asyncio.to_thread(_build_zip_bytes, tables_data, manifest)
    await asyncio.to_thread(candidate.write_bytes, payload)
    return BackupResult(name=candidate.name, size_bytes=len(payload),
                        created_at=created_at, tables=tables)


def list_backups(directory: Path) -> list[BackupResult]:
    """Newest-first descriptors; skips files outside the naming contract."""
    results: list[BackupResult] = []
    if not directory.is_dir():
        return results
    for path in sorted(directory.iterdir(), key=lambda p: p.name, reverse=True):
        if not path.is_file() or not FILENAME_RE.fullmatch(path.name):
            continue
        try:
            with zipfile.ZipFile(path) as archive:
                manifest = json.loads(archive.read("manifest.json"))
            tables = {k: int(v) for k, v in manifest.get("tables", {}).items()}
        except (zipfile.BadZipFile, ValueError, KeyError, OSError):
            continue  # corrupt/foreign file: invisible, never fatal
        stat = path.stat()
        results.append(BackupResult(
            name=path.name, size_bytes=stat.st_size,
            created_at=str(manifest.get("created_at", "")), tables=tables,
        ))
    return results


def backup_path(directory: Path, name: str) -> Path | None:
    """Resolve an archive by exact name, or None (also on traversal attempts)."""
    if not FILENAME_RE.fullmatch(name):
        return None
    path = directory / name
    return path if path.is_file() else None
