"""Tests for how the package initialises.

The app used to be built as a side effect of ``import app``. That made every
maintenance command a full application start: it connected to Redis (a 10s
timeout when no cache is running), ran the startup purge against the database,
and printed the admin-password banner. It also meant ``python -m
app.utils.backup`` loaded itself twice, once via the route tree.
"""

import os
import subprocess
import sys

import pytest


def test_importing_a_helper_module_does_not_build_the_app():
    # Read __dict__ directly: getattr() would trigger the lazy __getattr__ and
    # build the very app this test is asserting is absent.
    script = (
        "import sys, app.utils.backup; "
        "d = vars(sys.modules['app']); "
        "print('APP_BUILT' if d.get('app') is not None or d.get('_DEFAULT_APP') "
        "is not None else 'NO_APP')"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "NO_APP" in proc.stdout, proc.stdout


def test_maintenance_cli_does_not_touch_redis_or_the_database():
    """`paths` must be answerable with no cache running and no schema present."""
    proc = subprocess.run(
        [sys.executable, "-m", "app.utils.backup", "paths"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "data dir" in proc.stdout
    # The app's startup side effects would show up as these.
    assert "Unexpected error initializing Redis" not in proc.stderr
    assert "Failed to purge SSO cache" not in proc.stderr
    # Double-import of the module being run produced this warning.
    assert "found in sys.modules after import" not in proc.stderr


def test_default_app_is_still_available_for_wsgi():
    """`from app import app` must keep working - gunicorn loads wsgi:app."""
    from app import app as default_app

    assert default_app is not None
    assert default_app.name


def test_unknown_attribute_still_raises():
    import app

    with pytest.raises(AttributeError):
        app.definitely_not_a_real_attribute


def test_migrations_directory_is_absolute(tmp_path):
    """`flask db upgrade` must not depend on the working directory.

    flask_migrate defaults to the relative string "migrations", so from any
    other directory (systemd, launchd, Task Scheduler, a manual shell) the
    command died with "Path doesn't exist: migrations" - and worse, a stale
    `instance/redirect.db` could be migrated instead of the real one.
    """
    from app import create_app

    app = create_app()
    directory = app.extensions["migrate"].directory
    assert os.path.isabs(directory)
    assert os.path.isdir(directory)
    assert os.path.exists(os.path.join(directory, "env.py"))


def test_flask_app_is_pinned_for_the_migration_cli():
    """The entrypoint must not rely on Flask's CWD auto-detection."""
    entrypoint = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "entrypoint.sh",
    )
    with open(entrypoint, encoding="utf-8") as handle:
        content = handle.read()
    assert "FLASK_APP" in content
    assert "wsgi:app" in content
