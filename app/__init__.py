import os
import gevent.monkey
gevent.monkey.patch_all()

import logging
import secrets
from flask import Flask
from flask_migrate import Migrate

from .config import config  # App config instance
from model import db        # SQLAlchemy db instance
from .routes import register_blueprints
from .routes.version_routes import bp as system_info_bp
from .utils.utils import get_db_uri, get_port
from .utils.startup import app_startup_banner
from .CONSTANTS import __version__, get_semver

# Set up logger
logger = logging.getLogger(__name__)

def create_app():
    """Create and configure the Flask application."""
    
    # Initialize Flask app
    app = Flask(__name__)

    # Expose version in templates
    app.jinja_env.globals['version'] = get_semver()

    # Display a custom startup banner
    app_startup_banner(app)

    # Initialize Redis if enabled in config
    if config.redis_enabled:
        config.init_redis()

    # Set a secure random secret key for sessions
    app.secret_key = secrets.token_urlsafe(32)

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
        migrate = Migrate(app, db)
        logger.info("✅ Database initialized and migration support enabled.")
    except Exception as e:
        logger.exception("❌ Failed to initialize database.")

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

# For testing: expose app instance for pytest discovery
app = create_app()
