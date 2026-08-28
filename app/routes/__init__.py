import logging
from datetime import datetime, timezone

from .error_routes import bp as error_bp
from .redirection_routes import bp as redirection_bp
from .routes import bp as route_bp
from .upstream_routes import bp as upstream_bp
from .version_routes import bp as system_info_bp
from .mfa_routes import bp as mfa_bp
from .. import CONSTANTS
from ..config import config

logger = logging.getLogger(__name__)

ALL_APP_BLUEPRINTS = [
    route_bp,
    system_info_bp,
    error_bp,
    redirection_bp,
    upstream_bp,
    mfa_bp

]

def register_blueprints(app):
    for bp in ALL_APP_BLUEPRINTS:
        app.register_blueprint(bp)
        logger.debug(f"Registered blueprint: {bp.name}")

    @app.context_processor
    def inject_now():
        from app.CONSTANTS import get_semver
        # Try to get version string (count from last tag)
        try:
            import subprocess
            # Always fetch tags first (works in Docker and local if .git is present)
            try:
                subprocess.check_output(['git', 'fetch', '--tags'], stderr=subprocess.DEVNULL)
            except Exception as fetch_exc:
                logger.debug(f"Could not fetch tags: {fetch_exc}")
            desc = subprocess.check_output(['git', 'describe', '--tags', '--long', '--match', 'v*'], encoding='utf-8').strip()
            # Example: v2.1.0-5-gabcdef
            import re
            m = re.match(r'v?(\d+\.\d+\.\d+)-(\d+)-g([0-9a-f]+)', desc)
            if m:
                base, commits, githash = m.groups()
                version = f"{base}+{commits}.g{githash}"
            else:
                version = desc
        except Exception as e:
            version = 'unknown'
            logger.debug(f"Could not determine version from git: {e}")

        version = get_semver()
        redis_connected = bool(config.redis_enabled)
        redis_connected_location = f"{config.redis_host}:{config.redis_port}"


        return {'now': lambda: datetime.now(timezone.utc), 'version': version, 'redis_connected': redis_connected,
                'constants': CONSTANTS,'redis_connected_location': redis_connected_location}