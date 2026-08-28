from functools import wraps

from flask import session, request, jsonify, redirect, url_for

import logging
from urllib.parse import urlparse, urljoin

logger=logging.getLogger(__name__)

def is_safe_url(target: str) -> bool:
    """Validate next URL is safe (same host, http/https, no // bypass).
    Prevents open redirect via ?next=https://evil.com"""
    if not target or not isinstance(target, str):
        return False
    # Must be relative or same-host absolute
    # Block protocol-relative //evil.com and javascript:
    if target.startswith('//') or ':' in target.split('/')[0]:
        # Allow only http/https if absolute, but must be same host
        parsed = urlparse(target)
        if parsed.scheme and parsed.scheme not in ('http', 'https'):
            return False
    try:
        ref_url = urlparse(request.host_url)
        test_url = urlparse(urljoin(request.host_url, target))
        return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc
    except Exception:
        return False

def get_safe_next_url(default_endpoint='main.dashboard', fallback=None):
    """Return safe next URL from ?next= or fallback."""
    candidate = request.args.get('next') or request.form.get('next') or (fallback or '')
    if candidate and is_safe_url(candidate):
        return candidate
    try:
        return fallback or url_for(default_endpoint)
    except Exception:
        return fallback or '/'

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in'):
            logger.warning(f"Authentication required for {request.path}. User not logged in.")
            # If AJAX/JSON request, return JSON error
            if request.accept_mimetypes['application/json'] >= request.accept_mimetypes['text/html']:
                return jsonify({'success': False, 'error': 'Authentication required'}), 401
            # Otherwise, redirect to login page
            return redirect(url_for('main.admin_login', next=request.path))
        logger.debug(f"User authenticated for {request.path}.")
        return f(*args, **kwargs)

    return decorated_function
