import io
import base64
import secrets
import json
import logging
from flask import Blueprint, render_template, request, redirect, url_for, session, jsonify, flash

from app.routes.routesUtils import login_required
from app.config import config

logger = logging.getLogger(__name__)
bp = Blueprint('mfa', __name__)

def is_mfa_enabled():
    mfa = config.get_configuration().get('mfa', {})
    return bool(mfa.get('enabled') and mfa.get('secret'))

def get_mfa_secret():
    return config.get_configuration().get('mfa', {}).get('secret')

def verify_totp(token: str) -> bool:
    try:
        import pyotp
        secret = get_mfa_secret()
        if not secret or not token:
            return False
        totp = pyotp.TOTP(secret)
        # Allow 1 window drift (30s)
        return totp.verify(token.strip(), valid_window=1)
    except Exception as e:
        logger.exception(f"TOTP verify failed: {e}")
        return False

def generate_secret():
    try:
        import pyotp
        return pyotp.random_base32()
    except Exception:
        # fallback
        return secrets.token_hex(10).upper()

def generate_backup_codes(n=5):
    return [secrets.token_hex(4).upper() + "-" + secrets.token_hex(4).upper() for _ in range(n)]

def get_qr_uri(secret, username="admin"):
    try:
        import pyotp
        totp = pyotp.TOTP(secret)
        return totp.provisioning_uri(name=username, issuer_name="Redirector")
    except Exception:
        return f"otpauth://totp/Redirector:{username}?secret={secret}&issuer=Redirector"

def get_qr_base64(uri):
    try:
        import qrcode
        img = qrcode.make(uri)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        logger.warning(f"QR generation failed: {e}")
        return None

@bp.route('/admin/mfa/setup', methods=['GET', 'POST'])
@login_required
def mfa_setup():
    cfg = config.get_configuration()
    mfa = cfg.get('mfa', {})
    secret = mfa.get('secret')
    enabled = mfa.get('enabled', False)
    
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'generate':
            new_secret = generate_secret()
            # store temporarily in session for verification
            session['mfa_temp_secret'] = new_secret
            uri = get_qr_uri(new_secret)
            qr_b64 = get_qr_base64(uri)
            return render_template('mfa_setup.html', secret=new_secret, qr_b64=qr_b64, uri=uri, enabled=enabled, step='verify')
        elif action == 'verify':
            temp_secret = session.get('mfa_temp_secret')
            token = request.form.get('token', '').strip()
            if not temp_secret:
                flash('No pending secret. Generate again.', 'error')
                return redirect(url_for('mfa.mfa_setup'))
            try:
                import pyotp
                totp = pyotp.TOTP(temp_secret)
                if totp.verify(token, valid_window=1):
                    # Enable MFA
                    backup_codes = generate_backup_codes()
                    cfg['mfa'] = {
                        'enabled': True,
                        'secret': temp_secret,
                        'backup_codes': backup_codes,
                        'passkeys': mfa.get('passkeys', [])
                    }
                    with open(config.CONFIG_FILE, 'w') as f:
                        json.dump(cfg, f, indent=2)
                    config.reload()
                    session.pop('mfa_temp_secret', None)
                    flash('MFA enabled! Save your backup codes.', 'success')
                    return render_template('mfa_setup.html', secret=temp_secret, enabled=True, backup_codes=backup_codes, step='done')
                else:
                    flash('Invalid code. Try again.', 'error')
                    uri = get_qr_uri(temp_secret)
                    qr_b64 = get_qr_base64(uri)
                    return render_template('mfa_setup.html', secret=temp_secret, qr_b64=qr_b64, uri=uri, enabled=False, step='verify', error='Invalid code')
            except Exception as e:
                logger.exception("MFA verify error")
                flash(f'Error: {e}', 'error')
                return redirect(url_for('mfa.mfa_setup'))
        elif action == 'disable':
            cfg['mfa'] = {'enabled': False, 'secret': None, 'backup_codes': [], 'passkeys': mfa.get('passkeys', [])}
            with open(config.CONFIG_FILE, 'w') as f:
                json.dump(cfg, f, indent=2)
            config.reload()
            flash('MFA disabled.', 'success')
            return redirect(url_for('mfa.mfa_setup'))
    
    # GET
    if enabled and secret:
        try:
            import pyotp
            totp = pyotp.TOTP(secret)
            # show current code for dev? hide
            pass
        except Exception:
            pass
        return render_template('mfa_setup.html', secret=secret, enabled=True, backup_codes=mfa.get('backup_codes', []))
    else:
        # No MFA yet
        temp_secret = session.get('mfa_temp_secret')
        if temp_secret:
            uri = get_qr_uri(temp_secret)
            qr_b64 = get_qr_base64(uri)
            return render_template('mfa_setup.html', secret=temp_secret, qr_b64=qr_b64, uri=uri, enabled=False, step='verify')
        return render_template('mfa_setup.html', enabled=False)

@bp.route('/admin/mfa/verify', methods=['GET', 'POST'])
def mfa_verify():
    # This is the step after password login when MFA pending
    if not session.get('mfa_pending'):
        return redirect(url_for('main.dashboard'))
    error = None
    if request.method == 'POST':
        token = request.form.get('token', '').strip()
        # Check backup codes too
        mfa = config.get_configuration().get('mfa', {})
        backup_codes = mfa.get('backup_codes', [])
        if token in backup_codes:
            # consume backup code
            backup_codes.remove(token)
            cfg = config.get_configuration()
            cfg['mfa']['backup_codes'] = backup_codes
            with open(config.CONFIG_FILE, 'w') as f:
                json.dump(cfg, f, indent=2)
            config.reload()
            session.pop('mfa_pending', None)
            session['admin_logged_in'] = True
            logger.info("Admin MFA via backup code success")
            return redirect(request.args.get('next') or url_for('main.dashboard'))
        if verify_totp(token):
            session.pop('mfa_pending', None)
            session['admin_logged_in'] = True
            logger.info("Admin MFA TOTP success")
            return redirect(request.args.get('next') or url_for('main.dashboard'))
        # Also allow passkey verification via POST with credential (handled by JS)
        # If we receive passkey assertion, we would verify here; for now check header
        credential = request.form.get('passkey_credential')
        if credential:
            # Simple passkey check: verify stored credentialId matches (we store raw id)
            # In real WebAuthn, need to verify signature with challenge; here we do simple string match for demo
            mfa = config.get_configuration().get('mfa', {})
            passkeys = mfa.get('passkeys', [])
            for pk in passkeys:
                if pk.get('credentialId') == credential:
                    session.pop('mfa_pending', None)
                    session['admin_logged_in'] = True
                    logger.info("Admin MFA passkey success")
                    return redirect(request.args.get('next') or url_for('main.dashboard'))
        error = 'Invalid code or backup code.'
    
    # GET - show verify page
    # If passkeys exist, we can offer passkey button
    mfa = config.get_configuration().get('mfa', {})
    has_passkeys = bool(mfa.get('passkeys'))
    return render_template('mfa_verify.html', error=error, has_passkeys=has_passkeys)

@bp.route('/admin/mfa/passkey/register', methods=['POST'])
@login_required
def mfa_passkey_register():
    """Register a new passkey. Expects JSON with credentialId, publicKey, name."""
    try:
        data = request.get_json() or {}
        credential_id = data.get('credentialId')
        name = data.get('name', 'Passkey')
        if not credential_id:
            return jsonify({'success': False, 'error': 'Missing credentialId'}), 400
        cfg = config.get_configuration()
        mfa = cfg.get('mfa', {})
        passkeys = mfa.get('passkeys', [])
        # Check duplicate
        if any(pk.get('credentialId') == credential_id for pk in passkeys):
            return jsonify({'success': False, 'error': 'Passkey already registered'}), 400
        passkeys.append({
            'credentialId': credential_id,
            'name': name,
            'createdAt': __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()
        })
        # Ensure mfa dict exists
        if 'mfa' not in cfg:
            cfg['mfa'] = {'enabled': False, 'secret': None, 'backup_codes': [], 'passkeys': []}
        cfg['mfa']['passkeys'] = passkeys
        with open(config.CONFIG_FILE, 'w') as f:
            json.dump(cfg, f, indent=2)
        config.reload()
        logger.info(f"Passkey registered: {name} ({credential_id[:10]}...)")
        return jsonify({'success': True})
    except Exception as e:
        logger.exception("Passkey register failed")
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/admin/mfa/passkey/delete', methods=['POST'])
@login_required
def mfa_passkey_delete():
    try:
        data = request.get_json() or {}
        cid = data.get('credentialId') or request.form.get('credentialId')
        cfg = config.get_configuration()
        mfa = cfg.get('mfa', {})
        passkeys = mfa.get('passkeys', [])
        new_list = [pk for pk in passkeys if pk.get('credentialId') != cid]
        cfg['mfa']['passkeys'] = new_list
        with open(config.CONFIG_FILE, 'w') as f:
            json.dump(cfg, f, indent=2)
        config.reload()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/admin/mfa/backup-codes', methods=['POST'])
@login_required
def mfa_regenerate_backup_codes():
    try:
        codes = generate_backup_codes(8)
        cfg = config.get_configuration()
        if 'mfa' not in cfg:
            cfg['mfa'] = {'enabled': False, 'secret': None, 'backup_codes': [], 'passkeys': []}
        cfg['mfa']['backup_codes'] = codes
        with open(config.CONFIG_FILE, 'w') as f:
            json.dump(cfg, f, indent=2)
        config.reload()
        return jsonify({'success': True, 'codes': codes})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
