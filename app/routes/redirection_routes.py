from datetime import datetime, timezone

from flask import Blueprint, request, redirect, url_for, render_template, make_response

from app import CONSTANTS
from app.routes.routesUtils import login_required
from app.utils import utils
import logging

bp = Blueprint('redirection', __name__)
logger = logging.getLogger(__name__)

def _with_sso_headers(response, target_url):
    """Add no-cache headers for SSO URLs (SSO never cached)."""
    try:
        if target_url and utils.is_sso_url(target_url):
            response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
            response.headers['X-SSO-Link'] = 'true'
    except Exception:
        pass
    return response


@bp.route('/delete/<path:subpath>', methods=['GET', 'POST'])
@login_required
def dashboard_delete(subpath):
    if utils.get_delete_requires_password():
        if request.method == 'POST':
            admin_pwd = utils.get_admin_password()
            if request.form.get('password') == admin_pwd:
                utils.deleteShortCut(subpath)
                logger.info(f"Shortcut '{subpath}' deleted by admin.")
                return redirect(url_for('main.dashboard'))
            else:
                error = 'Invalid password.'
                logger.warning(f"Failed delete attempt for '{subpath}' (invalid password).")
                return render_template('delete_confirm.html', error=error, subpath=subpath)
        else:
            logger.debug(f"Displaying delete confirmation for '{subpath}'.")
            return render_template('delete_confirm.html', subpath=subpath, error=None)
    else:
        logger.info(f"Shortcut '{subpath}' deleted (password not required).")
        utils.deleteShortCut(subpath)
        return redirect(url_for('main.dashboard'))


@bp.route('/edit/<path:subpath>', methods=['GET', 'POST'])
def edit_redirect(subpath):
    shortcut, source_data, resp_time = utils.get_shortcut(subpath)
    if request.method == 'POST':
        type_ = request.form['type']
        target = request.form['target']
        current_time = datetime.now(timezone.utc).isoformat(sep=' ', timespec='seconds')
        ip_address = request.remote_addr or 'unknown'
        import re as _re
        user_dynamic_type = getattr(CONSTANTS, 'DATA_TYPE_USER_DYNAMIC', 'user-dynamic')
        # Validate target URL (allow placeholders, replace with dummy for check)
        dummy = _re.sub(r'\{[^}]+\}|\[[^\]]+\]', 'dummy', target)
        if target and not utils.is_safe_redirect_target(dummy):
            return render_template('error.html', message='Invalid target URL. Only http/https URLs are allowed.'), 400
        if type_ == user_dynamic_type:
            user_placeholder_names = _re.findall(r'\[([^\]]+)\]', target)
            param_descriptions = {}
            for name in user_placeholder_names:
                desc = request.form.get(f'param_desc_{name}', '').strip()
                # Fallback: if no description provided (e.g., API/test), use param name as description
                if not desc:
                    desc = f"Value for {name}"
                param_descriptions[name] = desc
            from model.user_param import UserParam
            from app import db
            now_dt = datetime.now(timezone.utc)
            for name, desc in param_descriptions.items():
                param = UserParam.query.filter_by(shortcut_pattern=subpath, param_name=name).first()
                if not param:
                    param = UserParam(
                        shortcut_pattern=subpath,
                        param_name=name,
                        description=desc,
                        required=True,
                        created_at=now_dt,
                        updated_at=now_dt
                    )
                    db.session.add(param)
                else:
                    param.description = desc
                    param.updated_at = now_dt
            db.session.commit()
        try:
            utils.set_shortcut(
                pattern=subpath,
                type_=type_,
                target=target,
                created_at=current_time if not shortcut else None,
                updated_at=current_time,
                created_ip=ip_address if not shortcut else None,
                updated_ip=ip_address
            )
            logger.info(
                f"Shortcut '{subpath}' {'updated' if shortcut else 'created'}."
            )
            return render_template(
                'success_create.html', pattern=subpath, target=target
            )
        except Exception:
            logger.exception(
                f"Failed to {'update' if shortcut else 'create'} shortcut '{subpath}'."
            )
            return render_template(
                'error.html', message='Failed to save shortcut.'
            )
    else:
        if not shortcut:
            logger.debug(f"Displaying create shortcut page for new pattern: '{subpath}'.")
            return render_template('create_shortcut.html', pattern=subpath)
        logger.debug(f"Displaying edit shortcut page for existing pattern: '{subpath}'.")
        return render_template('edit_shortcut.html', pattern=subpath, type=shortcut['type'], target=shortcut['target'])


@bp.route('/<path:subpath>', methods=['GET'])
def handle_redirect(subpath):
    logger.info(f"Attempting to handle redirect for subpath: '{subpath}'")
    # Use hierarchical resolver to support patterns like x/abc, x/def, y/h/k
    shortcut, data_source, resp_time, matched_pattern, dynamic_props = utils.get_shortcut_for_path(subpath)
    # Fallback for case where hierarchical resolver skipped static with leftover - try legacy first-segment dynamic check
    if not shortcut:
        # No hierarchical match found - proceed to upstream/create logic
        pass
    if shortcut:
        # For hierarchical patterns, matched_pattern is the actual stored pattern
        pattern = matched_pattern
        shortcut_type = shortcut.get(CONSTANTS.KEY_DATA_TYPE)
        target = shortcut.get('target')
        if (data_source == CONSTANTS.data_source_redirect or data_source == CONSTANTS.data_source_redis) and \
                shortcut_type == CONSTANTS.DATA_TYPE_STATIC:
            utils.increment_access_count(pattern)
            logger.info(
                f"Redirecting static shortcut: '{subpath}' (matched '{pattern}') -> '{target}' "
                f"(Source: {data_source}, Time: {resp_time:.4f}s)"
            )
            # Validate target is safe (http/https only) - prevents javascript: open redirect
            if not utils.is_safe_redirect_target(target):
                logger.warning(f"Blocked unsafe redirect target for '{pattern}': {target}")
                return render_template('error.html', message='Invalid redirect target. Only http/https URLs are allowed.'), 400
            if utils.get_auto_redirect_delay() > 0:
                resp = make_response(render_template(
                    'redirect.html',
                    target=target,
                    delay=utils.get_auto_redirect_delay(),
                    source=data_source,
                    response_time=resp_time
                ))
                return _with_sso_headers(resp, target)
            resp = make_response(redirect(target, code=302))
            return _with_sso_headers(resp, target)

        # UPSTREAM _HANDLING :::
        if data_source == CONSTANTS.data_source_upstream and shortcut.get('resolved_url'):
            resolved = shortcut['resolved_url']
            logger.info(
                f"Redirecting upstream shortcut: '{subpath}' -> '{resolved}' "
                f"(Source: {data_source}, Time: {resp_time:.4f}s)"
            )
            if not utils.is_safe_redirect_target(resolved):
                logger.warning(f"Blocked unsafe upstream redirect for '{pattern}': {resolved}")
                return render_template('error.html', message='Invalid upstream redirect target.'), 400
            if utils.get_auto_redirect_delay() > 0:
                resp = make_response(render_template(
                    'redirect.html',
                    target=resolved,
                    delay=utils.get_auto_redirect_delay(),
                    source=data_source,
                    response_time=resp_time
                ))
                return _with_sso_headers(resp, resolved)
            resp = make_response(redirect(resolved, code=302))
            return _with_sso_headers(resp, resolved)

        if (data_source == CONSTANTS.data_source_redirect or data_source == CONSTANTS.data_source_redis) and \
                shortcut_type in [CONSTANTS.DATA_TYPE_DYNAMIC, CONSTANTS.DATA_TYPE_USER_DYNAMIC]:
            import re as _re
            placeholder_names = utils.get_placeholder_vars(target)
            # Only extract user_placeholder_names if user-dynamic, else empty list
            if shortcut_type == CONSTANTS.DATA_TYPE_USER_DYNAMIC:
                user_placeholder_names = _re.findall(r'\[([^\]]+)\]', target)
            else:
                user_placeholder_names = []
            all_placeholders = list(dict.fromkeys(placeholder_names + user_placeholder_names))
            user_param_info = {}
            if shortcut_type == CONSTANTS.DATA_TYPE_USER_DYNAMIC:
                from model.user_param import UserParam
                user_param_objs = UserParam.query.filter(
                    (UserParam.shortcut_pattern == pattern) &
                    (UserParam.param_name.in_(all_placeholders))
                ).all()
                user_param_info = {
                    p.param_name: {'description': p.description, 'required': p.required}
                    for p in user_param_objs
                }
            # Map dynamic_props to placeholder_names by position
            param_values = {}
            for i, name in enumerate(all_placeholders):
                if i < len(dynamic_props):
                    param_values[name] = dynamic_props[i]
            # For required params, check if missing (user-dynamic) or just if missing (dynamic)
            missing_required = []
            if shortcut_type == CONSTANTS.DATA_TYPE_USER_DYNAMIC:
                for name in all_placeholders:
                    if user_param_info.get(name, {}).get('required') and not param_values.get(name):
                        missing_required.append(name)
            else:  # For dynamic, treat all as required
                for name in all_placeholders:
                    if not param_values.get(name):
                        missing_required.append(name)
            if missing_required:
                # For user-dynamic, provide rich usage page with param descriptions & client prompt fallback
                if shortcut_type == CONSTANTS.DATA_TYPE_USER_DYNAMIC:
                    return render_template(
                        'dynamic_shortcut_usage.html',
                        pattern=pattern,
                        dynamic_params=all_placeholders,
                        param_values=param_values,
                        missing_required=missing_required,
                        target=target,
                        example_param=all_placeholders[0] if all_placeholders else None,
                        user_param_info=user_param_info,
                        is_user_dynamic=True
                    )
                # For dynamic, show a hint/usage page
                return render_template(
                    'dynamic_shortcut_usage.html',
                    pattern=pattern,
                    dynamic_params=all_placeholders,
                    param_values=param_values,
                    missing_required=missing_required,
                    target=target,
                    example_param=all_placeholders[0] if all_placeholders else None
                )
            dest_url = target
            for name in all_placeholders:
                dest_url = dest_url.replace('{' + name + '}', param_values.get(name, ''))
                dest_url = dest_url.replace('[' + name + ']', param_values.get(name, ''))
            if not utils.is_safe_redirect_target(dest_url):
                logger.warning(f"Blocked unsafe dynamic redirect for '{pattern}': {dest_url}")
                return render_template('error.html', message='Invalid dynamic redirect target.'), 400
            utils.increment_access_count(pattern)
            logger.info(
                f"Redirecting dynamic shortcut: '{subpath}' -> '{dest_url}' (Source: {data_source})"
            )
            if utils.get_auto_redirect_delay() > 0:
                resp = make_response(render_template(
                    'redirect.html',
                    target=dest_url,
                    delay=utils.get_auto_redirect_delay(),
                    source=data_source
                ))
                return _with_sso_headers(resp, dest_url)
            resp = make_response(redirect(dest_url, code=302))
            return _with_sso_headers(resp, dest_url)

    logger.info(f"No direct shortcut found for '{subpath}'. Checking live upstreams.")
    if utils.get_upstreams():
        sanitized = utils.sanitize_pattern(subpath) if hasattr(utils, 'sanitize_pattern') else subpath.strip().strip('/')
        check_pattern = sanitized if sanitized else subpath.split('/')[0]
        logger.debug(f"Redirecting to upstream check UI for pattern: '{check_pattern}'")
        return redirect(url_for('upstream.check_upstreams_ui', pattern=check_pattern), code=302)

    # No upstreams: show not-found with suggestions (#71) instead of immediate create redirect
    try:
        suggestions = utils.get_similar_patterns(subpath, limit=3)
        # Also check for expired/private handling: if exact pattern exists but is expired/private, suggestions already handled above
        # For now, render not_found with suggestions and CTA to create
        return render_template('not_found.html', pattern=subpath, suggestions=suggestions), 404
    except Exception:
        logger.exception("Not-found handling failed")
        return redirect(url_for('redirection.edit_redirect', subpath=subpath))


@bp.route('/edit/', methods=['GET', 'POST'])
def edit_redirect_blank():
    if request.method == 'POST':
        pattern = request.form.get('pattern', '').strip()
        type_ = request.form.get('type', CONSTANTS.DATA_TYPE_STATIC)
        target = request.form.get('target', '').strip()
        current_time = datetime.now(timezone.utc).isoformat(sep=' ', timespec='seconds')
        ip_address = request.remote_addr or 'unknown'
        import re as _re
        # Validate target
        dummy2 = _re.sub(r'\{[^}]+\}|\[[^\]]+\]', 'dummy', target)
        if target and not utils.is_safe_redirect_target(dummy2):
            return render_template('error.html', message='Invalid target URL. Only http/https URLs are allowed.'), 400
        user_dynamic_type = getattr(CONSTANTS, 'DATA_TYPE_USER_DYNAMIC', 'user-dynamic')
        if type_ == user_dynamic_type:
            user_placeholder_names = _re.findall(r'\[([^\]]+)\]', target)
            param_descriptions = {}
            for name in user_placeholder_names:
                desc = request.form.get(f'param_desc_{name}', '').strip()
                if not desc:
                    desc = f"Value for {name}"
                param_descriptions[name] = desc
            from model.user_param import UserParam
            from app import db
            now_dt = datetime.now(timezone.utc)
            for name, desc in param_descriptions.items():
                param = UserParam.query.filter_by(shortcut_pattern=pattern, param_name=name).first()
                if not param:
                    param = UserParam(
                        shortcut_pattern=pattern,
                        param_name=name,
                        description=desc,
                        required=True,
                        created_at=now_dt,
                        updated_at=now_dt
                    )
                    db.session.add(param)
                else:
                    param.description = desc
                    param.updated_at = now_dt
            db.session.commit()
        if not pattern:
            logger.warning("Attempted to create shortcut with empty pattern.")
            return render_template('create_shortcut.html', pattern='', error='Shortcut pattern cannot be empty.')
        if utils.isPatternExists(pattern):
            try:
                utils.set_shortcut(
                    pattern=pattern,
                    type_=type_,
                    target=target,
                    updated_at=current_time,
                    updated_ip=ip_address
                )
                logger.info(
                    f"Shortcut '{pattern}' updated successfully via edit route."
                )
                return render_template(
                    'success_create.html', pattern=pattern, target=target
                )
            except Exception:
                logger.exception(
                    f"Failed to update shortcut '{pattern}' via edit route."
                )
                return render_template(
                    'error.html', message='Failed to update shortcut.'
                )
        try:
            utils.set_shortcut(
                pattern=pattern,
                type_=type_,
                target=target,
                created_at=current_time,
                updated_at=current_time,
                created_ip=ip_address,
                updated_ip=ip_address
            )
            logger.info(
                f"New shortcut '{pattern}' created successfully via blank edit route."
            )
            return render_template(
                'success_create.html', pattern=pattern, target=target
            )
        except Exception:
            logger.exception(
                f"Failed to create new shortcut '{pattern}' via blank edit route."
            )
            return render_template(
                'error.html', message='Failed to create shortcut.'
            )
    logger.debug("Rendering blank create shortcut page.")
    return render_template('create_shortcut.html', pattern='')
