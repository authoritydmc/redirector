"""Install state, schema introspection and backup bookkeeping.

The database alone does not tell you what you are running. This module keeps a
small, human-readable JSON file next to the data that answers the three
questions an upgrade needs:

1. *Which schema revision is this install actually on?*
   Read from the ``alembic_version`` table of the live database.
2. *Is the image newer or older than that data?*
   Compared against the migration files shipped with this checkout, so an
   accidental image rollback is detected before it can corrupt the schema.
3. *What backups exist, and did the last migration succeed?*
   Recorded in ``data/.redirector-state.json``.

The state file is written with the same atomic replace used for the main
config, because a truncated state file must never stop the app from booting.
"""

from __future__ import annotations

import glob
import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timezone

from .paths import (
    DEFAULT_DATA_DIR_NAME,
    SQLITE_PREFIX,
    is_absolute_sqlite_path,
    sqlite_path_from_uri,
)

logger = logging.getLogger(__name__)

STATE_FILENAME = ".redirector-state.json"
BACKUP_DIRNAME = "backups"
STATE_VERSION = 1
DEFAULT_BACKUP_RETENTION = 10

MANIFEST_NAME = "manifest.json"
MANIFEST_FORMAT = 2


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def state_path(data_dir: str) -> str:
    return os.path.join(data_dir, STATE_FILENAME)


def backup_dir(data_dir: str) -> str:
    return os.path.join(data_dir, BACKUP_DIRNAME)


def read_state(data_dir: str) -> dict:
    """Read the state file, tolerating absence and corruption."""
    path = state_path(data_dir)
    if not os.path.isfile(path):
        return {"state_version": STATE_VERSION}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError("state file is not a JSON object")
        data.setdefault("state_version", STATE_VERSION)
        return data
    except (OSError, ValueError) as exc:
        logger.warning("State file %s unreadable (%s); continuing with defaults.", path, exc)
        return {"state_version": STATE_VERSION}


def write_state(data_dir: str, updates: dict) -> dict:
    """Merge ``updates`` into the state file and persist it atomically."""
    state = read_state(data_dir)
    state.update(updates)
    state["state_version"] = STATE_VERSION
    state["updated_at"] = utc_now()
    path = state_path(data_dir)
    tmp = f"{path}.tmp.{os.getpid()}"
    try:
        os.makedirs(data_dir, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        logger.error("Could not persist state file %s: %s", path, exc)
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
    return state


# --------------------------------------------------------------------------
# Migration graph introspection
# --------------------------------------------------------------------------

_REVISION_RE = re.compile(r"^revision(?:\s*:\s*str)?\s*=\s*['\"]([^'\"]+)['\"]", re.M)
_DOWN_REVISION_RE = re.compile(
    r"^down_revision(?:\s*:\s*[^=]+)?\s*=\s*(.+)$", re.M
)


def _versions_dir() -> str:
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "migrations",
        "versions",
    )


def migration_graph() -> dict:
    """Parse ``migrations/versions/*.py`` without importing Alembic.

    Returns ``{"revisions": {rev: {"down": rev|None, "file": name}}, "head": rev|None,
    "chain": [rev, ...]}``.

    Parsing the files keeps this usable from the entrypoint script, from the
    web UI and from tests, none of which have a live Alembic context.
    """
    revisions: dict[str, dict] = {}
    for path in sorted(glob.glob(os.path.join(_versions_dir(), "*.py"))):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                source = handle.read()
        except OSError:
            continue
        rev_match = _REVISION_RE.search(source)
        if not rev_match:
            continue
        down_match = _DOWN_REVISION_RE.search(source)
        down = None
        if down_match:
            raw = down_match.group(1).strip()
            if raw and raw != "None":
                down = raw.strip("'\"")
        revisions[rev_match.group(1)] = {
            "down": down,
            "file": os.path.basename(path),
        }

    parents = {info["down"] for info in revisions.values() if info["down"]}
    heads = [rev for rev in revisions if rev not in parents]
    head = heads[0] if len(heads) == 1 else None

    chain: list[str] = []
    cursor = head
    guard = 0
    while cursor and cursor in revisions and guard <= len(revisions):
        chain.append(cursor)
        cursor = revisions[cursor]["down"]
        guard += 1
    chain.reverse()

    return {
        "revisions": revisions,
        "head": head,
        "heads": sorted(heads),
        "chain": chain,
    }


def _sqlite_file_for_uri(uri: str) -> str | None:
    """Best-effort local file path for a SQLite URI (``None`` for other engines)."""
    if not uri or not str(uri).lower().startswith(SQLITE_PREFIX):
        return None
    raw = sqlite_path_from_uri(str(uri))
    if not raw or raw == ":memory:" or raw.startswith("file:"):
        return None
    if is_absolute_sqlite_path(raw) and re.match(r"^[A-Za-z]:[\\/]", raw):
        # Absolute Windows path recorded by an older release.
        return raw
    return raw


def db_schema_revision(db_uri: str) -> str | None:
    """Read ``alembic_version.version_num`` straight from the SQLite file.

    Reading the file rather than going through SQLAlchemy keeps this callable
    before the app object exists, which is exactly when the entrypoint needs it.
    """
    path = _sqlite_file_for_uri(db_uri)
    if not path:
        return None
    for candidate in (path, path.lstrip("/\\")):
        if not os.path.isfile(candidate):
            continue
        try:
            conn = sqlite3.connect(f"file:{candidate}?mode=ro", uri=True, timeout=5)
        except sqlite3.Error as exc:
            logger.debug("Could not open %s read-only: %s", candidate, exc)
            continue
        try:
            row = conn.execute(
                "SELECT version_num FROM alembic_version LIMIT 1"
            ).fetchone()
            return row[0] if row else None
        except sqlite3.Error:
            # Table missing -> database predates Alembic stamping.
            return None
        finally:
            conn.close()
    return None


def schema_status(db_uri: str) -> dict:
    """Compare the live database against the migrations shipped in this image.

    ``drift`` is the important field:
      * ``up-to-date``   - nothing to do
      * ``pending``      - migrations will run on start
      * ``ahead``        - data was written by a NEWER image; refuse to touch it
      * ``unstamped``    - tables exist but Alembic has no record of them
      * ``fresh``        - no database file yet
      * ``unknown``      - non-SQLite engine, cannot be inspected offline
    """
    graph = migration_graph()
    current = db_schema_revision(db_uri)
    head = graph["head"]
    chain = graph["chain"]

    if _sqlite_file_for_uri(db_uri) is None:
        drift = "unknown"
    elif current is None:
        path = _sqlite_file_for_uri(db_uri)
        exists = bool(path) and any(
            os.path.isfile(p) for p in (path, path.lstrip("/\\"))
        )
        drift = "unstamped" if exists else "fresh"
    elif head is None:
        drift = "unknown"
    elif current == head:
        drift = "up-to-date"
    elif current in chain:
        drift = "pending"
    else:
        # Revision unknown to this checkout: either newer image, or a history
        # that has been rebased. Both cases must stop automatic migration.
        drift = "ahead"

    if current in chain:
        pending = chain[chain.index(current) + 1:]
    else:
        pending = []

    return {
        "current": current,
        "head": head,
        "pending": pending,
        "chain": chain,
        "heads": graph["heads"],
        "drift": drift,
        "multiple_heads": len(graph["heads"]) > 1,
    }


# --------------------------------------------------------------------------
# Backup bookkeeping
# --------------------------------------------------------------------------


def list_backups(data_dir: str) -> list[dict]:
    """Enumerate local backup archives, newest first."""
    pattern = os.path.join(backup_dir(data_dir), "*.zip")
    entries: list[dict] = []
    for path in glob.glob(pattern):
        try:
            stat = os.stat(path)
        except OSError:
            continue
        entries.append(
            {
                "name": os.path.basename(path),
                "path": path,
                "size_bytes": stat.st_size,
                "created_at": datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ).isoformat(timespec="seconds"),
            }
        )
    entries.sort(key=lambda item: item["created_at"], reverse=True)
    return entries


def prune_backups(data_dir: str, keep: int = DEFAULT_BACKUP_RETENTION) -> list[str]:
    """Delete the oldest local archives beyond ``keep``. Returns removed names."""
    if keep <= 0:
        return []
    entries = list_backups(data_dir)
    removed = []
    for entry in entries[keep:]:
        try:
            os.remove(entry["path"])
            removed.append(entry["name"])
        except OSError as exc:
            logger.warning("Could not prune backup %s: %s", entry["name"], exc)
    if removed:
        logger.info("Pruned %d old backup(s) beyond the %d kept.", len(removed), keep)
    return removed


def record_backup(data_dir: str, name: str, kind: str, size_bytes: int = 0) -> dict:
    backup = {
        "name": name,
        "kind": kind,
        "size_bytes": size_bytes,
        "created_at": utc_now(),
    }
    return write_state(data_dir, {"last_backup": backup})


def record_migration(data_dir: str, status: str, **details) -> dict:
    payload = {"status": status, "at": utc_now()}
    payload.update({k: v for k, v in details.items() if v is not None})
    return write_state(data_dir, {"last_migration": payload})


def ensure_data_dir(data_dir: str) -> dict:
    """Create the data directory tree and verify it is writable.

    Returns a report instead of raising, so the entrypoint can print a single
    actionable message that names the exact directory to fix.
    """
    report = {"data_dir": data_dir, "writable": False, "created": [], "error": None}
    for sub in ("", BACKUP_DIRNAME):
        target = os.path.join(data_dir, sub) if sub else data_dir
        if not os.path.isdir(target):
            try:
                os.makedirs(target, exist_ok=True)
                report["created"].append(target)
            except OSError as exc:
                # Curated literal: this report is served verbatim by the public
                # health endpoint, so OS error text stays in the log only.
                logger.warning("Cannot create data directory %s: %s", target, exc)
                report["error"] = f"cannot create {target}"
                return report
    probe = os.path.join(data_dir, ".write-probe")
    try:
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write("ok")
        os.remove(probe)
        report["writable"] = True
    except OSError as exc:
        logger.warning("Data directory %s is not writable: %s", data_dir, exc)
        report["error"] = f"{data_dir} is not writable"
    return report
