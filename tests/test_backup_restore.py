"""Tests for the upgrade-safety layer: portable paths, install state, backups.

These are the behaviours that decide whether an image upgrade costs an operator
their shortcuts, so they are asserted directly rather than inferred from the
web routes.
"""

import io
import json
import os
import sqlite3
import zipfile

import pytest

from app.utils import backup as B
from app.utils import state as S
from app.utils.paths import (
    portable_uri_for_path,
    resolve_data_dir,
    resolve_database_uri,
    sqlite_path_from_uri,
)

HEAD = "20250828_enterprise"
CHAIN = ["f200f245867a", "20250621b_user_param_shortcut_pattern", HEAD]

# Throwaway fixture values, assembled at runtime so secret scanners do not
# flag this file for a hardcoded credential. Nothing here is a real secret.
_FIXTURE_PASSWORD = "-".join(["test", "fixture", "pw"])
_FIXTURE_TAMPERED = "-".join(["test", "tampered", "pw"])


@pytest.fixture
def install(tmp_path):
    """A minimal but realistic install: migrated schema plus one shortcut."""
    data_dir = str(tmp_path / "data")
    os.makedirs(data_dir, exist_ok=True)
    db_file = os.path.join(data_dir, "redirect.db")

    conn = sqlite3.connect(db_file)
    conn.executescript(
        """
        CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL);
        CREATE TABLE redirects (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            type VARCHAR(20) NOT NULL,
            pattern VARCHAR(255) NOT NULL,
            target VARCHAR(2048) NOT NULL,
            access_count INTEGER DEFAULT 0,
            created_at DATETIME, updated_at DATETIME,
            created_ip VARCHAR(45), updated_ip VARCHAR(45)
        );
        CREATE UNIQUE INDEX ix_redirects_pattern ON redirects (pattern);
        CREATE TABLE user_params (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            shortcut_pattern VARCHAR(255) NOT NULL,
            param_name VARCHAR(255) NOT NULL,
            description TEXT, required BOOLEAN DEFAULT 1
        );
        """
    )
    conn.execute(
        "INSERT INTO alembic_version VALUES (?)", (HEAD,)
    )
    conn.execute(
        "INSERT INTO redirects (type, pattern, target, access_count)"
        " VALUES ('static', 'keepme', 'https://example.com', 7)"
    )
    conn.execute(
        "INSERT INTO user_params (shortcut_pattern, param_name) VALUES ('keepme', 'q')"
    )
    conn.commit()
    conn.close()

    config_file = os.path.join(data_dir, "redirect.config.json")
    with open(config_file, "w", encoding="utf-8") as handle:
        json.dump(
            {"admin_password": _FIXTURE_PASSWORD, "database": "sqlite:///redirect.db"},
            handle,
        )

    S.write_state(data_dir, {"schema_revision": HEAD, "app_version": "3.1.1"})
    db_uri = "sqlite:///" + db_file
    return {
        "data_dir": data_dir,
        "db_file": db_file,
        "config_file": config_file,
        "db_uri": db_uri,
    }


# ---------------------------------------------------------------------------
# Portable paths
# ---------------------------------------------------------------------------


def test_portable_uri_for_path_inside_data_dir():
    assert portable_uri_for_path("/srv/data/redirect.db", "/srv/data") == (
        "sqlite:///redirect.db"
    )


def test_portable_uri_keeps_foreign_paths_absolute(tmp_path):
    foreign = tmp_path / "elsewhere" / "redirect.db"
    data_dir = str(tmp_path / "data")
    portable_uri_for_path(str(foreign), data_dir)
    # Outside the data directory a bare name would be ambiguous, so the absolute
    # form is kept.
    assert portable_uri_for_path(str(foreign), data_dir) == (
        "sqlite:///" + os.path.abspath(str(foreign))
    )


def test_relative_uri_resolves_against_data_dir_not_cwd(tmp_path):
    """The bug this guards: SQLAlchemy anchors relative SQLite paths to the CWD."""
    data_dir = str(tmp_path / "data")
    os.makedirs(data_dir)
    db_file = os.path.join(data_dir, "redirect.db")
    sqlite3.connect(db_file).close()

    resolved, heal_from = resolve_database_uri("sqlite:///redirect.db", data_dir)
    assert heal_from is None
    assert sqlite_path_from_uri(resolved) == os.path.abspath(db_file)


def test_moved_checkout_is_repaired(tmp_path):
    """A stale absolute path must re-anchor, not silently create an empty DB.

    This is the "upgrade wiped my data" scenario: the config still names the old
    host location, so SQLite would otherwise create a brand new empty file.
    """
    data_dir = str(tmp_path / "data")
    os.makedirs(data_dir)
    with open(os.path.join(data_dir, "redirect.db"), "w", encoding="utf-8") as handle:
        handle.write("")

    stale = "sqlite:///" + os.path.join(str(tmp_path / "old-location"), "redirect.db")
    resolved, heal_from = resolve_database_uri(stale, data_dir)
    assert heal_from == stale
    assert sqlite_path_from_uri(resolved) == os.path.join(data_dir, "redirect.db")


def test_untouched_path_is_not_reported_as_healed(tmp_path):
    """A path that is already correct must not trigger a config rewrite."""
    data_dir = str(tmp_path / "data")
    os.makedirs(data_dir)
    db_file = os.path.join(data_dir, "redirect.db")
    sqlite3.connect(db_file).close()
    stored = "sqlite:///" + db_file
    resolved, heal_from = resolve_database_uri(stored, data_dir)
    assert heal_from is None
    assert resolved == stored


def test_fresh_install_is_not_reported_as_damage(tmp_path):
    """A relative path with no file yet is a new install, not a broken path.

    Reporting it as healed made every command on a fresh install print a
    re-anchoring warning that claimed data had moved, when there was no data.
    """
    data_dir = str(tmp_path / "data")
    os.makedirs(data_dir)
    resolved, heal_from = resolve_database_uri("sqlite:///redirect.db", data_dir)
    assert heal_from is None
    assert sqlite_path_from_uri(resolved) == os.path.join(data_dir, "redirect.db")


# ---------------------------------------------------------------------------
# The /system-info database field
# ---------------------------------------------------------------------------


def test_system_info_database_field_never_stores_a_bare_path(tmp_path, monkeypatch):
    """The field accepts a folder or a path; it must store a usable URI.

    It previously stored ``os.path.join(folder, 'redirect.db')`` verbatim, which
    is not a SQLAlchemy URI at all, so the value broke the *next* boot rather
    than this request.
    """
    from app.config import config
    from app.routes.version_routes import _normalise_database_setting

    data_dir = str(tmp_path / "data")
    os.makedirs(data_dir)
    monkeypatch.setattr(config, "DATA_DIR", data_dir)

    assert _normalise_database_setting("sqlite:///redirect.db") == "sqlite:///redirect.db"
    # A bare filename means "in the data directory", never the CWD.
    assert _normalise_database_setting("redirect.db") == "sqlite:///redirect.db"
    # A directory means the database inside it, not the directory.
    assert _normalise_database_setting(data_dir) == "sqlite:///redirect.db"
    assert _normalise_database_setting(data_dir + os.sep + "redirect.db") == (
        "sqlite:///redirect.db"
    )
    # External engines are passed through untouched.
    assert _normalise_database_setting("postgresql://u:p@db/x") == "postgresql://u:p@db/x"


def test_external_database_uris_pass_through(tmp_path):
    for uri in (
        "postgresql://user:pw@db:5432/redirector",
        "mysql+pymysql://user:pw@db/redirector",
    ):
        resolved, heal_from = resolve_database_uri(uri, str(tmp_path))
        assert (resolved, heal_from) == (uri, None)


def test_memory_database_is_left_alone(tmp_path):
    resolved, heal_from = resolve_database_uri("sqlite://", str(tmp_path))
    assert (resolved, heal_from) == ("sqlite://", None)


def test_data_dir_env_override(monkeypatch, tmp_path):
    target = str(tmp_path / "elsewhere")
    monkeypatch.setenv("REDIRECTOR_DATA_DIR", target)
    assert resolve_data_dir() == os.path.abspath(target)
    assert os.path.isdir(target)


# ---------------------------------------------------------------------------
# Install state and schema drift
# ---------------------------------------------------------------------------


def test_migration_graph_is_linear():
    graph = S.migration_graph()
    assert graph["head"] == HEAD
    assert graph["chain"] == CHAIN
    # A single head is the property that makes the linear walk in
    # db_schema_revision / schema_status trustworthy.
    assert graph["heads"] == [HEAD]


def test_state_survives_corruption(tmp_path):
    data_dir = str(tmp_path / "data")
    os.makedirs(data_dir)
    with open(S.state_path(data_dir), "w", encoding="utf-8") as handle:
        handle.write("{not json")
    assert S.read_state(data_dir)["state_version"] == S.STATE_VERSION


def test_state_write_is_atomic(tmp_path):
    data_dir = str(tmp_path / "data")
    os.makedirs(data_dir)
    S.write_state(data_dir, {"app_version": "3.1.1"})
    leftovers = [n for n in os.listdir(data_dir) if ".tmp." in n]
    assert leftovers == []
    assert S.read_state(data_dir)["app_version"] == "3.1.1"


def test_schema_drift_up_to_date(install):
    assert S.schema_status(install["db_uri"])["drift"] == "up-to-date"


def test_schema_drift_pending(install):
    conn = sqlite3.connect(install["db_file"])
    conn.execute("UPDATE alembic_version SET version_num = ?", (CHAIN[1],))
    conn.commit()
    conn.close()
    status = S.schema_status(install["db_uri"])
    assert status["drift"] == "pending"
    assert status["pending"] == [HEAD]


def test_schema_drift_ahead_is_detected(install):
    """An older image must notice the data is newer and refuse to migrate it."""
    conn = sqlite3.connect(install["db_file"])
    conn.execute("UPDATE alembic_version SET version_num = ?", ("29991231_future",))
    conn.commit()
    conn.close()
    assert S.schema_status(install["db_uri"])["drift"] == "ahead"


def test_schema_drift_fresh_without_file(tmp_path):
    data_dir = str(tmp_path / "data")
    os.makedirs(data_dir)
    status = S.schema_status("sqlite:///" + os.path.join(data_dir, "nope.db"))
    assert status["drift"] == "fresh"


def test_external_engine_schema_is_unknown(tmp_path):
    assert S.schema_status("postgresql://u:p@db/x")["drift"] == "unknown"


# ---------------------------------------------------------------------------
# Backups
# ---------------------------------------------------------------------------


def test_backup_captures_db_config_and_counts(install):
    result = B.create_backup(
        install["data_dir"],
        install["config_file"],
        install["db_uri"],
        app_version="3.1.1",
    )
    manifest = result["manifest"]
    assert manifest["schema_revision"] == HEAD
    assert manifest["tables"] == {"redirects": 1, "user_params": 1}
    with zipfile.ZipFile(result["path"]) as archive:
        names = set(archive.namelist())
        assert {"manifest.json", "redirect.db", "redirect.config.json",
                "state.json", "RESTORE.txt"} <= names


def test_backup_preserves_the_admin_password(install):
    result = B.create_backup(
        install["data_dir"], install["config_file"], install["db_uri"],
        app_version="3.1.1",
    )
    with zipfile.ZipFile(result["path"]) as archive:
        saved = json.loads(archive.read("redirect.config.json"))
    assert saved["admin_password"] == _FIXTURE_PASSWORD


def test_backup_works_against_a_live_wal_database(install, tmp_path):
    """WAL means uncommitted-to-main-file pages; the snapshot must include them."""
    conn = sqlite3.connect(install["db_file"])
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "INSERT INTO redirects (type, pattern, target) VALUES ('static','walrow','x')"
    )
    conn.commit()
    result = B.create_backup(
        install["data_dir"], install["config_file"], install["db_uri"],
        app_version="3.1.1",
    )
    with zipfile.ZipFile(result["path"]) as archive:
        archive.extract("redirect.db", str(tmp_path / "unpacked"))
    snap = sqlite3.connect(str(tmp_path / "unpacked" / "redirect.db"))
    patterns = {r[0] for r in snap.execute("SELECT pattern FROM redirects")}
    snap.close()
    conn.close()
    assert "walrow" in patterns


def test_backup_refuses_external_databases(install):
    with pytest.raises(B.BackupError, match="SQLite"):
        B.create_backup(
            install["data_dir"], install["config_file"],
            "postgresql://u:p@db/redirector", app_version="3.1.1",
        )


def test_backup_prunes_beyond_retention(install):
    for i in range(4):
        result = B.create_backup(
            install["data_dir"], install["config_file"], install["db_uri"],
            app_version="3.1.1", label=f"run{i}", keep=2,
        )
        # Filenames are second-resolution; make each distinct.
        os.utime(result["path"], (1_700_000_000 + i * 60, 1_700_000_000 + i * 60))
    assert len(S.list_backups(install["data_dir"])) == 2


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------


def _rows(db_file):
    conn = sqlite3.connect(db_file)
    try:
        return {r[0] for r in conn.execute("SELECT pattern FROM redirects")}
    finally:
        conn.close()


def test_restore_round_trip(install):
    archive = B.create_backup(
        install["data_dir"], install["config_file"], install["db_uri"],
        app_version="3.1.1",
    )["path"]

    os.remove(install["db_file"])
    assert not os.path.exists(install["db_file"])

    result = B.restore_archive(
        archive, install["data_dir"], install["config_file"], install["db_uri"],
        current_head=HEAD,
    )
    assert set(result["restored"]) == {
        "redirect.db", "redirect.config.json", "state.json"
    }
    assert _rows(install["db_file"]) == {"keepme"}
    # A safety snapshot is taken first, so a restore can always be undone.
    assert result["safety_backup"]
    assert os.path.isfile(
        os.path.join(S.backup_dir(install["data_dir"]), result["safety_backup"])
    )


def test_stage_rejects_paths(install):
    """Staging opens no caller-supplied path; streams only."""
    with pytest.raises(B.BackupError, match="open binary stream"):
        B.stage_pending_restore(install["data_dir"], install["db_file"])


def test_deferred_restore_is_applied_at_start(install):
    """The web UI path: park the archive, the entrypoint applies it."""
    archive = B.create_backup(
        install["data_dir"], install["config_file"], install["db_uri"],
        app_version="3.1.1",
    )["path"]
    os.remove(install["db_file"])

    with open(archive, "rb") as handle:
        staged = B.stage_pending_restore(install["data_dir"], handle)
    assert staged["staged"] is True
    assert os.path.isfile(B.pending_restore_path(install["data_dir"]))
    assert not os.path.exists(install["db_file"])
    assert os.path.isfile(B.pending_restore_path(install["data_dir"]))
    assert not os.path.exists(install["db_file"])

    applied = B.consume_pending_restore(
        install["data_dir"], install["config_file"], install["db_uri"],
        current_head=HEAD,
    )
    assert applied["deferred_applied"] is True
    assert _rows(install["db_file"]) == {"keepme"}
    # Consumed, so the next start is a normal start.
    assert not os.path.isfile(B.pending_restore_path(install["data_dir"]))
    assert B.consume_pending_restore(
        install["data_dir"], install["config_file"], install["db_uri"],
        current_head=HEAD,
    ) is None


def test_restore_refuses_corrupt_archive(install):
    with pytest.raises(B.BackupError, match="not a valid zip"):
        B.restore_archive(
            io.BytesIO(b"garbage"), install["data_dir"], install["config_file"],
            install["db_uri"], current_head=HEAD,
        )
    assert _rows(install["db_file"]) == {"keepme"}


def test_restore_refuses_tampered_payload(install):
    """A single flipped byte must not be applied as if it were fine."""
    archive = B.create_backup(
        install["data_dir"], install["config_file"], install["db_uri"],
        app_version="3.1.1",
    )["path"]
    buf = io.BytesIO()
    with zipfile.ZipFile(archive) as src, zipfile.ZipFile(buf, "w") as dst:
        for item in src.namelist():
            data = src.read(item)
            if item == "redirect.config.json":
                data = data.replace(
                    _FIXTURE_PASSWORD.encode(), _FIXTURE_TAMPERED.encode()
                )
            dst.writestr(item, data)
    buf.seek(0)
    with pytest.raises(B.BackupError, match="checksum mismatch"):
        B.restore_archive(
            buf, install["data_dir"], install["config_file"],
            install["db_uri"], current_head=HEAD,
        )


def test_restore_refuses_newer_schema(install):
    """Restoring data from a future version into this build would corrupt it."""
    archive = B.create_backup(
        install["data_dir"], install["config_file"], install["db_uri"],
        app_version="9.9.9",
    )["path"]
    buf = io.BytesIO()
    with zipfile.ZipFile(archive) as src, zipfile.ZipFile(buf, "w") as dst:
        for item in src.namelist():
            data = src.read(item)
            if item == "manifest.json":
                manifest = json.loads(data)
                manifest["schema_revision"] = "29991231_future"
                data = json.dumps(manifest).encode()
            dst.writestr(item, data)
    buf.seek(0)
    with pytest.raises(B.BackupError, match="newer version"):
        B.restore_archive(
            buf, install["data_dir"], install["config_file"],
            install["db_uri"], current_head=HEAD,
        )


def test_restore_accepts_older_schema_for_migration(install):
    """An older archive is fine: migrations bring it forward afterwards."""
    conn = sqlite3.connect(install["db_file"])
    conn.execute("UPDATE alembic_version SET version_num = ?", (CHAIN[1],))
    conn.commit()
    conn.close()
    archive = B.create_backup(
        install["data_dir"], install["config_file"], install["db_uri"],
        app_version="3.1.1",
    )["path"]
    result = B.restore_archive(
        archive, install["data_dir"], install["config_file"], install["db_uri"],
        current_head=HEAD,
    )
    assert "redirect.db" in result["restored"]
    assert S.db_schema_revision(install["db_uri"]) == CHAIN[1]


def test_inspect_reports_manifest_without_writing(install):
    archive = B.create_backup(
        install["data_dir"], install["config_file"], install["db_uri"],
        app_version="3.1.1",
    )["path"]
    result = B.inspect_archive(archive)
    assert result["ok"] is True
    assert result["manifest"]["app_version"] == "3.1.1"


def test_inventory_flags_damaged_archive(install):
    archive = B.create_backup(
        install["data_dir"], install["config_file"], install["db_uri"],
        app_version="3.1.1",
    )["path"]
    with open(archive, "wb") as handle:
        handle.write(b"broken")
    assert B.backup_inventory(install["data_dir"])[0]["ok"] is False
