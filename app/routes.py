from flask import Blueprint, render_template, request, redirect, stream_with_context, url_for, session

from app import CONSTANTS
from .utils import get_db, get_admin_password, get_auto_redirect_delay, get_delete_requires_password, increment_access_count, init_upstream_check_log, log_upstream_check, get_upstream_logs
from .utils import get_shortcut, set_shortcut, init_redis_from_config
from .utils import is_upstream_cache_enabled, get_cached_upstream_result, cache_upstream_result, clear_upstream_cache
from functools import wraps
from datetime import datetime
import json
import os
import requests
from flask import Response
import time
from flask import send_file
import io
from flask import session as flask_session
from flask import jsonify, make_response

bp = Blueprint('main', __name__)

# At the top of the file, after imports, initialize Redis
init_redis_from_config()

@bp.context_processor
def inject_now():
    from datetime import datetime
    # Try to get version string (same logic as version page)
    try:
        import subprocess
        commit_count = subprocess.check_output(['git', 'rev-list', '--count', 'HEAD'], encoding='utf-8').strip()
        commit_hash = subprocess.check_output(['git', 'rev-parse', '--short', 'HEAD'], encoding='utf-8').strip()
        version = f"v1.{commit_count}.{commit_hash}"
    except Exception:
        version = 'unknown'
    # Add redis_connected context
    try:
        from .utils import _redis_enabled
        redis_connected = bool(_redis_enabled)
    except Exception:
        redis_connected = False
    return {'now': datetime.now, 'version': version, 'redis_connected': redis_connected,'constants': CONSTANTS}

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'redirect.config.json')

def load_config():
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_config(cfg):
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=2)

def get_upstreams():
    cfg = load_config()
    return cfg.get('upstreams', [])

def set_upstreams(upstreams):
    cfg = load_config()
    cfg['upstreams'] = upstreams
    save_config(cfg)

# --- Simple Auth Decorator ---
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in'):
            # If AJAX/JSON request, return JSON error
            if request.accept_mimetypes['application/json'] >= request.accept_mimetypes['text/html']:
                return jsonify({'success': False, 'error': 'Authentication required'}), 401
            # Otherwise, redirect to login page
            return redirect(url_for('main.admin_login', next=request.path))
        return f(*args, **kwargs)
    return decorated_function

# GET/POST: Admin login page and handler. Triggered when user visits /admin-login or submits login form.
@bp.route('/admin-login', methods=['GET', 'POST'])
def admin_login():
    error = None
    if request.method == 'POST':
        admin_pwd = get_admin_password()
        if request.form.get('password') == admin_pwd:
            session['admin_logged_in'] = True
            next_url = request.args.get('next') or url_for('main.dashboard')
            return redirect(next_url)
        else:
            error = 'Invalid password.'
    return render_template('admin_login.html', error=error, now=datetime.utcnow)

# GET: Logout endpoint. Triggered when user visits /logout.
@bp.route('/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('main.dashboard'))

# GET: Dashboard page. Triggered when user visits the root URL '/'.
@bp.route('/', methods=['GET'])
def dashboard():
    db = get_db()
    cursor = db.execute('SELECT pattern, type, target, access_count, created_at, updated_at FROM redirects ORDER BY updated_at DESC LIMIT 5')
    shortcuts = [
        dict(
            pattern=row[0],
            type=row[1],
            target=row[2],
            access_count=row[3] if row[3] is not None else 0,
            created_at=row[4],
            updated_at=row[5]
        ) for row in cursor.fetchall()
    ]
    return render_template('dashboard.html', shortcuts=shortcuts, now=datetime.utcnow)


# GET/POST: Delete shortcut. Triggered when user visits /delete/<subpath> or submits delete confirmation.
@bp.route('/delete/<path:subpath>', methods=['GET', 'POST'])
def dashboard_delete(subpath):
    db = get_db()
    if get_delete_requires_password():
        if request.method == 'POST':
            admin_pwd = get_admin_password()
            if request.form.get('password') == admin_pwd:
                db.execute('DELETE FROM redirects WHERE pattern=?', (subpath,))
                db.commit()
                return redirect(url_for('main.dashboard'))
            else:
                error = 'Invalid password.'
                return render_template('delete_confirm.html', error=error, now=datetime.utcnow)
        else:
            return render_template('delete_confirm_noerror.html', now=datetime.utcnow)
    else:
        db.execute('DELETE FROM redirects WHERE pattern=?', (subpath,))
        db.commit()
        return redirect(url_for('main.dashboard'))

# GET/POST: Edit or create shortcut. Triggered when user visits /edit/<subpath> or submits edit form.
@bp.route('/edit/<path:subpath>', methods=['GET', 'POST'])
def edit_redirect(subpath):
    # --- Upstream check done during GET ---
    if request.method == 'GET':
        shortcut,source_data,resp_time = get_shortcut(subpath)
        if not shortcut:
            return render_template('create_shortcut.html', pattern=subpath, now=datetime.utcnow)
        return render_template('edit_shortcut.html', pattern=subpath, type=shortcut['type'], target=shortcut['target'], now=datetime.utcnow)

    # POST: Form was submitted to update/create shortcut
    elif request.method == 'POST':
        type_ = request.form['type']
        target = request.form['target']
        now = datetime.utcnow().isoformat(sep=' ', timespec='seconds')
        ip = request.remote_addr or ''
        cursor = get_db().execute('SELECT 1 FROM redirects WHERE pattern=?', (subpath,))
        exists = cursor.fetchone()
        if exists:
            set_shortcut(subpath, type_, target, updated_at=now)
        else:
            set_shortcut(subpath, type_, target, created_at=now, updated_at=now)
        return render_template('success_create.html', pattern=subpath, target=target, now=datetime.utcnow)

    db = get_db()
    from .utils import get_access_count, get_created_updated
    if request.method == 'POST':
        type_ = request.form['type']
        target = request.form['target']
        now = datetime.utcnow().isoformat(sep=' ', timespec='seconds')
        ip = request.remote_addr or ''
        cursor = db.execute('SELECT 1 FROM redirects WHERE pattern=?', (subpath,))
        exists = cursor.fetchone()
        if exists:
            db.execute('UPDATE redirects SET type=?, target=?, updated_at=?, updated_ip=? WHERE pattern=?', (type_, target, now, ip, subpath))
            db.commit()
            return render_template('success_create.html', pattern=subpath, target=target, now=datetime.utcnow)
        else:
            db.execute('INSERT INTO redirects (type, pattern, target, created_at, updated_at, created_ip, updated_ip) VALUES (?, ?, ?, ?, ?, ?, ?)', (type_, subpath, target, now, now, ip, ip))
            db.commit()
            return render_template('success_create.html', pattern=subpath, target=target, now=datetime.utcnow)
    else:
        cursor = db.execute('SELECT type, target FROM redirects WHERE pattern=?', (subpath,))
        row = cursor.fetchone()
        if not row:
            return render_template('create_shortcut.html', pattern=subpath, now=datetime.utcnow)
        return render_template('edit_shortcut.html', pattern=subpath, type=row[0], target=row[1], now=datetime.utcnow)

# GET: Handle redirect for static and dynamic shortcuts. Triggered for any /<subpath> not matching other routes.
@bp.route('/<path:subpath>', methods=['GET'])
def handle_redirect(subpath):
    shortcut,data_source,resp_time = get_shortcut(subpath)
    if (data_source== CONSTANTS.data_source_redirect or data_source == CONSTANTS.data_source_redis) and shortcut['type'] == 'static':
        print(f"Redirecting static shortcut: {subpath} -> {shortcut['target']}")
        increment_access_count(subpath)
        if get_auto_redirect_delay() > 0:
            return render_template('redirect.html', target=shortcut['target'], delay=get_auto_redirect_delay(), now=datetime.utcnow,source=data_source,response_time=resp_time)
        return redirect(shortcut['target'], code=302)
    # Check upstream cache before running upstream checks
    if data_source == CONSTANTS.data_source_upstream and shortcut.get('resolved_url'):
        print(f"Redirecting upstream shortcut: {subpath} -> {shortcut['resolved_url']}")
            # Use the same redirect logic as for local hits
        if get_auto_redirect_delay() > 0:
            return render_template('redirect.html', target=shortcut['resolved_url'], delay=get_auto_redirect_delay(), now=datetime.utcnow,source=data_source,response_time=resp_time)
        return redirect(shortcut['resolved_url'], code=302)
    
    print(f"Handling redirect for subpath: {subpath}, data source: {data_source}, shortcut: {shortcut}")
    # If no static shortcut found, check for dynamic patterns
    # Check if subpath matches a dynamic pattern but is missing the variable
    db = get_db()
    cursor = db.execute('SELECT pattern, target FROM redirects WHERE type = ?', ('dynamic',))
    for pattern, target in cursor.fetchall():
        import re as _re
        match = _re.search(r'\{(\w+)\}', target)
        var_name = match.group(1) if match else 'name'
        if subpath == pattern:
            example_var = 'yourvalue'
            example_target = target.replace(f'{{{var_name}}}', example_var)
            return render_template('dynamic_shortcut_usage.html', pattern=pattern, var_name=var_name, example_target=example_target, now=datetime.utcnow)
        if subpath.startswith(pattern + "/"):
            variable = subpath[len(pattern)+1:]
            dest_url = _re.sub(r"\{\w+\}", variable, target)
            increment_access_count(pattern)
            if get_auto_redirect_delay() > 0:
                return render_template('redirect.html', target=dest_url, delay=get_auto_redirect_delay(), now=datetime.utcnow,source="redirect_table")
            return redirect(dest_url, code=302)
    # Only check upstreams if any are configured
    from .routes import get_upstreams
    if get_upstreams():
        first_segment = subpath.split('/')[0]
        return redirect(url_for('main.check_upstreams_ui', pattern=first_segment), code=302)
    # If no upstreams, redirect to create shortcut page
    return redirect(url_for('main.edit_redirect', subpath=subpath))

# GET: Tutorial/help page. Triggered when user visits /tutorial.
@bp.route('/tutorial', methods=['GET'])
def tutorial():
    return render_template('tutorial.html', now=datetime.utcnow)

# --- Upstreams Config API ---
@bp.route('/admin/upstreams', methods=['GET', 'POST'])
def admin_upstreams():
    error = None
    upstreams = get_upstreams()
    if request.method == 'POST':
        # Handle delete
        if 'delete' in request.form:
            idx = int(request.form['delete'])
            if 0 <= idx < len(upstreams):
                del upstreams[idx]
                set_upstreams(upstreams)
                return redirect(url_for('main.admin_upstreams'))
        # Handle save (add/edit)
        else:
            # Rebuild upstreams from form
            new_upstreams = []
            i = 0
            while True:
                name = request.form.get(f'name_{i}')
                base_url = request.form.get(f'base_url_{i}')
                fail_url = request.form.get(f'fail_url_{i}')
                fail_status_code = request.form.get(f'fail_status_code_{i}')
                if name is None and base_url is None and fail_url is None and fail_status_code is None:
                    break
                if name or base_url or fail_url:
                    try:
                        fail_status_code = int(fail_status_code) if fail_status_code else None
                    except Exception:
                        fail_status_code = None
                    new_upstreams.append({
                        'name': name or '',
                        'base_url': base_url or '',
                        'fail_url': fail_url or '',
                        'fail_status_code': fail_status_code
                    })
                i += 1
            set_upstreams(new_upstreams)
            upstreams = new_upstreams
    return render_template('admin_upstreams.html', upstreams=upstreams, error=error)


@bp.route('/check-upstreams-ui/<pattern>')
def check_upstreams_ui(pattern):
    from .utils import get_auto_redirect_delay
    delay = get_auto_redirect_delay()
    print(f"Check upstreams UI for pattern: {pattern}, delay: {delay}")
    return render_template('check_upstreams_stream.html', pattern=pattern, delay=delay)

@bp.route('/stream/check-upstreams/<pattern>')
def stream_check_upstreams(pattern):
    from .utils import cache_upstream_result, is_upstream_cache_enabled
    init_upstream_check_log()
    @stream_with_context
    def event_stream():
        found = False
        redirect_url = None

        # Helper to send log messages with timestamp
        def send_log(message, extra_data=None):
            timestamp = datetime.utcnow().strftime("%H:%M:%S")
            data = {'log': f"[{timestamp}] {message}"}
            if extra_data:
                data.update(extra_data)
            yield f"data: {json.dumps(data)}\n\n"

        yield from send_log(f"🔍 Starting upstream check for pattern: `{pattern}`")

        for up in get_upstreams():
            # Check required fields
            required_fields = ['name', 'base_url', 'fail_url']
            missing_fields = [field for field in required_fields if not up.get(field)]
            if missing_fields:
                yield from send_log(f"⚠️ Warning: Upstream missing required fields: {', '.join(missing_fields)}. Upstream config: {json.dumps(up)}")
            up_name = up.get('name', '[unnamed]')
            base_url = up.get('base_url', '').rstrip('/')
            fail_url = up.get('fail_url', '')
            if 'fail_url' not in up:
                yield from send_log(f"⚠️ Warning: Upstream '{up_name}' missing 'fail_url' key. Treating as empty string.")
            fail_url = fail_url.rstrip('/')
            fail_status_code = str(up.get('fail_status_code')) if up.get('fail_status_code') else None

            check_url = f"{base_url}/{pattern}"
            yield from send_log(f"🌐 Checking upstream: {up_name}")
            yield from send_log(f"Constructed URL: {check_url}")
            yield from send_log(f"Fail criteria → URL: {fail_url}, Status: {fail_status_code or 'Not specified'}")

            tried_at = datetime.utcnow().isoformat(sep=' ', timespec='seconds')
            try:
                verify_ssl = up.get('verify_ssl',False)
                resp = requests.get(check_url, allow_redirects=True, timeout=3,verify=verify_ssl)
                actual_url = resp.url.rstrip('/')
                status_code = str(resp.status_code)

                yield from send_log(f"➡️ Response received from {check_url} → {actual_url} (status {status_code})")

                # Check if we landed on the "fail" URL/status
                fail_url_match = actual_url.startswith(fail_url) if fail_url else False
                fail_status_match = (fail_status_code is not None and status_code == fail_status_code)

                if not fail_url_match or (fail_status_code and not fail_status_match):
                    found = True
                    redirect_url = actual_url
                    log_upstream_check(
                        pattern, up_name, check_url, 'success',
                        f"actual_url={actual_url}, status_code={status_code}", tried_at
                    )
                    # Cache the result if enabled
                    if is_upstream_cache_enabled():
                        cache_upstream_result(pattern, up_name, actual_url, tried_at)
                    yield from send_log(
                        f"✅ Shortcut found in {up_name} (redirected to {actual_url}, status {status_code})",
                        {'found': True, 'redirect_url': redirect_url}
                    )
                    break
                else:
                    log_upstream_check(
                        pattern, up_name, check_url, 'fail',
                        f"actual_url={actual_url}, status_code={status_code}", tried_at
                    )
                    yield from send_log(f"❌ Shortcut not found in {up_name} — matched fail criteria.")

            except Exception as e:
                log_upstream_check(
                    pattern, up_name, check_url, 'exception', str(e), tried_at
                )
                yield from send_log(f"⚠️ Error checking {up_name}: {str(e)}")

            yield from send_log(f"--- Finished check for {up_name} ---")
            time.sleep(0.5)

        if not found:
            yield from send_log("🔚 No upstream found containing the shortcut.")
            yield f"data: {json.dumps({'done': True})}\n\n"

    return Response(event_stream(), mimetype='text/event-stream')

@bp.route('/admin/upstream-logs')
def admin_upstream_logs():
    if not session.get('admin_logged_in'):
        # Try both possible endpoint names for admin_login
        try:
            return redirect(url_for('admin_login', next=request.path))
        except Exception:
            return redirect(url_for('main.admin_login', next=request.path))
    logs = get_upstream_logs()
    # Build cache_status_map: (pattern, upstream) -> {checked_at}
    from .utils import list_upstream_cache
    cache_status_map = {}
    # For all upstreams, get their cache entries
    for up in get_upstreams():
        cached = list_upstream_cache(up.get('name'))
        for entry in cached:
            cache_status_map[(entry['pattern'], up.get('name'))] = {'checked_at': entry['checked_at']}
    return render_template('admin_upstream_logs.html', logs=logs, cache_status_map=cache_status_map)

@bp.route('/admin/export-redirects')
@login_required
def admin_export_redirects():
    db = get_db()
    cursor = db.execute('PRAGMA table_info(redirects)')
    columns = [row[1] for row in cursor.fetchall()]
    cursor = db.execute(f'SELECT {", ".join(columns)} FROM redirects')
    redirects = [dict(zip(columns, row)) for row in cursor.fetchall()]
    buf = io.BytesIO(json.dumps(redirects, indent=2).encode('utf-8'))
    buf.seek(0)
    return send_file(buf, mimetype='application/json', as_attachment=True, download_name='redirects.json')

@bp.route('/admin/import-redirects', methods=['GET', 'POST'])
@login_required
def admin_import_redirects():
    error = None
    success = None
    if request.method == 'POST':
        file = request.files.get('file')
        if file and file.filename.endswith('.json'):
            try:
                data = json.load(file)
                if not isinstance(data, list):
                    raise ValueError('JSON must be a list of redirect objects.')
                db = get_db()
                cursor = db.execute('PRAGMA table_info(redirects)')
                columns = [row[1] for row in cursor.fetchall()]
                db.execute('DELETE FROM redirects')
                for entry in data:
                    values = [entry.get(col) for col in columns]
                    placeholders = ','.join(['?'] * len(columns))
                    db.execute(f'INSERT INTO redirects ({", ".join(columns)}) VALUES ({placeholders})', values)
                db.commit()
                success = 'Redirect data imported successfully.'
            except Exception as e:
                error = f'Import failed: {e}'
        else:
            error = 'Please upload a valid .json file.'
    return render_template('admin_import_export.html', error=error, success=success, session=flask_session)

@bp.route('/api/check-shortcut-exists/<pattern>')
def api_check_shortcut_exists(pattern):
    db = get_db()
    cursor = db.execute('SELECT 1 FROM redirects WHERE pattern = ?', (pattern,))
    exists = cursor.fetchone() is not None
    return jsonify({'exists': exists})

@bp.route('/edit/', methods=['GET', 'POST'])
def edit_redirect_blank():
    # Always show the create shortcut page with an empty pattern
    from datetime import datetime
    if request.method == 'POST':
        # Handle form submission for new shortcut
        pattern = request.form.get('pattern', '').strip()
        type_ = request.form.get('type', 'static')
        target = request.form.get('target', '').strip()
        now = datetime.utcnow().isoformat(sep=' ', timespec='seconds')
        ip = request.remote_addr or ''
        db = get_db()
        # Check if pattern is empty or already exists
        if not pattern:
            return render_template('create_shortcut.html', pattern='', error='Shortcut pattern cannot be empty.', now=datetime.utcnow)
        cursor = db.execute('SELECT 1 FROM redirects WHERE pattern=?', (pattern,))
        if cursor.fetchone():
            return render_template('create_shortcut.html', pattern=pattern, error='A shortcut with this pattern already exists.', now=datetime.utcnow)
        db.execute('''
            INSERT INTO redirects (type, pattern, target, created_at, updated_at, created_ip, updated_ip)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (type_, pattern, target, now, now, ip, ip))
        db.commit()
        return render_template('success_create.html', pattern=pattern, target=target, now=datetime.utcnow)
    # GET: show blank create page
    return render_template('create_shortcut.html', pattern='', now=datetime.utcnow)

# GET: Instructions page for enabling r/ shortcuts
@bp.route('/enable-r-instructions', methods=['GET'])
def enable_r_instructions():
    return render_template('enable_r_instructions.html', now=datetime.utcnow)

# --- Upstream Cache Management ---
@bp.route('/admin/upstream-cache/<upstream>')
@login_required
def admin_upstream_cache(upstream):
    from .utils import list_upstream_cache
    cached = list_upstream_cache(upstream)
    return render_template('admin_upstream_cache.html', upstream=upstream, cached=cached)

# Explicitly allow both GET and POST for resync endpoint, and add a print to confirm route is hit
@bp.route('/admin/upstream-cache/resync/<upstream>/<pattern>', methods=['GET', 'POST'])
@login_required
def admin_upstream_cache_resync(upstream, pattern):
    try:
        print(f"[DEBUG] admin_upstream_cache_resync called for upstream={upstream}, pattern={pattern}")
        from .utils import cache_upstream_result, clear_upstream_cache
        import requests
        from datetime import datetime
        import sys
        up = next((u for u in get_upstreams() if u.get('name') == upstream), None)
        print(f"[RESYNC] Upstream: {upstream}, Pattern: {pattern}", file=sys.stderr)
        if not up:
            print(f"[RESYNC] Upstream not found: {upstream}", file=sys.stderr)
            return jsonify({'success': False, 'error': 'Upstream not found'}), 404
        base_url = up.get('base_url', '').rstrip('/')
        check_url = f"{base_url}/{pattern}"
        print(f"[RESYNC] Check URL: {check_url}", file=sys.stderr)
        try:
            verify_ssl = up.get('verify_ssl', False)
            resp = requests.get(check_url, allow_redirects=True, timeout=3, verify=verify_ssl)
            actual_url = resp.url.rstrip('/')
            status_code = str(resp.status_code)
            print(f"[RESYNC] Response: actual_url={actual_url}, status_code={status_code}", file=sys.stderr)
            fail_url = up.get('fail_url', '').rstrip('/')
            fail_status_code = str(up.get('fail_status_code')) if up.get('fail_status_code') else None
            fail_url_match = actual_url.startswith(fail_url) if fail_url else False
            fail_status_match = (fail_status_code is not None and status_code == fail_status_code)
            tried_at = datetime.utcnow().isoformat(sep=' ', timespec='seconds')
            print(f"[RESYNC] fail_url_match={fail_url_match}, fail_status_match={fail_status_match}", file=sys.stderr)
            if not fail_url_match or (fail_status_code and not fail_status_match):
                cache_upstream_result(pattern, upstream, actual_url, tried_at)
                print(f"[RESYNC] Cached result for {pattern} in {upstream}", file=sys.stderr)
                return jsonify({'success': True, 'resolved_url': actual_url, 'checked_at': tried_at})
            else:
                clear_upstream_cache(pattern)
                print(f"[RESYNC] Fail criteria matched. Cleared cache for {pattern}", file=sys.stderr)
                return jsonify({'success': False, 'error': 'Pattern not found in upstream (fail criteria matched).', 'checked_at': tried_at})
        except Exception as e:
            print(f"[RESYNC] Exception: {e}", file=sys.stderr)
            return jsonify({'success': False, 'error': str(e)})
    except Exception as e:
        # Catch any unexpected error and always return JSON
        print(f"[RESYNC] Unexpected Exception: {e}", file=sys.stderr)
        return jsonify({'success': False, 'error': 'Unexpected server error', 'details': str(e)}), 500

@bp.route('/admin/upstream-cache/purge/<upstream>', methods=['POST'])
@login_required
def admin_upstream_cache_purge(upstream):
    try:
        from .utils import list_upstream_cache, clear_upstream_cache
        cached = list_upstream_cache(upstream)
        for entry in cached:
            clear_upstream_cache(entry['pattern'])
        return jsonify({'success': True, 'purged': len(cached)})
    except Exception as e:
        import sys
        print(f"[PURGE] Exception: {e}", file=sys.stderr)
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/admin/upstream-cache/resync-all/<upstream>', methods=['POST'])
@login_required
def admin_upstream_cache_resync_all(upstream):
    try:
        from .utils import list_upstream_cache
        import requests
        from datetime import datetime
        up = next((u for u in get_upstreams() if u.get('name') == upstream), None)
        if not up:
            return jsonify({'success': False, 'error': 'Upstream not found'}), 404
        base_url = up.get('base_url', '').rstrip('/')
        fail_url = up.get('fail_url', '').rstrip('/')
        fail_status_code = str(up.get('fail_status_code')) if up.get('fail_status_code') else None
        verify_ssl = up.get('verify_ssl', False)
        cached = list_upstream_cache(upstream)
        results = []
        for entry in cached:
            pattern = entry['pattern']
            check_url = f"{base_url}/{pattern}"
            try:
                resp = requests.get(check_url, allow_redirects=True, timeout=3, verify=verify_ssl)
                actual_url = resp.url.rstrip('/')
                status_code = str(resp.status_code)
                fail_url_match = actual_url.startswith(fail_url) if fail_url else False
                fail_status_match = (fail_status_code is not None and status_code == fail_status_code)
                tried_at = datetime.utcnow().isoformat(sep=' ', timespec='seconds')
                if not fail_url_match or (fail_status_code and not fail_status_match):
                    from .utils import cache_upstream_result
                    cache_upstream_result(pattern, upstream, actual_url, tried_at)
                    results.append({'pattern': pattern, 'success': True, 'resolved_url': actual_url, 'checked_at': tried_at})
                else:
                    from .utils import clear_upstream_cache
                    clear_upstream_cache(pattern)
                    results.append({'pattern': pattern, 'success': False, 'error': 'Fail criteria matched'})
            except Exception as e:
                results.append({'pattern': pattern, 'success': False, 'error': str(e)})
        return jsonify({'success': True, 'results': results})
    except Exception as e:
        import sys
        print(f"[RESYNC-ALL] Exception: {e}", file=sys.stderr)
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/admin/clear-upstream-logs', methods=['POST'])
@login_required
def clear_upstream_logs():
    db = get_db()
    db.execute('DELETE FROM upstream_check_log')
    db.commit()
    return redirect(url_for('main.admin_upstream_logs'))

# Global error handler for 500 errors: return JSON if requested
from flask import current_app
@bp.app_errorhandler(500)
def handle_500_error(e):
    if request.accept_mimetypes['application/json'] >= request.accept_mimetypes['text/html']:
        return jsonify({'success': False, 'error': 'Internal server error'}), 500
    return render_template('500.html'), 500