"""Backup, restore and upgrade-introspection endpoints.

These are the endpoints that make an upgrade recoverable. ``/admin/export-redirects``
only ever produced a shortcuts-only JSON dump; a real restore needs the database,
the configuration and the install state together, which is what
:mod:`app.utils.backup` produces.
"""

import io
import json
import logging
import os

from flask import (
    Blueprint,
    current_app,
    jsonify,
    render_template,
    request,
    send_file,
    session,
)

from app.config import config
from app.routes.routesUtils import login_required
from app.utils import backup as backup_mod
from app.utils import state as state_mod
from app.utils.paths import project_root, relative_to_data

bp = Blueprint('backup', __name__)

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 512 * 1024 * 1024
DOC_FILES = ("docs/UPGRADE.md", "docs/DATA-PERSISTENCE.md", "UPGRADE.md")


def _db_uri():
    from app.utils.utils import get_db_uri

    return get_db_uri()


def _semver():
    from app.CONSTANTS import get_semver

    return get_semver()


# --------------------------------------------------------------------------
# Upgrade introspection (drives the /upgrade page)
# --------------------------------------------------------------------------


def build_upgrade_info(latest: str | None = None, update_available: bool = False) -> dict:
    """Everything the upgrade page needs to render correct instructions.

    Assembled at request time from the live data directory, the migration files
    in this checkout and the local backup inventory, so the commands shown are
    the commands for *this* install rather than a generic paste.
    """
    data_dir = config.DATA_DIR
    db_uri = _db_uri()
    status = state_mod.schema_status(db_uri)
    install = state_mod.read_state(data_dir)
    db_path = backup_mod.resolve_db_path(db_uri, data_dir)

    info = {
        "ok": True,
        "version": _semver(),
        "latest": latest,
        "update_available": bool(update_available),
        "data": {
            "dir": data_dir,
            "dir_relative": os.path.relpath(data_dir, project_root())
            if data_dir.startswith(project_root())
            else data_dir,
            "in_docker": config.RUNNING_IN_DOCKER,
            "config_file": config.CONFIG_FILE,
            "database_uri": db_uri,
            "database_file": db_path,
            "database_relative": relative_to_data(db_path, data_dir) if db_path else None,
            "external_database": bool(db_uri) and not db_path,
            "backups_dir": state_mod.backup_dir(data_dir),
        },
        "schema": {
            "current": status["current"],
            "head": status["head"],
            "pending": status["pending"],
            "chain": status["chain"],
            "drift": status["drift"],
            "multiple_heads": status["multiple_heads"],
        },
        "install": {
            "installed_at": install.get("installed_at"),
            "app_version": install.get("app_version"),
            "schema_revision": install.get("schema_revision"),
            "last_migration": install.get("last_migration"),
            "last_backup": install.get("last_backup"),
            "last_restore": install.get("last_restore"),
        },
        "backups": backup_mod.backup_inventory(data_dir),
        "redis_enabled": config.redis_enabled,
        "docs": {},
    }
    for rel in DOC_FILES:
        path = os.path.join(project_root(), rel)
        if os.path.isfile(path):
            info["docs"][os.path.basename(rel).replace(".md", "").lower()] = rel
    return info


@bp.route('/api/upgrade-info')
def api_upgrade_info():
    """Machine-readable upgrade state. Reuses the cached GitHub version check."""
    from app.routes.version_routes import _version_check_cache

    latest = (_version_check_cache.get('result') or {}).get('latest')
    available = (_version_check_cache.get('result') or {}).get('update_available')
    return jsonify(build_upgrade_info(latest, available))


# --------------------------------------------------------------------------
# Backup
# --------------------------------------------------------------------------


@bp.route('/admin/backup')
@login_required
def admin_backup():
    """Backup and restore console."""
    info = build_upgrade_info()
    return render_template(
        'admin_backup.html',
        info=info,
        data_dir=config.DATA_DIR,
        backups=info['backups'],
        schema=info['schema'],
    )


@bp.route('/admin/backup/list')
@login_required
def api_backup_list():
    return jsonify({
        'success': True,
        'backups': backup_mod.backup_inventory(config.DATA_DIR),
        'state': state_mod.read_state(config.DATA_DIR),
    })


@bp.route('/admin/backup/create', methods=['POST'])
@login_required
def api_backup_create():
    """Write a fresh archive into data/backups/ and return its manifest."""
    label = (request.form.get('label') or request.json and request.json.get('label') or '')
    try:
        result = backup_mod.create_backup(
            config.DATA_DIR,
            config.CONFIG_FILE,
            _db_uri(),
            app_version=_semver(),
            kind='manual',
            label=label or '',
        )
    except backup_mod.BackupError as exc:
        logger.warning("Manual backup refused: %s", exc)
        return jsonify({'success': False, 'error': str(exc)}), 400
    return jsonify({
        'success': True,
        'name': result['name'],
        'path': result['path'],
        'manifest': result['manifest'],
    })


@bp.route('/admin/backup/download', methods=['GET'])
@login_required
def admin_backup_download():
    """Stream an archive as a download.

    ``?name=`` selects an existing local archive; omitting it creates a fresh
    one, so the "Download backup" button always yields something current.
    """
    name = (request.args.get('name') or '').strip()
    if name:
        safe = os.path.basename(name)
        path = os.path.join(state_mod.backup_dir(config.DATA_DIR), safe)
        if not os.path.isfile(path) or not safe.endswith('.zip'):
            return jsonify({'success': False, 'error': 'No such backup on disk.'}), 404
    else:
        try:
            result = backup_mod.create_backup(
                config.DATA_DIR, config.CONFIG_FILE, _db_uri(),
                app_version=_semver(), kind='manual', label='',
            )
        except backup_mod.BackupError as exc:
            return jsonify({'success': False, 'error': str(exc)}), 400
        path = result['path']
        safe = result['name']

    try:
        stream = backup_mod.to_stream(path)
    except OSError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 500
    return send_file(
        stream,
        mimetype='application/zip',
        as_attachment=True,
        download_name=safe,
    )


@bp.route('/admin/backup/<name>', methods=['DELETE'])
@login_required
def admin_backup_delete(name):
    safe = os.path.basename(name)
    if not safe.endswith('.zip'):
        return jsonify({'success': False, 'error': 'Invalid backup name.'}), 400
    path = os.path.join(state_mod.backup_dir(config.DATA_DIR), safe)
    if not os.path.isfile(path):
        return jsonify({'success': False, 'error': 'No such backup.'}), 404
    try:
        os.remove(path)
    except OSError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 500
    return jsonify({'success': True})


# --------------------------------------------------------------------------
# Restore
# --------------------------------------------------------------------------


@bp.route('/admin/backup/restore', methods=['POST'])
@login_required
def admin_backup_restore():
    """Restore from an uploaded archive or a named local one.

    The running application cannot overwrite the database it is serving from -
    on Windows the file is locked outright, and everywhere else it would pull
    the file out from under live connections. So the archive is validated,
    parked in the data directory, and applied by the entrypoint on the next
    start, *before* migrations. Refuses, rather than guessing, when the archive
    is corrupt, incomplete, or from a newer schema than this build understands.
    """
    from flask import flash, redirect, url_for

    target = request.form.get('name') or ''
    upload = request.files.get('file')
    defer = request.form.get('defer', '1') != '0'

    if not target and not (upload and upload.filename):
        return jsonify({'success': False, 'error': 'Choose a .zip file or a local backup.'}), 400

    head = state_mod.migration_graph()["head"]
    wants_json = request.accept_mimetypes.best == 'application/json' or bool(upload)

    try:
        if target:
            safe = os.path.basename(target)
            if not safe.endswith('.zip'):
                raise backup_mod.BackupError('Invalid backup name.')
            path = os.path.join(state_mod.backup_dir(config.DATA_DIR), safe)
            if not os.path.isfile(path):
                raise backup_mod.BackupError(f'Backup {safe} is not on disk.')
            source = path
        else:
            blob = upload.read(MAX_UPLOAD_BYTES + 1)
            if len(blob) > MAX_UPLOAD_BYTES:
                raise backup_mod.BackupError('Archive exceeds the 512 MiB limit.')
            source = io.BytesIO(blob)

        # Validate before staging: a corrupt archive must never be parked, or
        # the next start would fail with a far more confusing error.
        check = backup_mod.inspect_archive(source)
        if not check["ok"]:
            raise backup_mod.BackupError("Refusing to restore: " + "; ".join(check["errors"]))
        blocked = backup_mod.guard_revision(check["manifest"], head)
        if blocked:
            raise backup_mod.BackupError(blocked)

        if defer:
            result = backup_mod.stage_pending_restore(config.DATA_DIR, source)
            message = (
                "Backup verified and staged. Restart Redirector to apply it: "
                "`docker compose restart app` (Docker) or `docker restart redirector`. "
                "Migrations run automatically afterwards."
            )
        else:
            result = backup_mod.restore_archive(
                source,
                config.DATA_DIR,
                config.CONFIG_FILE,
                _db_uri(),
                current_head=head,
                safety_backup=not request.form.get('skip_safety'),
            )
            message = (
                f"Restored {', '.join(result['restored'])}. "
                "Restart the app so the remaining migrations run."
            )
    except backup_mod.BackupError as exc:
        logger.warning("Restore refused: %s", exc)
        if wants_json:
            return jsonify({'success': False, 'error': str(exc)}), 400
        flash(str(exc), 'error')
        return redirect(url_for('backup.admin_backup'))

    logger.warning("Admin restore requested: %s", result.get('pending') or result.get('restored'))
    if wants_json:
        return jsonify({'success': True, 'message': message, **result})
    flash(message, 'success')
    return redirect(url_for('backup.admin_backup'))


@bp.route('/api/backup/inspect', methods=['POST'])
@login_required
def api_backup_inspect():
    """Validate an uploaded archive without changing anything."""
    upload = request.files.get('file')
    if not upload or not upload.filename:
        return jsonify({'success': False, 'error': 'No file uploaded.'}), 400
    if not upload.filename.lower().endswith('.zip'):
        return jsonify({'success': False, 'error': 'Only .zip archives are backups.'}), 400
    result = backup_mod.inspect_archive(io.BytesIO(upload.read(MAX_UPLOAD_BYTES + 1)))
    return jsonify({
        'success': result['ok'],
        'manifest': result['manifest'],
        'errors': result['errors'],
    })


@bp.route('/api/data-dir')
def api_data_dir():
    """Public, non-sensitive view of where data lives.

    Exposed unauthenticated because the upgrade instructions have to be
    actionable for someone who is locked out of the admin UI after a failed
    upgrade - which is exactly when they most need to know the path.
    """
    db_path = backup_mod.resolve_db_path(_db_uri(), config.DATA_DIR)
    return jsonify({
        'success': True,
        'data_dir': config.DATA_DIR,
        'config_file': config.CONFIG_FILE,
        'database_file': db_path,
        'backups_dir': state_mod.backup_dir(config.DATA_DIR),
        'backup_count': len(state_mod.list_backups(config.DATA_DIR)),
        'restore_pending': os.path.isfile(backup_mod.pending_restore_path(config.DATA_DIR)),
        'in_docker': config.RUNNING_IN_DOCKER,
    })


@bp.route('/api/health/state')
def api_health_state():
    """Data-layer health, folded into the wider health story."""
    db_uri = _db_uri()
    status = state_mod.schema_status(db_uri)
    install = state_mod.read_state(config.DATA_DIR)
    report = state_mod.ensure_data_dir(config.DATA_DIR)
    return jsonify({
        'success': True,
        'data_dir_writable': report['writable'],
        'data_dir_error': report['error'],
        'schema': status,
        'last_migration': install.get('last_migration'),
        'last_backup': install.get('last_backup'),
    })


@bp.route('/admin/backup/state.json')
@login_required
def admin_state_json():
    """Raw install state, for support requests."""
    return current_app.response_class(
        json.dumps(state_mod.read_state(config.DATA_DIR), indent=2, sort_keys=True),
        mimetype='application/json',
    )


def _resolve_doc(name: str) -> str | None:
    """Map a requested doc name onto a bundled file.

    The URL is ``/admin/backup/docs/UPGRADE.md`` but ``DOC_FILES`` holds
    ``docs/UPGRADE.md``, so a plain membership test 404s on every real request.
    Match on the basename, then confirm the result is a known file, so a crafted
    name still cannot escape the project root.
    """
    wanted = os.path.basename(name).replace("\\", "/")
    for rel in DOC_FILES:
        if os.path.basename(rel) == wanted:
            return rel
    return None


@bp.route('/admin/backup/docs/<path:name>')
@login_required
def admin_backup_doc(name):
    """Serve a bundled doc file (used by the copy buttons on /upgrade)."""
    rel = _resolve_doc(name)
    if not rel:
        return jsonify({'success': False, 'error': 'Unknown document.'}), 404
    path = os.path.join(project_root(), rel)
    if not os.path.isfile(path):
        return jsonify({'success': False, 'error': f'{rel} is not bundled in this image.'}), 404
    with open(path, 'r', encoding='utf-8') as handle:
        body = handle.read()
    return current_app.response_class(
        body, mimetype='text/markdown; charset=utf-8'
    )
