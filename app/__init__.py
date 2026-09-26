import os

# Monkey-patch for gevent (production: gunicorn gevent workers).
# Guard so tests and local dev can run without gevent installed.
try:
    import gevent.monkey
    gevent.monkey.patch_all()
except ImportError:
    pass

import logging
import secrets
from flask import Flask
from flask_migrate import Migrate

from .config import config  # App config instance
from model import db        # SQLAlchemy db instance
from .CONSTANTS import __version__, get_semver

# Deliberately NOT imported here: the route tree and app.utils.utils. Pulling
# them in at package-import time meant `python -m app.utils.backup` had already
# loaded every route module (and therefore backup.py itself) before runpy
# executed it, producing a duplicate-module RuntimeWarning. create_app() imports
# them locally instead, so importing a helper module stays cheap.

# Set up logger
logger = logging.getLogger(__name__)


def _apply_sqlite_durability(app):
    """Tune SQLite so an unclean stop cannot corrupt or roll back the database.

    WAL turns a half-written transaction into a recoverable one and removes the
    window where a reader blocks a writer, which matters because gunicorn runs
    several gevent workers against one file. ``busy_timeout`` stops concurrent
    workers from failing instantly on a lock. Both are best-effort: a database
    on a network filesystem may reject WAL, and that must not stop the boot.
    """
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    @event.listens_for(Engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):  # noqa: ANN001
        if type(dbapi_connection).__module__.split(".")[0] != "sqlite3":
            return
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA busy_timeout=15000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
        except Exception as exc:  # pragma: no cover - depends on the filesystem
            app.logger.debug("SQLite pragmas not applied: %s", exc)
        finally:
            cursor.close()


def stamp_install_state(app):
    """Record what this install is and which schema revision its data is on."""
    from app.utils import state as state_mod

    try:
        db_uri = app.config.get("SQLALCHEMY_DATABASE_URI")
        status = state_mod.schema_status(db_uri)
        previous = state_mod.read_state(config.DATA_DIR)
        updates = {
            "app_version": get_semver(),
            "schema_revision": status["current"],
            "schema_head": status["head"],
            "data_dir": config.DATA_DIR,
            "in_docker": config.RUNNING_IN_DOCKER,
        }
        if not previous.get("installed_at"):
            updates["installed_at"] = state_mod.utc_now()
        if status["current"] != previous.get("schema_revision"):
            updates["schema_changed_at"] = state_mod.utc_now()
        state_mod.write_state(config.DATA_DIR, updates)
        if status["drift"] == "ahead":
            logger.warning(
                "⚠️ Database schema %s is NEWER than this build knows (%s). "
                "Do not run an older image against this data.",
                status["current"], status["head"],
            )
    except Exception as exc:
        app.logger.debug("Could not stamp install state: %s", exc)


def create_app():
    """Create and configure the Flask application."""
    from .routes import register_blueprints
    from .utils.utils import get_db_uri, get_port
    from .utils.startup import app_startup_banner

    # Initialize Flask app
    app = Flask(__name__)

    # Expose version in templates
    app.jinja_env.globals['version'] = get_semver()

    # Display a custom startup banner
    app_startup_banner(app)

    # Initialize Redis if enabled in config
    if config.redis_enabled:
        config.init_redis()

    # Session key. A random per-process key signed out every admin session on
    # each restart and, worse, gave each gunicorn worker a *different* key, so
    # MFA logins failed depending on which worker happened to answer. Persisted
    # in the data directory it survives upgrades, and survives losing the
    # container entirely.
    app.secret_key = config.get_value("session_secret") or secrets.token_urlsafe(48)

    # Get and validate the database URI
    db_uri = get_db_uri()
    if not db_uri:
        logger.error("❌ Database URI could not be determined. Exiting application.")
        raise RuntimeError("Invalid database URI")

    logger.info(f"🔌 Connecting to database using URI: {db_uri}")
    app.config["SQLALCHEMY_DATABASE_URI"] = db_uri
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False  # Recommended for performance

    # Initialize SQLAlchemy and apply migrations
    try:
        db.init_app(app)
        # Absolute migrations path: flask_migrate otherwise defaults to the
        # relative string "migrations", so `flask db upgrade` only worked while
        # the working directory happened to be the repo root. Any other CWD
        # (systemd, launchd, Task Scheduler, a manual shell) failed with
        # "Path doesn't exist: migrations".
        migrate = Migrate(app, db, directory=os.path.join(
            config.PROJECT_ROOT, "migrations"))
        logger.info("✅ Database initialized and migration support enabled.")
    except Exception as e:
        logger.exception("❌ Failed to initialize database.")

    _apply_sqlite_durability(app)
    stamp_install_state(app)

    # Register application routes
    register_blueprints(app)

    # Set the app port inside context and purge SSO caches
    with app.app_context():
        app.config['port'] = get_port()
        # Purge any stale SSO entries from cache (SSO never cached)
        try:
            from app.utils.utils import purge_sso_upstream_cache
            purged = purge_sso_upstream_cache()
            if purged:
                logger.info(f"🧹 Purged {purged} SSO cache entries on startup (SSO never cached)")
        except Exception as e:
            logger.warning(f"SSO cache purge on startup failed: {e}")

    # Security headers middleware
    @app.after_request
    def add_security_headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        response.headers['X-XSS-Protection'] = '1; mode=block'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        # For SSO redirects, ensure no caching
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get('Location', '')
            try:
                from app.utils.utils import is_sso_url
                if location and is_sso_url(location):
                    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
                    response.headers['Pragma'] = 'no-cache'
            except Exception:
                pass
        return response

    return app


# The default app instance used to be built here at import time, which meant that
# *any* import of *any* submodule - including `python -m app.utils.backup` -
# connected to Redis, ran the startup purge against the database, and printed the
# admin-password banner. A maintenance command should not need a working cache
# or write to the database before it does its job.
#
# PEP 562 module __getattr__ makes it lazy instead: `from app import app` still
# yields a real app for anything that wants one, but importing app.utils.* stays
# cheap. wsgi.py and app.py both call create_app() themselves, so gunicorn is
# unaffected.
_DEFAULT_APP = None


def __getattr__(name):
    global _DEFAULT_APP
    if name == "app":
        if _DEFAULT_APP is None:
            _DEFAULT_APP = create_app()
        return _DEFAULT_APP
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(list(globals()) + ["app"])
