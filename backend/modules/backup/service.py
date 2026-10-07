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
    ShortcutType,
    Upstream,
    UpstreamCache,
    UserParam,
    Visibility,
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


class RestoreError(ValueError):
    """Archive unusable for restore (corrupt, foreign, or wrong format)."""


@dataclass(frozen=True)
class RestoreResult:
    safety_backup: str
    restored: dict[str, int]


def _parse_dt(raw: object) -> datetime | None:
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
        raise RestoreError(f"unparseable datetime: {raw!r}") from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _read_archive(path: Path) -> dict[str, list[dict[str, Any]]]:
    """Read + validate an archive; raises RestoreError on any defect."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            try:
                manifest = json.loads(archive.read("manifest.json"))
            except (KeyError, ValueError) as exc:
                raise RestoreError(f"backup is missing a valid manifest: {exc}") from exc
            if manifest.get("kind") != "redirector-backup":
                raise RestoreError(f"not a redirector backup (kind={manifest.get('kind')!r})")
            if manifest.get("format_version") != FORMAT_VERSION:
                raise RestoreError(
                    f"unsupported backup format {manifest.get('format_version')!r} "
                    f"(this server reads {FORMAT_VERSION})")
            tables: dict[str, list[dict[str, Any]]] = {}
            for table in TABLE_MODELS:
                if f"{table}.json" not in names:
                    continue  # older archive: tolerate missing tables
                try:
                    rows = json.loads(archive.read(f"{table}.json"))
                except ValueError as exc:
                    raise RestoreError(f"corrupt table dump: {table}.json") from exc
                if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
                    raise RestoreError(f"corrupt table dump: {table}.json (want a row list)")
                tables[table] = rows
    except zipfile.BadZipFile as exc:
        raise RestoreError("not a valid zip archive") from exc
    except OSError as exc:
        raise RestoreError(f"unreadable archive: {exc}") from exc
    return tables


def _nonempty_str(data: dict[str, Any], table: str, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RestoreError(f"corrupt {table} row: {key!r} must be a non-empty string")
    return value


def _validated_rows(table: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Structural validation pass (no DB writes): enums parse, datetimes
    parse, wrongly-typed fields fail loud before anything is applied."""
    for data in rows:
        if table == "shortcuts":
            _nonempty_str(data, table, "pattern")
            try:
                ShortcutType(data.get("type", "static"))
                Visibility(data.get("visibility", "public"))
            except ValueError as exc:
                raise RestoreError(f"corrupt shortcuts row: {exc}") from exc
            _parse_dt(data.get("expires_at"))
            if not isinstance(data.get("tags", []), list):
                raise RestoreError("corrupt shortcuts row: 'tags' must be a list")
        elif table == "upstreams":
            _nonempty_str(data, table, "name")
            fail_code = data.get("fail_status_code")
            if fail_code is not None and (isinstance(fail_code, bool) or not isinstance(fail_code, int)):
                raise RestoreError("corrupt upstreams row: 'fail_status_code' must be int|null")
            for flag in ("verify_ssl", "skip_sso_cache"):
                if flag in data and not isinstance(data[flag], bool):
                    raise RestoreError(f"corrupt upstreams row: {flag!r} must be bool")
        elif table == "upstream_cache":
            _nonempty_str(data, table, "pattern")
            _nonempty_str(data, table, "upstream_name")
            _parse_dt(data.get("checked_at"))
        elif table == "user_params":
            _nonempty_str(data, table, "shortcut_pattern")
            _nonempty_str(data, table, "param_name")
            if not isinstance(data.get("required", False), bool):
                raise RestoreError("corrupt user_params row: 'required' must be bool")
        elif table == "settings":
            _nonempty_str(data, table, "key")
        elif table == "api_keys":
            _nonempty_str(data, table, "prefix")
            _nonempty_str(data, table, "secret_hash")
            _nonempty_str(data, table, "name")
            if not isinstance(data.get("scopes", []), list):
                raise RestoreError("corrupt api_keys row: 'scopes' must be a list")
            for stamp in ("created_at", "last_used_at", "revoked_at"):
                _parse_dt(data.get(stamp))
    return rows


def _dt_or_now(raw: object) -> datetime:
    return _parse_dt(raw) or datetime.now(UTC)


async def _apply_table(session: AsyncSession, table: str, rows: list[dict[str, Any]]) -> int:
    """Merge validated rows over live rows by natural key.

    Matches update every column except the key (point-in-time for covered
    tables); misses insert fresh rows with DB-assigned ids (explicit dumped
    ids are never reused, so Postgres sequences can't collide). Returns the
    rows written.
    """
    written = 0
    if table == "shortcuts":
        for data in rows:
            shortcut = (await session.execute(
                select(Shortcut).where(Shortcut.pattern == data["pattern"])
            )).scalar_one_or_none()
            fields = {
                "type": ShortcutType(data.get("type", "static")),
                "target": data.get("target", ""),
                "access_count": data.get("access_count") or 0,
                "created_at": _dt_or_now(data.get("created_at")),
                "updated_at": _dt_or_now(data.get("updated_at")),
                "created_ip": data.get("created_ip"),
                "updated_ip": data.get("updated_ip"),
                "tags": data.get("tags") or [],
                "visibility": Visibility(data.get("visibility", "public")),
                "expires_at": _parse_dt(data.get("expires_at")),
                "owner_email": data.get("owner_email"),
            }
            if shortcut is None:
                session.add(Shortcut(pattern=data["pattern"], **fields))
            else:
                for attr, value in fields.items():
                    setattr(shortcut, attr, value)
            written += 1
    elif table == "upstreams":
        for data in rows:
            upstream = (await session.execute(
                select(Upstream).where(Upstream.name == data["name"])
            )).scalar_one_or_none()
            fields = {
                "base_url": data.get("base_url") or "",
                "fail_url": data.get("fail_url"),
                "fail_status_code": data.get("fail_status_code"),
                "verify_ssl": data.get("verify_ssl", True),
                "skip_sso_cache": data.get("skip_sso_cache", False),
            }
            if upstream is None:
                session.add(Upstream(name=data["name"], **fields))
            else:
                for attr, value in fields.items():
                    setattr(upstream, attr, value)
            written += 1
    elif table == "upstream_cache":
        for data in rows:
            cached = (await session.execute(
                select(UpstreamCache).where(
                    UpstreamCache.pattern == data["pattern"],
                    UpstreamCache.upstream_name == data["upstream_name"],
                )
            )).scalar_one_or_none()
            if cached is None:
                session.add(UpstreamCache(
                    pattern=data["pattern"], upstream_name=data["upstream_name"],
                    resolved_url=data.get("resolved_url"),
                    checked_at=_dt_or_now(data.get("checked_at"))))
            else:
                cached.resolved_url = data.get("resolved_url")
                cached.checked_at = _dt_or_now(data.get("checked_at"))
            written += 1
    elif table == "user_params":
        for data in rows:
            param = (await session.execute(
                select(UserParam).where(
                    UserParam.shortcut_pattern == data["shortcut_pattern"],
                    UserParam.param_name == data["param_name"],
                )
            )).scalar_one_or_none()
            if param is None:
                session.add(UserParam(
                    shortcut_pattern=data["shortcut_pattern"],
                    param_name=data["param_name"],
                    description=data.get("description"),
                    required=data.get("required", False),
                    created_at=_dt_or_now(data.get("created_at")),
                    updated_at=_dt_or_now(data.get("updated_at"))))
            else:
                param.description = data.get("description")
                param.required = data.get("required", False)
                param.created_at = _dt_or_now(data.get("created_at"))
                param.updated_at = _dt_or_now(data.get("updated_at"))
            written += 1
    elif table == "settings":
        for data in rows:
            setting = (await session.execute(
                select(Setting).where(Setting.key == data["key"])
            )).scalar_one_or_none()
            if setting is None:
                session.add(Setting(key=data["key"], value=data.get("value")))
            else:
                setting.value = data.get("value")
                setting.updated_at = _dt_or_now(data.get("updated_at"))
            written += 1
    elif table == "api_keys":
        for data in rows:
            api_key = (await session.execute(
                select(ApiKey).where(ApiKey.prefix == data["prefix"])
            )).scalar_one_or_none()
            fields = {
                "secret_hash": data["secret_hash"],
                "name": data["name"],
                "scopes": data.get("scopes") or [],
                "created_at": _dt_or_now(data.get("created_at")),
                "last_used_at": _parse_dt(data.get("last_used_at")),
                "revoked_at": _parse_dt(data.get("revoked_at")),
            }
            if api_key is None:
                session.add(ApiKey(prefix=data["prefix"], **fields))
            else:
                for attr, value in fields.items():
                    setattr(api_key, attr, value)
            written += 1
    await session.commit()
    return written


async def restore_backup(
    session: AsyncSession,
    directory: Path,
    name: str,
    app_version: str,
    on_progress: Callable[[int, int], Awaitable[None]] | None = None,
) -> RestoreResult:
    """Restore an archive over live rows (merge-upsert by natural key).

    Validate-then-apply: the whole archive is validated before the safety
    backup is even taken, so a corrupt archive fails without touching live
    data (or the backups dir). Operational tables are untouched, as on the
    backup side.
    """
    path = backup_path(directory, name)
    if path is None:
        raise RestoreError(f"backup '{name}' not found")
    tables = _read_archive(path)
    for table, rows in tables.items():
        _validated_rows(table, rows)
    safety = await create_backup(session, directory, "pre-restore", app_version)
    restored: dict[str, int] = {}
    names = list(tables)
    for i, table in enumerate(names, start=1):
        restored[table] = await _apply_table(session, table, tables[table])
        if on_progress is not None:
            await on_progress(i, len(names))
    return RestoreResult(safety_backup=safety.name, restored=restored)
