import json
import time
from datetime import datetime, timezone

import requests
from flask import Blueprint, request, redirect, url_for, render_template, stream_with_context, Response, jsonify
import logging

from app.routes.routesUtils import login_required
from app.utils import utils
from model import UpstreamCheckLog

logger = logging.getLogger(__name__)
bp=Blueprint('upstream', __name__)

# --- Upstreams Config API ---
@bp.route('/admin/upstreams', methods=['GET', 'POST'])
@login_required
def admin_upstreams():
    error = None
    upstreams = utils.get_upstreams()
    if request.method == 'POST':
        if 'delete' in request.form:
            idx = int(request.form['delete'])
            if 0 <= idx < len(upstreams):
                deleted_name = upstreams[idx].get('name', 'Unnamed Upstream')
                del upstreams[idx]
                utils.set_upstreams(upstreams)
                logger.info(f"Deleted upstream: '{deleted_name}'")
                return redirect(url_for('upstream.admin_upstreams'))
            else:
                logger.warning(f"Attempted to delete non-existent upstream index: {idx}")
        else:
            new_upstreams = []
            i = 0
            while True:
                name = request.form.get(f'name_{i}')
                base_url = request.form.get(f'base_url_{i}')
                fail_url = request.form.get(f'fail_url_{i}')
                fail_status_code = request.form.get(f'fail_status_code_{i}')
                verify_ssl = request.form.get(f'verify_ssl_{i}') == 'on'
                skip_sso_cache = request.form.get(f'skip_sso_cache_{i}') == 'on'

                if name is None and base_url is None and fail_url is None and fail_status_code is None:
                    break
                if name or base_url or fail_url:
                    try:
                        fail_status_code = int(fail_status_code) if fail_status_code else None
                    except ValueError:
                        fail_status_code = None
                        logger.warning(
                            f"Invalid fail_status_code for upstream '{name}': '{request.form.get(f'fail_status_code_{i}')}'. Setting to None.")
                    new_upstreams.append({
                        'name': name or '',
                        'base_url': base_url or '',
                        'fail_url': fail_url or '',
                        'fail_status_code': fail_status_code,
                        'verify_ssl': verify_ssl,
                        'skip_sso_cache': skip_sso_cache
                    })
                i += 1
            utils.set_upstreams(new_upstreams)
            logger.info("Upstream configuration updated.")
            upstreams = new_upstreams  # Refresh the list for rendering
    logger.debug("Rendering admin upstreams page.")
    return render_template('admin_upstreams.html', upstreams=upstreams, error=error)


@bp.route('/check-upstreams-ui/<path:pattern>')  # Use path converter
def check_upstreams_ui(pattern):
    delay = utils.get_auto_redirect_delay()
    logger.info(f"Displaying upstream check UI for pattern: '{pattern}', delay: {delay}s")
    return render_template('check_upstreams_stream.html', pattern=pattern, delay=delay)


@bp.route('/stream/check-upstreams/<path:pattern>')  # Use path converter
def stream_check_upstreams(pattern):
    @stream_with_context
    def event_stream():
        found = False
        redirect_url = None

        def send_log(message, extra_data=None):
            timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
            data = {'log': f"[{timestamp}] {message}"}
            if extra_data:
                data.update(extra_data)
            yield f"data: {json.dumps(data)}\n\n"

        yield from send_log(f"🔍 Starting upstream check for pattern: `{pattern}`")
        logger.info(f"Stream initiated for upstream check of pattern: '{pattern}'")

        for up in utils.get_upstreams():
            up_name = up.get('name', '[unnamed]')
            base_url = up.get('base_url', '').rstrip('/')
            fail_url = up.get('fail_url', '')  # Keep as is, rstrip is not desired for comparison here
            fail_status_code = str(up.get('fail_status_code')) if up.get('fail_status_code') else None
            verify_ssl = up.get('verify_ssl', False)

            # Basic validation of upstream config for current check
            if not base_url:
                yield from send_log(f"⚠️ Warning: Upstream '{up_name}' has no base_url configured. Skipping.")
                logger.warning(f"Upstream '{up_name}' missing base_url, skipping check.")
                continue
            if not fail_url:
                yield from send_log(
                    f"⚠️ Warning: Upstream '{up_name}' missing fail_url. This might lead to incorrect detections.")
                logger.warning(f"Upstream '{up_name}' missing fail_url.")

            check_url = f"{base_url}/{pattern}"
            yield from send_log(f"🌐 Checking upstream: {up_name}")
            yield from send_log(f"Constructed URL: {check_url}")
            yield from send_log(f"Fail criteria → URL: '{fail_url}', Status: {fail_status_code or 'Not specified'}")
            yield from send_log(f"SSL Verification: {'Enabled' if verify_ssl else 'Disabled'}")

            try:
                resp = requests.get(check_url, allow_redirects=True, timeout=5, verify=verify_ssl)
                actual_url = resp.url
                status_code = str(resp.status_code)

                yield from send_log(f"➡️ Response received from {check_url} → {actual_url} (status {status_code})")
                logger.debug(f"Upstream '{up_name}' response: actual_url='{actual_url}', status='{status_code}'")

                fail_url_match = actual_url.startswith(fail_url) if fail_url else False
                fail_status_match = (fail_status_code is not None and status_code == fail_status_code)

                # SSO detection - must not cache SSO login pages
                is_sso = utils.is_sso_url(actual_url)
                if is_sso:
                    logger.warning(f"SSO URL detected for '{pattern}' in '{up_name}': {actual_url} - will not cache")
                    utils.log_upstream_check(
                        pattern=pattern, upstream_name=up_name, check_url=check_url, result='sso_required',
                        detail=f"actual_url={actual_url}, status_code={status_code}, sso_detected=True", cached=False
                    )
                    # SSO links exist but require auth - redirect to original check_url so browser handles SSO
                    # Do NOT cache SSO URLs (they are useless for next user)
                    yield from send_log(
                        f"⚠️ SSO required for {up_name} (redirected to SSO login: {actual_url}) - not caching, will use original URL",
                        {'found': True, 'redirect_url': check_url}
                    )
                    found = True
                    redirect_url = check_url
                    logger.info(f"Shortcut '{pattern}' found in '{up_name}' but requires SSO - redirecting to original URL.")
                    break

                if not fail_url_match or (fail_status_code and not fail_status_match):
                    found = True
                    redirect_url = actual_url
                    # Only cache if not SSO and upstream allows it
                    should_cache = utils.should_cache_upstream_result(up, actual_url) and utils.is_upstream_cache_enabled()
                    utils.log_upstream_check(
                       pattern= pattern,upstream_name= up_name,check_url= check_url, result='success',
                        detail=f"actual_url={actual_url}, status_code={status_code}",cached=should_cache
                    )
                    if should_cache:
                        utils.cache_upstream_result(pattern, up_name, actual_url)
                    else:
                        logger.info(f"Skipping cache for '{pattern}' in '{up_name}' (SSO or cache disabled)")
                    yield from send_log(
                        f"✅ Shortcut found in {up_name} (redirected to {actual_url}, status {status_code})" + ("" if should_cache else " (not cached - SSO/disabled)"),
                        {'found': True, 'redirect_url': redirect_url}
                    )
                    logger.info(f"Shortcut '{pattern}' successfully found in upstream '{up_name}'.")
                    break
                else:
                    utils.log_upstream_check(
                        pattern, up_name, check_url, 'fail',
                        f"actual_url={actual_url}, status_code={status_code}, fail_url_match={fail_url_match}, fail_status_match={fail_status_code}",

                    )
                    yield from send_log(f"❌ Shortcut not found in {up_name} — matched fail criteria.")
                    logger.info(f"Shortcut '{pattern}' not found in upstream '{up_name}' (matched fail criteria).")

            except requests.exceptions.Timeout:
                utils.log_upstream_check(pattern, up_name, check_url, 'timeout', 'Request timed out')
                yield from send_log(f"⚠️ Timeout checking {up_name}: Request timed out after 5 seconds.",
                                    {'error': True})
                logger.error(f"Upstream check for '{up_name}' timed out for pattern '{pattern}'.")
            except requests.exceptions.ConnectionError as e:
                utils.log_upstream_check(pattern, up_name, check_url, 'connection_error', str(e))
                yield from send_log(f"⚠️ Connection error checking {up_name}: {str(e)}", {'error': True})
                logger.error(f"Connection error for upstream '{up_name}' and pattern '{pattern}': {e}")
            except requests.exceptions.RequestException as e:
                utils.log_upstream_check(pattern, up_name, check_url, 'request_exception', str(e))
                yield from send_log(f"⚠️ HTTP request error for {up_name}: {str(e)}", {'error': True})
                logger.error(f"HTTP request error for upstream '{up_name}' and pattern '{pattern}': {e}")
            except Exception as e:
                utils.log_upstream_check(pattern, up_name, check_url, 'exception', str(e))
                yield from send_log(f"⚠️ An unexpected error occurred for {up_name}: {str(e)}", {'error': True})
                logger.exception(f"Unexpected error during upstream check for '{up_name}' and pattern '{pattern}'.")

            yield from send_log(f"--- Finished check for {up_name} ---")
            time.sleep(0.5)

        if not found:
            yield from send_log("🔚 No upstream found containing the shortcut.")
            logger.info(f"No upstream found for pattern: '{pattern}'.")
            yield f"data: {json.dumps({'done': True})}\n\n"
        else:
            yield f"data: {json.dumps({'done': True, 'final_redirect_url': redirect_url})}\n\n"  # Send final redirect for client-side action

    return Response(event_stream(), mimetype='text/event-stream')


@bp.route('/admin/upstream-logs')
@login_required
def admin_upstream_logs():
    logs = utils.get_upstream_logs()
    logger.debug("Rendering admin upstream logs page.")
    return render_template('admin_upstream_logs.html', logs=logs)


# --- Upstream Cache Management ---
@bp.route('/admin/upstream-cache/<upstream>')
@login_required
def admin_upstream_cache(upstream):
    cached = utils.list_upstream_cache(upstream)
    logger.debug(f"Rendering admin upstream cache page for '{upstream}'.")
    return render_template('admin_upstream_cache.html', upstream=upstream, cached=cached)


@bp.route('/admin/upstream-cache/resync/<upstream>/<path:pattern>', methods=['GET', 'POST'])
@login_required
def admin_upstream_cache_resync(upstream, pattern):
    try:
        logger.info(f"Admin initiated resync for upstream='{upstream}', pattern='{pattern}'")
        up = next((u for u in utils.get_upstreams() if u.get('name') == upstream), None)
        if not up:
            logger.warning(f"Upstream '{upstream}' not found during resync operation.")
            return jsonify({'success': False, 'error': 'Upstream not found'}), 404

        base_url = up.get('base_url', '').rstrip('/')
        check_url = f"{base_url}/{pattern}"

        try:
            verify_ssl = up.get('verify_ssl', False)
            resp = requests.get(check_url, allow_redirects=True, timeout=5, verify=verify_ssl)
            actual_url = resp.url
            status_code = str(resp.status_code)

            fail_url = up.get('fail_url', '')
            fail_status_code = str(up.get('fail_status_code')) if up.get('fail_status_code') else None

            fail_url_match = actual_url.startswith(fail_url) if fail_url else False
            fail_status_match = (fail_status_code is not None and status_code == fail_status_code)

            logger.debug(
                f"Resync check for '{pattern}' in '{upstream}': actual_url='{actual_url}', status='{status_code}', fail_url_match={fail_url_match}, fail_status_match={fail_status_match}")

            # SSO check - do not cache SSO URLs
            if utils.is_sso_url(actual_url):
                logger.warning(f"SSO URL detected during resync for '{pattern}' in '{upstream}': {actual_url} - will not cache")
                utils.log_upstream_check(pattern, upstream, check_url, 'sso_required', f"actual_url={actual_url}, status_code={status_code}, sso_detected=True", cached=False)
                utils.clear_upstream_cache(pattern)
                return jsonify({'success': False, 'error': 'Upstream requires SSO authentication - not cached. Link may exist but needs browser login.', 'sso': True,
                                'checked_at': datetime.now(timezone.utc).isoformat(sep=' ', timespec='seconds')})

            if not fail_url_match and (fail_status_code is None or not fail_status_match):
                tried_at = datetime.now(timezone.utc).isoformat(sep=' ', timespec='seconds')
                if not utils.should_cache_upstream_result(up, actual_url):
                    logger.info(f"Skipping cache for SSO URL '{pattern}' in '{upstream}' during resync")
                    return jsonify({'success': True, 'resolved_url': actual_url, 'checked_at': tried_at, 'cached': False, 'warning': 'SSO URL not cached'})
                utils.cache_upstream_result(pattern, upstream, actual_url, tried_at)
                logger.info(f"Resync for '{pattern}' in '{upstream}' successful, cache updated.")
                return jsonify({'success': True, 'resolved_url': actual_url, 'checked_at': tried_at})
            else:
                utils.clear_upstream_cache(pattern)
                logger.info(f"Resync for '{pattern}' in '{upstream}' failed (matched fail criteria), cache cleared.")
                return jsonify({'success': False, 'error': 'Pattern not found in upstream (fail criteria matched).',
                                'checked_at': datetime.now(timezone.utc).isoformat(sep=' ', timespec='seconds')})
        except requests.exceptions.RequestException as e:
            utils.clear_upstream_cache(pattern)
            logger.error(f"Resync upstream check for '{pattern}' in '{upstream}' failed with HTTP error: {e}")
            return jsonify({'success': False, 'error': f"Upstream check failed: {str(e)}",
                            'checked_at': datetime.now(timezone.utc).isoformat(sep=' ', timespec='seconds')})
        except Exception as e:
            utils.clear_upstream_cache(pattern)
            logger.exception(f"Unexpected error during resync upstream check for '{pattern}' in '{upstream}'.")
            return jsonify({'success': False, 'error': f"An unexpected error occurred during upstream check: {str(e)}",
                            'checked_at': datetime.now(timezone.utc).isoformat(sep=' ', timespec='seconds')})
    except Exception as e:
        logger.exception(f"Top-level error during admin_upstream_cache_resync for '{upstream}'.")
        return jsonify(
            {'success': False, 'error': 'Unexpected server error during resync operation', 'details': str(e)}), 500



@bp.route('/admin/upstream-cache/purge-entry/<upstream>/<path:pattern>', methods=['POST'])
@login_required
def admin_upstream_cache_purge_entry(upstream, pattern):
    try:
        utils.clear_upstream_cache(pattern, upstream_name=upstream)
        return jsonify({'success': True, 'purged': 1})
    except Exception as e:
        utils.db.session.rollback()
        logger.exception(f"Error purging cache entry for pattern '{pattern}' in upstream '{upstream}'.")
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/admin/upstream-cache/purge/<upstream>', methods=['POST'])
@login_required
def admin_upstream_cache_purge(upstream):
    try:
        cached_entries_for_upstream = utils.list_upstream_cache(upstream)
        purged_count = 0
        for entry in cached_entries_for_upstream:
            utils.clear_upstream_cache(entry['pattern'], upstream_name=upstream)
            purged_count += 1
        logger.info(f"Purged {purged_count} cache entries for upstream: '{upstream}'.")
        return jsonify({'success': True, 'purged': purged_count})
    except Exception as e:
        logger.exception(f"Error purging cache for upstream: '{upstream}'.")
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/admin/upstream-cache/resync-all/<upstream>', methods=['POST'])
@login_required
def admin_upstream_cache_resync_all(upstream):
    try:
        logger.info(f"Admin initiated full resync for all cached patterns in upstream: '{upstream}'.")
        up = next((u for u in utils.get_upstreams() if u.get('name') == upstream), None)
        if not up:
            logger.warning(f"Upstream '{upstream}' not found during resync-all operation.")
            return jsonify({'success': False, 'error': 'Upstream not found'}), 404

        base_url = up.get('base_url', '').rstrip('/')
        fail_url = up.get('fail_url', '')
        fail_status_code = str(up.get('fail_status_code')) if up.get('fail_status_code') else None
        verify_ssl = up.get('verify_ssl', False)

        cached_entries_for_upstream = utils.list_upstream_cache(upstream)
        patterns_to_check = [entry['pattern'] for entry in cached_entries_for_upstream]

        results = []
        for pattern in patterns_to_check:
            check_url = f"{base_url}/{pattern}"
            try:
                resp = requests.get(check_url, allow_redirects=True, timeout=5, verify=verify_ssl)
                actual_url = resp.url
                status_code = str(resp.status_code)

                fail_url_match = actual_url.startswith(fail_url) if fail_url else False
                fail_status_match = (fail_status_code is not None and status_code == fail_status_code)
                tried_at = datetime.now(timezone.utc).isoformat(sep=' ', timespec='seconds')

                # SSO detection for resync-all
                if utils.is_sso_url(actual_url):
                    logger.warning(f"SSO URL detected during resync-all for '{pattern}' in '{upstream}': {actual_url} - will not cache")
                    utils.log_upstream_check(pattern, upstream, check_url, 'sso_required', f"actual_url={actual_url}, status_code={status_code}, sso_detected=True", cached=False)
                    utils.clear_upstream_cache(pattern)
                    results.append({'pattern': pattern, 'success': False, 'error': 'SSO required - not cached', 'sso': True,
                                    'checked_at': tried_at})
                    logger.debug(f"Resync-all: Pattern '{pattern}' needs SSO, not cached.")
                    continue

                if not fail_url_match and (fail_status_code is None or not fail_status_match):
                    if not utils.should_cache_upstream_result(up, actual_url):
                        results.append({'pattern': pattern, 'success': True, 'resolved_url': actual_url, 'checked_at': tried_at, 'cached': False})
                        logger.debug(f"Resync-all: Pattern '{pattern}' SSO not cached.")
                        continue
                    utils.cache_upstream_result(pattern, upstream, actual_url, tried_at)
                    results.append(
                        {'pattern': pattern, 'success': True, 'resolved_url': actual_url, 'checked_at': tried_at})
                    logger.debug(f"Resync-all: Pattern '{pattern}' in '{upstream}' successful.")
                else:
                    utils.clear_upstream_cache(pattern)
                    results.append({'pattern': pattern, 'success': False, 'error': 'Fail criteria matched',
                                    'checked_at': tried_at})
                    logger.debug(f"Resync-all: Pattern '{pattern}' in '{upstream}' failed (matched fail criteria).")
            except requests.exceptions.RequestException as e:
                utils.clear_upstream_cache(pattern)
                results.append({'pattern': pattern, 'success': False, 'error': f"Upstream check failed: {str(e)}",
                                'checked_at': datetime.now(timezone.utc).isoformat(sep=' ', timespec='seconds')})
                logger.error(f"Resync-all: HTTP error for '{pattern}' in '{upstream}': {e}")
            except Exception as e:
                utils.clear_upstream_cache(pattern)
                results.append(
                    {'pattern': pattern, 'success': False, 'error': f"An unexpected error occurred: {str(e)}",
                     'checked_at': datetime.now(timezone.utc).isoformat(sep=' ', timespec='seconds')})
                logger.exception(f"Resync-all: Unexpected error for '{pattern}' in '{upstream}'.")
        logger.info(f"Finished full resync for upstream: '{upstream}'. Processed {len(patterns_to_check)} patterns.")
        return jsonify({'success': True, 'results': results})
    except Exception as e:
        logger.exception(f"Top-level error during admin_upstream_cache_resync_all for '{upstream}'.")
        return jsonify(
            {'success': False, 'error': 'Unexpected server error during resync-all operation', 'details': str(e)}), 500


@bp.route('/admin/clear-upstream-logs', methods=['POST'])
@login_required
def clear_upstream_logs():
    from model import db  # Get db instance from model
    db.session.query(UpstreamCheckLog).delete()
    db.session.commit()
    logger.info("All upstream check logs cleared.")
    return redirect(url_for('upstream.admin_upstream_logs'))
