"""Full-install backup and restore.

The existing ``/admin/export-redirects`` endpoint is a *shortcuts* export: one
JSON file with the ``redirects`` table, no ``user_params``, no upstream cache,
no configuration, and therefore no admin password or MFA secrets. It cannot
bring an install back after an upgrade gone wrong.

This module backs up the **whole install** instead, into a single zip:

    manifest.json          checksums, versions, row counts
    redirect.config.json   settings + secrets
    redirect.db            consistent SQLite snapshot
    state.json             install state (schema revision, last migration)
    RESTORE.txt            how to restore it, for humans

The SQLite snapshot uses the online backup API rather than ``cp`` so it is safe
to take while the app is serving traffic, and safe on Windows where a plain
file copy of a locked database fails outright.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import shutil
import sqlite3
import tempfile
import time
import zipfile

from .paths import (
    DEFAULT_DB_FILENAME,
    is_absolute_sqlite_path,
    is_sqlite_uri,
    sqlite_path_from_uri,
    uri_from_sqlite_path,
)
from .state import (
    BACKUP_DIRNAME,
    MANIFEST_FORMAT,
    MANIFEST_NAME,
    backup_dir,
    db_schema_revision,
    ensure_data_dir,
    list_backups,
    prune_backups,
    read_state,
    record_backup,
    utc_now,
    write_state,
)

logger = logging.getLogger(__name__)

CONFIG_BASENAME = "redirect.config.json"
STATE_BASENAME = "state.json"
RESTORE_BASENAME = "RESTORE.txt"
PENDING_RESTORE_FILENAME = ".restore-pending.zip"

# Tables counted in the manifest so an operator can eyeball what they are about
# to restore. Purely informational.
COUNTED_TABLES = ("redirects", "user_params", "upstream_cache", "upstream_check_log")

RESTORE_INSTRUCTIONS = """\
Restoring a Redirector backup
=============================

Preferred: the web UI
  1. docker compose up -d                 (or: docker start redirector)
  2. open http://localhost/admin/backup
  3. Drag this .zip onto "Restore from file"
  4. Restart: docker compose restart app

Plain Docker
  docker cp <this-file> redirector:/tmp/restore.zip
  docker exec redirector python -m app.utils.backup restore /tmp/restore.zip
  docker restart redirector

Source checkout (macOS / Linux)
  docker compose up -d
  # or, without Docker:
  python -m app.utils.backup restore /path/to/this-file.zip

Source checkout (Windows PowerShell)
  python -m app.utils.backup restore "C:\\path\\to\\this-file.zip"

The restore is transactional: your current data is snapshotted to
data/backups/ first, so a restore can always be undone by restoring that file.
"""


class BackupError(RuntimeError):
    """Raised when a backup cannot be created or a restore must be refused."""


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _timestamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S", time.gmtime())


def _slug(value: str, limit: int = 24) -> str:
    keep = [c if c.isalnum() or c in "-_" else "-" for c in (value or "")]
    return "".join(keep).strip("-").lower()[:limit] or "manual"


def resolve_db_path(db_uri: str, data_dir: str | None = None) -> str | None:
    """Absolute local filesystem path for a configured SQLite URI.

    The stored URI may be the portable relative form (``sqlite:///redirect.db``);
    it is anchored to the data directory here, never to the working directory.
    Returns ``None`` for external engines and in-memory databases.
    """
    if not db_uri or not is_sqlite_uri(db_uri):
        return None
    raw = sqlite_path_from_uri(str(db_uri))
    if not raw or raw == ":memory:" or raw.startswith("file:"):
        return None
    if is_absolute_sqlite_path(raw):
        return os.path.abspath(raw)
    base = data_dir or os.getcwd()
    return os.path.abspath(os.path.join(base, raw))


def snapshot_sqlite(source: str, destination: str) -> None:
    """Consistent copy of a SQLite database, safe to run against a live file.

    Uses ``sqlite3.Connection.backup``, which takes a read lock only for the
    duration of each page copy and honours the WAL. This is the one operation
    that works identically on Linux, macOS and Windows, where ``cp`` of an open
    database can silently produce a truncated file on Windows.
    """
    src = sqlite3.connect(source, timeout=30)
    try:
        # Fold any pending WAL back into the main file so the copy is
        # self-contained.
        try:
            src.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass
        dst = sqlite3.connect(destination, timeout=30)
        try:
            with dst:
                src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def _count_rows(db_file: str) -> dict:
    counts: dict[str, int] = {}
    if not os.path.isfile(db_file):
        return counts
    try:
        conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True, timeout=10)
    except sqlite3.Error:
        return counts
    try:
        existing = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        for table in COUNTED_TABLES:
            if table in existing:
                try:
                    counts[table] = conn.execute(
                        f"SELECT COUNT(*) FROM {table}"  # noqa: S608 - fixed table list
                    ).fetchone()[0]
                except sqlite3.Error:
                    continue
    except sqlite3.Error:
        pass
    finally:
        conn.close()
    return counts


# --------------------------------------------------------------------------
# Create
# --------------------------------------------------------------------------


def create_backup(
    data_dir: str,
    config_file: str,
    db_uri: str,
    app_version: str,
    kind: str = "manual",
    label: str = "",
    keep: int | None = None,
) -> dict:
    """Write ``data/backups/redirector-<kind>-<ts>.zip`` and return its manifest.

    ``kind`` is recorded in the manifest so the UI and the entrypoint can tell
    an automatic pre-upgrade snapshot from a manual one.
    """
    report = ensure_data_dir(data_dir)
    if not report["writable"]:
        raise BackupError(f"Cannot write backup: {report['error']}")

    db_path = resolve_db_path(db_uri, data_dir)
    if db_uri and not db_path:
        raise BackupError(
            "Backups can only snapshot a local SQLite database. This install "
            "uses an external database, so dump it with pg_dump or mysqldump "
            "and back up redirect.config.json separately."
        )

    target_dir = backup_dir(data_dir)
    os.makedirs(target_dir, exist_ok=True)
    name = f"redirector-{_slug(kind)}-{_timestamp()}"
    if label:
        name = f"{name}-{_slug(label, 16)}"
    target = os.path.join(target_dir, f"{name}.zip")

    staging = tempfile.mkdtemp(prefix=".backup-", dir=target_dir)
    try:
        payload: dict[str, str] = {}

        # 1. Database snapshot
        if db_path and os.path.isfile(db_path):
            snapshot_path = os.path.join(staging, DEFAULT_DB_FILENAME)
            snapshot_sqlite(db_path, snapshot_path)
            payload[DEFAULT_DB_FILENAME] = snapshot_path

        # 2. Configuration (contains the admin password and MFA secrets)
        if os.path.isfile(config_file):
            config_copy = os.path.join(staging, CONFIG_BASENAME)
            shutil.copy2(config_file, config_copy)
            payload[CONFIG_BASENAME] = config_copy

        # 3. Install state
        state_copy = os.path.join(staging, STATE_BASENAME)
        with open(state_copy, "w", encoding="utf-8") as handle:
            json.dump(read_state(data_dir), handle, indent=2, sort_keys=True)
        payload[STATE_BASENAME] = state_copy

        # The manifest must describe the *data*, so take the schema revision
        # from the database itself and only fall back to the state file.
        # Reading it from state would stamp a wrong (or missing) revision into
        # archives taken before the app had ever booted.
        schema_revision = db_schema_revision(
            uri_from_sqlite_path(db_path) if db_path else ""
        ) or read_state(data_dir).get("schema_revision")

        manifest = {
            "format": MANIFEST_FORMAT,
            "app": "redirector",
            "kind": kind,
            "label": label or None,
            "created_at": utc_now(),
            "app_version": app_version,
            "schema_revision": schema_revision,
            "host_os": os.name,
            "data_dir": data_dir,
            "files": {
                member: {
                    "sha256": _sha256(src),
                    "size": os.path.getsize(src),
                }
                for member, src in payload.items()
            },
            "tables": _count_rows(db_path) if db_path else {},
        }

        restore_path = os.path.join(staging, RESTORE_BASENAME)
        with open(restore_path, "w", encoding="utf-8") as handle:
            handle.write(RESTORE_INSTRUCTIONS)
        payload[RESTORE_BASENAME] = restore_path

        manifest["files"][RESTORE_BASENAME] = {
            "sha256": _sha256(restore_path),
            "size": os.path.getsize(restore_path),
        }

        manifest_blob = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(MANIFEST_NAME, manifest_blob)
            for member, src in payload.items():
                archive.write(src, arcname=member)

        size = os.path.getsize(target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    record_backup(data_dir, os.path.basename(target), kind, size)
    if keep is not None:
        prune_backups(data_dir, keep)

    logger.info(
        "Created %s backup %s (%d bytes, %s)",
        kind,
        os.path.basename(target),
        size,
        manifest["tables"] or "no local database",
    )
    return {"name": os.path.basename(target), "path": target, "manifest": manifest}


# --------------------------------------------------------------------------
# Inspect
# --------------------------------------------------------------------------


def inspect_archive(source) -> dict:
    """Validate an archive without writing anything.

    ``source`` may be a path or a binary file object.
    Returns ``{"ok": bool, "manifest": dict, "errors": [...]}``.
    """
    errors: list[str] = []
    manifest: dict = {}
    try:
        with zipfile.ZipFile(source) as archive:
            names = set(archive.namelist())
            if MANIFEST_NAME not in names:
                errors.append("archive has no manifest.json - not a Redirector backup")
                return {"ok": False, "manifest": {}, "errors": errors}
            try:
                manifest = json.loads(archive.read(MANIFEST_NAME).decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                errors.append(f"manifest.json is unreadable: {exc}")
                return {"ok": False, "manifest": {}, "errors": errors}

            for member, meta in (manifest.get("files") or {}).items():
                if member not in names:
                    errors.append(f"missing payload file: {member}")
                    continue
                digest = hashlib.sha256(archive.read(member)).hexdigest()
                if meta.get("sha256") and digest != meta["sha256"]:
                    errors.append(f"checksum mismatch for {member} - archive is corrupt")

            for required in (CONFIG_BASENAME,):
                if required not in names:
                    errors.append(f"missing payload file: {required}")
    except zipfile.BadZipFile as exc:
        errors.append(f"not a valid zip archive: {exc}")
    except OSError as exc:
        errors.append(f"could not read archive: {exc}")

    return {"ok": not errors, "manifest": manifest, "errors": errors}


def read_manifest_from_path(path: str) -> dict:
    with zipfile.ZipFile(path) as archive:
        return json.loads(archive.read(MANIFEST_NAME).decode("utf-8"))


# --------------------------------------------------------------------------
# Restore
# --------------------------------------------------------------------------


def _guard_revision(manifest: dict, current_head: str | None) -> str | None:
    """Public alias: see :func:`guard_revision`."""
    return guard_revision(manifest, current_head)


def guard_revision(manifest: dict, current_head: str | None) -> str | None:
    """Refuse archives that the running code cannot correctly interpret."""
    revision = manifest.get("schema_revision")
    if not revision or not current_head:
        return None
    if revision == current_head:
        return None
    from .state import migration_graph

    chain = migration_graph()["chain"]
    if revision in chain:
        # Older schema: fine, migrations will bring it forward after restore.
        return None
    return (
        f"This backup was written by a newer version of Redirector "
        f"(database schema {revision}); this install only knows up to "
        f"{current_head}. Upgrading Redirector first will not lose data - "
        f"start the newer image, then restore again."
    )


def restore_archive(
    source,
    data_dir: str,
    config_file: str,
    db_uri: str,
    current_head: str | None,
    safety_backup: bool = True,
) -> dict:
    """Replace the install with the contents of an archive.

    Safety ordering matters here, so it is explicit:

    1. validate (checksums, required members, schema compatibility)
    2. snapshot whatever is on disk right now
    3. write files to temp names and ``os.replace`` them into position
    4. leave the restored database exactly as the archive holds it

    Migrations are deliberately *not* run here. The restored database carries
    its own older ``alembic_version``; the next container start applies the
    remaining migrations, which is the same path a normal upgrade takes.

    This must be called with the application stopped. On Windows the open
    database file cannot be replaced at all, and on every OS replacing a file
    under a live engine is asking for trouble; use :func:`stage_pending_restore`
    for a restore requested through the web UI.
    """
    report = ensure_data_dir(data_dir)
    if not report["writable"]:
        raise BackupError(f"Cannot write to {data_dir}: {report['error']}")

    check = inspect_archive(source)
    if not check["ok"]:
        raise BackupError("Refusing to restore: " + "; ".join(check["errors"]))

    manifest = check["manifest"]
    blocked = _guard_revision(manifest, current_head)
    if blocked:
        raise BackupError(blocked)

    if isinstance(source, (str, bytes, os.PathLike)):
        archive_path = os.fspath(source)
        opener = lambda: zipfile.ZipFile(archive_path)  # noqa: E731
        close_after = True
    else:
        opener = lambda: zipfile.ZipFile(source)  # noqa: E731
        close_after = False

    saved = None
    if safety_backup:
        try:
            saved = create_backup(
                data_dir,
                config_file,
                db_uri,
                app_version=manifest.get("app_version", "unknown"),
                kind="pre-restore",
                keep=None,
            )
        except BackupError as exc:
            raise BackupError(f"Refusing to restore without a safety backup: {exc}")

    restored: list[str] = []
    archive = opener()
    try:
        names = set(archive.namelist())

        db_target = resolve_db_path(db_uri, data_dir)
        if DEFAULT_DB_FILENAME in names and db_target:
            _atomic_extract(archive, DEFAULT_DB_FILENAME, db_target)
            restored.append(DEFAULT_DB_FILENAME)

        if CONFIG_BASENAME in names:
            _atomic_extract(archive, CONFIG_BASENAME, config_file)
            restored.append(CONFIG_BASENAME)

        if STATE_BASENAME in names:
            try:
                blob = json.loads(archive.read(STATE_BASENAME).decode("utf-8"))
                if isinstance(blob, dict):
                    blob["restored_at"] = utc_now()
                    blob["restored_from"] = manifest.get("app_version")
                    write_state(data_dir, blob)
                    restored.append(STATE_BASENAME)
            except (ValueError, UnicodeDecodeError, OSError) as exc:
                logger.warning("Could not restore state.json: %s", exc)
    finally:
        if close_after:
            archive.close()

    write_state(
        data_dir,
        {
            "last_restore": {
                "at": utc_now(),
                "from_version": manifest.get("app_version"),
                "schema_revision": manifest.get("schema_revision"),
                "files": restored,
                "safety_backup": saved["name"] if saved else None,
            }
        },
    )
    logger.warning("Restored install from backup: %s", ", ".join(restored) or "nothing")
    return {
        "restored": restored,
        "manifest": manifest,
        "safety_backup": saved["name"] if saved else None,
    }


def _atomic_extract(archive: zipfile.ZipFile, member: str, target: str) -> None:
    """Extract one member via a temp file so a crash cannot truncate the target."""
    os.makedirs(os.path.dirname(os.path.abspath(target)) or ".", exist_ok=True)
    tmp = f"{target}.restore.{os.getpid()}"
    try:
        with archive.open(member) as src, open(tmp, "wb") as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)
            dst.flush()
            os.fsync(dst.fileno())
        os.replace(tmp, target)
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise BackupError(f"Could not write {target}: {exc}") from exc


# --------------------------------------------------------------------------
# Deferred restore
# --------------------------------------------------------------------------


def pending_restore_path(data_dir: str) -> str:
    return os.path.join(data_dir, PENDING_RESTORE_FILENAME)


def stage_pending_restore(data_dir: str, source, kind: str = "manual") -> dict:
    """Park an archive to be applied on the next application start.

    A restore requested through the running web UI cannot safely overwrite the
    database it is being served from: on Windows the file is locked outright,
    and on Linux it would pull the file out from under a live connection pool.
    Staging sidesteps both, and it is also the correct order of operations -
    the restored database may be an older schema, and the entrypoint runs
    migrations *after* applying a pending restore.
    """
    report = ensure_data_dir(data_dir)
    if not report["writable"]:
        raise BackupError(f"Cannot stage a restore: {report['error']}")

    target = pending_restore_path(data_dir)
    tmp = f"{target}.tmp.{os.getpid()}"
    try:
        with open(tmp, "wb") as dst:
            if isinstance(source, (str, bytes, os.PathLike)):
                with open(os.fspath(source), "rb") as src:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
            else:
                source.seek(0)
                shutil.copyfileobj(source, dst, 1024 * 1024)
            dst.flush()
            os.fsync(dst.fileno())
        os.replace(tmp, target)
    except OSError as exc:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise BackupError(f"Could not stage the restore: {exc}") from exc

    logger.warning("Restore staged to %s; it will be applied on the next start.", target)
    return {"staged": True, "pending": target, "kind": kind}


def consume_pending_restore(
    data_dir: str,
    config_file: str,
    db_uri: str,
    current_head: str | None,
    safety_backup: bool = True,
) -> dict | None:
    """Apply a staged restore. Returns ``None`` when nothing is pending.

    The staged archive is kept on disk until the restore succeeds, so a failure
    here leaves the operator able to retry after fixing the cause.
    """
    target = pending_restore_path(data_dir)
    if not os.path.isfile(target):
        return None
    logger.warning("Applying restore staged earlier: %s", target)
    result = restore_archive(
        target,
        data_dir,
        config_file,
        db_uri,
        current_head=current_head,
        safety_backup=safety_backup,
    )
    try:
        os.remove(target)
    except OSError as exc:
        logger.warning("Restore applied but %s could not be removed: %s", target, exc)
    result["deferred_applied"] = True
    return result


# --------------------------------------------------------------------------
# Summaries for the UI
# --------------------------------------------------------------------------


def backup_inventory(data_dir: str) -> list[dict]:
    """Backups on disk, annotated with their manifest summary when readable."""
    inventory = []
    for entry in list_backups(data_dir):
        item = dict(entry)
        try:
            manifest = read_manifest_from_path(entry["path"])
            item["kind"] = manifest.get("kind")
            item["app_version"] = manifest.get("app_version")
            item["schema_revision"] = manifest.get("schema_revision")
            item["tables"] = manifest.get("tables") or {}
            item["ok"] = True
        except (OSError, ValueError, KeyError, zipfile.BadZipFile):
            item["ok"] = False
        inventory.append(item)
    return inventory


def to_stream(archive_path: str) -> io.BytesIO:
    with open(archive_path, "rb") as handle:
        return io.BytesIO(handle.read())


# --------------------------------------------------------------------------
# CLI:  python -m app.utils.backup <create|list|inspect|restore> [args]
# --------------------------------------------------------------------------


def _printable_inventory(data_dir: str) -> int:
    entries = backup_inventory(data_dir)
    if not entries:
        print(f"No backups found in {backup_dir(data_dir)}")
        return 0
    print(f"Backups in {backup_dir(data_dir)}:")
    for item in entries:
        tables = item.get("tables") or {}
        detail = (
            f" redirects={tables.get('redirects', 0)}"
            f" user_params={tables.get('user_params', 0)}"
            if tables
            else " (no local database)"
        )
        print(
            f"  {item['created_at']}  {item['name']:<44} "
            f"{item['size_bytes'] / 1024:>9.1f} KiB  v{item.get('app_version', '?')}"
            f"  schema={item.get('schema_revision', '?')}{detail}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse

    from ..config import config
    from .paths import resolve_data_dir
    from .state import migration_graph

    parser = argparse.ArgumentParser(
        prog="python -m app.utils.backup",
        description="Create, list and restore full Redirector backups.",
    )
    sub = parser.add_subparsers(dest="command")
    create = sub.add_parser("create", help="write a new backup archive")
    create.add_argument("--label", default="", help="optional label in the filename")
    create.add_argument("--kind", default="manual")
    sub.add_parser("list", help="list backups on disk")
    inspect_cmd = sub.add_parser("inspect", help="validate an archive")
    inspect_cmd.add_argument("archive")
    restore = sub.add_parser("restore", help="restore an archive over this install")
    restore.add_argument("archive")
    restore.add_argument(
        "--no-safety-backup",
        action="store_true",
        help="skip the pre-restore snapshot of current data",
    )
    sub.add_parser("paths", help="print resolved data directory and database path")
    stage = sub.add_parser(
        "stage", help="park an archive to be applied on the next application start"
    )
    stage.add_argument("archive")
    sub.add_parser(
        "apply-pending", help="apply a staged restore (used by the container entrypoint)"
    )

    args = parser.parse_args(argv)
    command = args.command or "list"
    data_dir = resolve_data_dir()
    # Use the resolved (absolute) URI, not the portable stored one.
    db_uri = getattr(config, 'resolved_database', None) or config.get_configuration().get("database")

    if command == "paths":
        print(f"data dir   : {data_dir}")
        print(f"config     : {config.CONFIG_FILE}")
        print(f"stored uri : {config.get_configuration().get('database')}")
        print(f"resolved   : {db_uri}")
        resolved = resolve_db_path(db_uri, data_dir)
        print(f"db file    : {resolved if resolved else 'not a local SQLite file'}")
        return 0

    if command == "list":
        return _printable_inventory(data_dir)

    if command == "create":
        from ..CONSTANTS import get_semver

        result = create_backup(
            data_dir,
            config.CONFIG_FILE,
            db_uri,
            app_version=get_semver(),
            kind=args.kind,
            label=args.label,
        )
        print(result["path"])
        return 0

    if command == "inspect":
        result = inspect_archive(args.archive)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["ok"] else 1

    if command == "restore":
        from ..CONSTANTS import get_semver

        result = restore_archive(
            args.archive,
            data_dir,
            config.CONFIG_FILE,
            db_uri,
            current_head=migration_graph()["head"],
            safety_backup=not args.no_safety_backup,
        )
        print(f"Restored: {', '.join(result['restored']) or 'nothing'}")
        print("Restart the app so migrations can finish:  docker compose restart app")
        return 0

    if command == "apply-pending":
        from ..CONSTANTS import get_semver  # noqa: F401 - keeps import order stable

        result = consume_pending_restore(
            data_dir,
            config.CONFIG_FILE,
            db_uri,
            current_head=migration_graph()["head"],
        )
        if result is None:
            print("No restore pending.")
            return 0
        print(f"Applied staged restore: {', '.join(result['restored'])}")
        return 0

    if command == "stage":
        result = stage_pending_restore(data_dir, args.archive)
        print(result["pending"])
        print("Restart the app to apply it.")
        return 0

    parser.print_help()
    return 1

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
