from flask import Blueprint, render_template, request, session, jsonify
import subprocess
import socket
import platform
import sys
import os
import re
from app.utils.utils import  get_port
import requests
from app.CONSTANTS import __version__, get_semver
import logging
import time
from flask import current_app

bp = Blueprint('version', __name__)

GITHUB_REPO = "authoritydmc/redirector"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

logger = logging.getLogger(__name__)

def get_accessible_urls(port):
    urls = []
    # Localhost
    urls.append(f"http://localhost:{port}/")
    urls.append(f"http://127.0.0.1:{port}/")
    # All local IPs
    try:
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
        if local_ip not in ('127.0.0.1', 'localhost'):
            urls.append(f"http://{local_ip}:{port}/")
        # All interfaces
        for ip in socket.gethostbyname_ex(hostname)[2]:
            if ip not in ('127.0.0.1', local_ip) and not ip.startswith('169.'):
                urls.append(f"http://{ip}:{port}/")
    except Exception:
        pass
    # Try to get all IPv4 addresses from all interfaces
    try:
        import psutil
        for iface, addrs in psutil.net_if_addrs().items():
            for addr in addrs:
                if addr.family == socket.AF_INET and addr.address not in ('127.0.0.1', local_ip):
                    urls.append(f"http://{addr.address}:{port}/")
    except Exception:
        pass
    # Remove duplicates
    return sorted(set(urls))

@bp.route('/system-info', methods=['GET', 'POST'])
def system_info_page():
    from app.CONSTANTS import get_semver
    from datetime import datetime
    from app.utils.utils import get_config, set_config
    import json
    config_path = 'data/redirect.config.json'
    config_update_success = None
    config_update_error = None
    allowed_keys = {'upstream_cache.enabled', 'log_level', 'port', 'auto_redirect_delay', 'database'}
    if request.method == 'POST' and session.get('admin_logged_in'):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
            for k, v in request.form.items():
                if k == 'config_version':
                    continue  # Prevent editing config_version
                if k not in allowed_keys:
                    continue
                # Handle nested key for upstream_cache.enabled
                if k == 'upstream_cache.enabled':
                    if 'upstream_cache' not in config_data:
                        config_data['upstream_cache'] = {}
                    config_data['upstream_cache']['enabled'] = v.lower() == 'true'
                elif k == 'auto_redirect_delay':
                    # Store as int seconds
                    try:
                        config_data[k] = int(float(v))
                    except Exception:
                        config_data[k] = v
                elif k == 'port':
                    try:
                        config_data[k] = int(v)
                    except Exception:
                        config_data[k] = v
                elif k == 'database':
                    # Only allow folder, append redirects.db
                    import os
                    folder = v
                    if folder.endswith('redirects.db'):
                        folder = os.path.dirname(folder)
                    db_path = os.path.join(folder, 'redirects.db')
                    config_data[k] = db_path
                else:
                    config_data[k] = v
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(config_data, f, indent=2)
            config_update_success = 'Configuration updated successfully.'
        except Exception:
            logger.exception("Config update failed")
            config_update_error = 'Failed to update config.'
    try:
        commit_count = subprocess.check_output(['git', 'rev-list', '--count', 'HEAD'], encoding='utf-8').strip()
        commit_hash = subprocess.check_output(['git', 'rev-parse', '--short', 'HEAD'], encoding='utf-8').strip()
        commit_date = subprocess.check_output(['git', 'log', '-1', '--format=%cd', '--date=short'], encoding='utf-8').strip()
        semver = get_semver()
    except Exception:
        commit_count = commit_hash = commit_date = semver = 'unknown'
    port = get_port()
    urls = get_accessible_urls(port)
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config_data = json.load(f)
        # Remove sensitive fields for read-only mode
        if not (session.get('admin_logged_in') and request.method == 'POST'):
            if 'database' in config_data:
                config_data['database'] = '***hidden***'
        config_data = {k: v for k, v in config_data.items() if 'password' not in k.lower() and k != 'upstreams'}
    except Exception:
        config_data = {}
    return render_template('system_info.html', version=semver, commit_count=commit_count, commit_hash=commit_hash, commit_date=commit_date, urls=urls, config_data=config_data, config_update_success=config_update_success, config_update_error=config_update_error)

# Health & readiness endpoints for K8s/Docker probes
@bp.route('/health')
def health_check():
    """Liveness probe - checks DB and Redis connectivity. Does not expose exception details."""
    from app.config import config
    from model import db
    status = {"status": "ok", "version": get_semver(), "checks": {}}
    # DB check
    try:
        with current_app.app_context():
            db.session.execute(db.text("SELECT 1"))
        status["checks"]["database"] = "up"
    except Exception:
        logger.exception("Health check DB failed")
        status["checks"]["database"] = "down"
        status["status"] = "degraded"
    # Redis check
    if config.redis_enabled:
        try:
            if config.redis_client:
                config.redis_client.ping()
                status["checks"]["redis"] = "up"
            else:
                status["checks"]["redis"] = "disabled/disconnected"
        except Exception:
            logger.exception("Health check Redis failed")
            status["checks"]["redis"] = "down"
            status["status"] = "degraded"
    else:
        status["checks"]["redis"] = "disabled"
    code = 200 if status["status"] == "ok" else 503
    return jsonify(status), code

@bp.route('/ready')
def readiness_check():
    """Readiness probe - stricter check."""
    return health_check()

@bp.route('/api/metrics')
def api_metrics():
    """Prometheus-like metrics (lightweight)."""
    from model.redirect import Redirect
    from model.upstream_cache import UpstreamCache
    try:
        total = Redirect.query.count()
        upstream_cached = UpstreamCache.query.count()
        from app.config import config
        redis_status = 1 if (config.redis_enabled and config.redis_client and config.redis_client.ping()) else 0
        lines = [
            "# HELP redirector_shortcuts_total Total shortcuts",
            "# TYPE redirector_shortcuts_total gauge",
            f"redirector_shortcuts_total {total}",
            "# HELP redirector_upstream_cache_total Total upstream cached entries",
            "# TYPE redirector_upstream_cache_total gauge",
            f"redirector_upstream_cache_total {upstream_cached}",
            "# HELP redirector_redis_up Redis status (1=up,0=down/disabled)",
            "# TYPE redirector_redis_up gauge",
            f"redirector_redis_up {redis_status}",
            "# HELP redirector_version_info Version info",
            "# TYPE redirector_version_info gauge",
            f'redirector_version_info{{version="{get_semver()}"}} 1',
        ]
        return "\n".join(lines) + "\n", 200, {"Content-Type": "text/plain; version=0.0.4"}
    except Exception:
        logger.exception("Metrics collection failed")
        return "# error collecting metrics\n", 500, {"Content-Type": "text/plain"}

# Changelog page + API (like Tax_Scripts)
@bp.route('/changelog')
def changelog_page():
    """Render changelog HTML page."""
    return render_template('changelog.html', version=get_semver())

@bp.route('/api/changelog')
def api_changelog():
    """Return raw CHANGELOG.md content for frontend parsing."""
    import os
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    changelog_path = os.path.join(root, "CHANGELOG.md")
    if os.path.isfile(changelog_path):
        try:
            with open(changelog_path, "r", encoding="utf-8") as f:
                content = f.read()
            return jsonify({"changelog": content})
        except Exception:
            logger.exception("Failed to read changelog")
            return jsonify({"changelog": "", "error": "Failed to read changelog"}), 500
    return jsonify({"changelog": "Changelog not found."}), 404

# Simple in-memory cache for version check
_version_check_cache = {
    'timestamp': 0,
    'result': None,
    'error': False
}

@bp.route('/api/latest-version')
def api_latest_version():
    global _version_check_cache
    logger.info("Checking for latest version from GitHub...")
    now = time.time()
    cache_valid = (
        _version_check_cache['result'] is not None and
        (now - _version_check_cache['timestamp'] < 86400) and  # 24h cache - once per day
        not _version_check_cache['error']
    )
    if cache_valid:
        logger.debug("Returning cached version check result.")
        return _version_check_cache['result']
    try:
        resp = requests.get(GITHUB_API_URL, timeout=3)
        if resp.status_code == 200:
            data = resp.json()
            latest = data.get('tag_name') or data.get('name')
            logger.info(f"Version check success: current={get_semver()}, latest={latest}")
            result = {'success': True, 'latest': latest, 'current': get_semver()}
            _version_check_cache = {
                'timestamp': now,
                'result': result,
                'error': False
            }
            return result
        elif resp.status_code == 404:
            logger.warning(f"GitHub API 404: No releases found for {GITHUB_REPO}. You must create a release on GitHub for version check to work. Response: {resp.text}")
            result = {'success': False, 'error': 'No releases found on GitHub. Please create a release for version checking.', 'current': get_semver()}
            _version_check_cache = {
                'timestamp': now,
                'result': result,
                'error': True
            }
            return result
        elif resp.status_code == 403 and 'rate limit' in resp.text.lower():
            logger.warning(f"GitHub API rate limit exceeded: {resp.text}")
            result = {'success': False, 'error': 'GitHub API rate limit exceeded. Please try again later or set a GitHub token for higher limits.', 'current': get_semver()}
            _version_check_cache = {
                'timestamp': now,
                'result': result,
                'error': True
            }
            return result
        else:
            logger.warning(f"GitHub API error: status_code={resp.status_code}, text={resp.text}")
            result = {'success': False, 'error': f'GitHub API error: {resp.status_code}', 'current': get_semver()}
            _version_check_cache = {
                'timestamp': now,
                'result': result,
                'error': True
            }
            return result
    except Exception:
        logger.exception("Error checking latest version")
        result = {'success': False, 'error': 'Failed to check version', 'current': get_semver()}
        _version_check_cache = {
            'timestamp': now,
            'result': result,
            'error': True
        }
        return result
