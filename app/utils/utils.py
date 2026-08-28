import json
import os
import re
import time
from datetime import datetime, timezone
import logging # Import logging
from model import db
from model.upstream_check_log import UpstreamCheckLog
from model.upstream_cache import UpstreamCache
from model.redirect import Redirect
from app import CONSTANTS  # Import CONSTANTS for data source strings


# Get a logger instance for this module
logger = logging.getLogger(__name__)

from ..config import config
def get_db_uri():
    cfg=config.get_configuration()
    if "database" in cfg:
        db_url=cfg["database"]
        return db_url
    default_db_uri = "sqlite:///" + os.path.join(config.DATA_DIR, "redirect.db")
    logger.warning(f"Database URI not found in config, defaulting to {default_db_uri}")
    return default_db_uri

def _save_config():
    try:
        with open(config.CONFIG_FILE, 'w') as f:
            # Save config sorted by keys
            json.dump(config.get_configuration(), f, indent=2, sort_keys=True)
        config.logger.debug(f"✅ Configuration saved (sorted) to {config.CONFIG_FILE}")
    except IOError as e:
        config.logger.error(f"❌ Failed to save configuration file {config.CONFIG_FILE}: {e}")



def get_config(key, default=None):
    cfg = config.get_configuration()
    if key in cfg:
        return cfg[key]
    # Set and return default if not present
    cfg[key] = default
    _save_config()
    logger.info(f"Config key '{key}' not found, set to default: {default}")
    return default

def set_config(key, value):
    cfg = config.get_configuration()
    cfg[key] = value
    _save_config()
    logger.info(f"Config key '{key}' set to '{value}'")





def get_admin_password():
    pwd = get_config('admin_password')
    if pwd:
        return pwd
    import secrets
    import string
    pwd = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(12))
    set_config('admin_password', pwd)
    logger.warning("Admin password was not set in config. Generated a new one.")
    return pwd

def get_port():
    port = get_config('port')
    if port:
        return port
    set_config('port', 80)
    logger.info("Port not set in config, defaulting to 80.")
    return 80

def get_auto_redirect_delay():
    delay = get_config('auto_redirect_delay')
    if delay is not None:
        return delay
    set_config('auto_redirect_delay', 0)
    logger.info("Auto redirect delay not set, defaulting to 0.")
    return 0

def get_delete_requires_password():
    val = get_config('delete_requires_password')
    if val is not None:
        return val
    set_config('delete_requires_password', True)
    logger.info("Delete requires password not set, defaulting to True.")
    return True

# --- Access count helpers ---
def increment_access_count(pattern):
    redirect_obj = Redirect.query.filter_by(pattern=pattern).first()
    if redirect_obj:
        redirect_obj.access_count = (redirect_obj.access_count or 0) + 1
        db.session.commit()
        logger.info(f"Access count incremented for shortcut '{pattern}'. New count: {redirect_obj.access_count}")
    else:
        logger.warning(f"Attempted to increment access count for non-existent shortcut: '{pattern}'")


def get_access_count(pattern):
    redirect_obj = Redirect.query.filter_by(pattern=pattern).first()
    count = redirect_obj.access_count if redirect_obj else 0
    logger.debug(f"Retrieved access count for '{pattern}': {count}")
    return count

# Helper to get created/updated times for UI
def get_created_updated(pattern):
    redirect_obj = Redirect.query.filter_by(pattern=pattern).first()
    if redirect_obj:
        logger.debug(f"Retrieved audit info for '{pattern}'.")
        return redirect_obj.created_at, redirect_obj.updated_at
    logger.warning(f"Could not retrieve audit info for non-existent shortcut: '{pattern}'")
    return None, None



def log_upstream_check(pattern: str, upstream_name: str, check_url: str,
                       result: str, detail: str, cached: bool = False):
    """
    Logs an upstream check result. If an entry for the given pattern and upstream_name
    already exists, it updates the existing entry (including incrementing its count);
    otherwise, it creates a new log entry.

    Args:
        pattern (str): The shortcut pattern that was checked.
        upstream_name (str): The name of the upstream service is checked.
        check_url (str): The specific URL tried for the check.
        result (str): The outcome of the check (e.g., 'success', 'fail', 'exception').
        detail (str): Detailed information about the check, often including status codes or errors.
        cached (bool): True if the result was served from cache, False otherwise.
    """
    try:
        # This call assumes UpstreamCheckLog.upsert_log is correctly implemented
        # to handle the ON CONFLICT DO UPDATE logic, passing all required arguments.
        UpstreamCheckLog.upsert_log(
            pattern=pattern,
            upstream_name=upstream_name,
            check_url=check_url,
            result=result,
            detail=detail,
            # 'tried_at' is not passed here because upsert_log now generates
            # the current timestamp internally for consistency during UPSERT.
            cached=cached
        )
        db.session.commit() # Commit the transaction to save changes to the database
        logger.info(f"Upstream check logged/updated: pattern='{pattern}', upstream='{upstream_name}', result='{result}'.")
    except Exception as e:
        db.session.rollback() # Rollback the session if any error occurs
        logger.exception(f"Failed to log/update upstream check for '{pattern}' in '{upstream_name}'. Rolled back transaction. Error: {e}")
        raise # Re-raise the exception to propagate it up the call stack for proper error handling

def get_upstream_logs():
    """
    Retrieves upstream check logs, orders them, and converts them
    to a list of dictionaries for easier template rendering.
    Parses 'detail' string to extract status_code and actual_url.
    """

    logs = UpstreamCheckLog.query.order_by(UpstreamCheckLog.id.desc()).all()
    logger.debug(f"Retrieved {len(logs)} upstream check logs.")

    # Convert SQLAlchemy objects to dictionaries, processing 'detail' field
    processed_logs = []
    for log_entry in logs:
        # Prepare common fields
        log_dict = {
            'time': log_entry.tried_at,  # Using tried_at directly
            'shortcut': log_entry.pattern,
            'upstream': log_entry.upstream_name,
            'result': log_entry.result,
            'details': log_entry.detail,  # Keep raw detail for full display
            'cache_info': log_entry.cached  # This will be True/False
        }

        # --- Parse status_code and actual_url from 'detail' string ---
        # This parsing logic now happens in Python, making the template simpler
        status_code = '-'
        actual_url = '-'
        exception_msg = ''

        details_str = log_entry.detail or ''
        is_exception = (log_entry.result or '').lower() == 'exception'

        if 'status_code=' in details_str:
            match = re.search(r'status_code=(\d+)', details_str)
            if match:
                status_code = match.group(1)

        if 'actual_url=' in details_str:
            # Matches actual_url= then captures everything until the next comma or end of string
            match = re.search(r'actual_url=([^,]+)', details_str)
            if match:
                actual_url = match.group(1).strip()

        if is_exception:
            exception_msg = details_str  # If it's an exception, the detail IS the message

        log_dict['status_code'] = status_code
        log_dict['actual_url'] = actual_url
        log_dict['exception_msg'] = exception_msg  # Add for direct use in template

        processed_logs.append(log_dict)

    return processed_logs





def redis_get(key):
    if config.redis_enabled and config.redis_client:
        try:
            value = config.redis_client.get(key)
            logger.debug(f"Redis GET '{key}': {'HIT' if value else 'MISS'}")
            return value
        except Exception as e:
            logger.error(f"Redis GET failed for key '{key}': {e}")
            return None
    logger.debug(f"Redis GET '{key}': Skipped (Redis disabled/not connected).")
    return None

def redis_set(key, value, ex=None): # Added optional expiry 'ex'
    if config.redis_enabled and config.redis_client:
        try:
            config.redis_client.set(key, value, ex=ex)
            logger.debug(f"Redis SET '{key}' successfully.")
        except Exception as e:
            logger.error(f"Redis SET failed for key '{key}': {e}")
            pass
    else:
        logger.debug(f"Redis SET '{key}': Skipped (Redis disabled/not connected).")

def redis_delete(key):
    if config.redis_enabled and config.redis_client:
        try:
            config.redis_client.delete(key)
            logger.debug(f"Redis DELETE '{key}' successfully.")
        except Exception as e:
            logger.error(f"Redis DELETE failed for key '{key}': {e}")
            pass
    else:
        logger.debug(f"Redis DELETE '{key}': Skipped (Redis disabled/not connected).")


def get_shortcut(pattern):
    start_time = time.time()
    source = CONSTANTS.data_source_redis # Default source assumption

    if config.redis_enabled:
        val = redis_get(f"shortcut:{pattern}")
        if val:
            try:
                shortcut = json.loads(val)
                # Ensure data_type is present for consistency, if not already
                if 'data_type' not in shortcut:
                    shortcut['data_type'] = shortcut.get('type', CONSTANTS.DATA_TYPE_STATIC)
                # SSO URLs are never served from Redis cache - force DB lookup for freshness
                if is_sso_url(shortcut.get('target', '')):
                    logger.info(f"SSO target in Redis for '{pattern}' - purging Redis entry (SSO never cached)")
                    redis_delete(f"shortcut:{pattern}")
                else:
                    logger.debug(f"Shortcut '{pattern}' HIT from Redis. Source: {source}")
                    return shortcut, source, round(time.time() - start_time, 6)
            except json.JSONDecodeError as e:
                logger.error(f"JSON decode error from Redis for shortcut:{pattern}: {e}. Deleting corrupt entry.")
                redis_delete(f"shortcut:{pattern}") # If cached data is corrupt, delete it
            except Exception as e:
                logger.exception(f"Unexpected error processing Redis shortcut:{pattern}. Deleting entry.")
                redis_delete(f"shortcut:{pattern}") # Delete if other unexpected error

    # Fallback to DB (and hydrate Redis if enabled)
    source = CONSTANTS.data_source_redirect
    redirect_obj = Redirect.query.filter_by(pattern=pattern).first()
    if redirect_obj:
        shortcut = {
            'pattern': redirect_obj.pattern,
            'type': redirect_obj.type,
            'target': redirect_obj.target,
            'access_count': redirect_obj.access_count if redirect_obj.access_count is not None else 0,
            'created_at': redirect_obj.created_at,
            'updated_at': redirect_obj.updated_at,
            'data_type': redirect_obj.type,
            'tags': getattr(redirect_obj, 'tags', None),
            'visibility': getattr(redirect_obj, 'visibility', 'public'),
            'expires_at': getattr(redirect_obj, 'expires_at', None),
            'owner_email': getattr(redirect_obj, 'owner_email', None)
        }
        # Hydrate Redis - NEVER cache SSO targets (avoid caching auth redirects)
        if config.redis_enabled:
            if is_sso_url(shortcut.get('target', '')):
                logger.info(f"SSO target detected for '{pattern}' - not caching in Redis (SSO never cached)")
            else:
                try:
                    redis_set(f"shortcut:{pattern}", json.dumps(shortcut))
                    logger.debug(f"Shortcut '{pattern}' MISS from Redis, HIT from DB. Hydrated Redis.")
                except Exception as e:
                    logger.error(f"Failed to hydrate Redis with shortcut:{pattern}: {e}")
        else:
            logger.debug(f"Shortcut '{pattern}' HIT from DB (Redis disabled).")
        return shortcut, source, round(time.time() - start_time, 6)

    # Check upstream DB cache (and hydrate Redis if enabled)
    source = CONSTANTS.data_source_upstream
    if is_upstream_cache_enabled():
        cached_upstream_result = get_cached_upstream_result_from_db(pattern=pattern)
        if cached_upstream_result:
            # Add data_type to cached result for consistency with local shortcuts
            cached_upstream_result['data_type'] = CONSTANTS.DATA_TYPE_STATIC
            # Hydrate Redis with the upstream cache result (already handled by get_cached_upstream_result_from_db)
            logger.debug(f"Upstream shortcut '{pattern}' HIT from cache (Redis/DB).")
            return cached_upstream_result, source, round(time.time() - start_time, 6)
        logger.debug(f"Upstream shortcut '{pattern}' not found in cache.")

    logger.info(f"Shortcut '{pattern}' not found in local DB or upstream cache.")
    return None, None, round(time.time() - start_time, 6)

def set_shortcut(pattern, type_, target, created_at=None, updated_at=None, created_ip=None, updated_ip=None, tags=None, visibility=None, expires_at=None, owner_email=None):
    redirect_obj = Redirect.query.filter_by(pattern=pattern).first()
    if redirect_obj:
        # Update existing shortcut
        redirect_obj.type = type_
        redirect_obj.target = target
        redirect_obj.updated_at = updated_at or datetime.now(timezone.utc).isoformat(sep=' ', timespec='seconds')
        redirect_obj.updated_ip = updated_ip
        if tags is not None:
            redirect_obj.tags = tags
        if visibility is not None:
            redirect_obj.visibility = visibility
        if expires_at is not None:
            redirect_obj.expires_at = expires_at
        if owner_email is not None:
            redirect_obj.owner_email = owner_email
        logger.info(f"Updated existing shortcut: '{pattern}'")
    else:
        # Create new shortcut
        new_shortcut = Redirect(
            pattern=pattern,
            type=type_,
            target=target,
            access_count=0,
            created_at=created_at or datetime.now(timezone.utc).isoformat(sep=' ', timespec='seconds'),
            updated_at=updated_at or datetime.now(timezone.utc).isoformat(sep=' ', timespec='seconds'),
            created_ip=created_ip,
            updated_ip=updated_ip,
            tags=tags,
            visibility=visibility or 'public',
            expires_at=expires_at,
            owner_email=owner_email
        )
        db.session.add(new_shortcut)
        logger.info(f"Created new shortcut: '{pattern}'")

    try:
        db.session.commit()
        logger.debug(f"DB commit successful for shortcut '{pattern}'.")
        # Invalidate (or re-set) Redis cache for this shortcut after update/set - NEVER cache SSO
        if config.redis_enabled:
            # Fetch the updated shortcut from DB to ensure consistency before caching
            updated_shortcut = Redirect.query.filter_by(pattern=pattern).first()
            if updated_shortcut:
                if is_sso_url(updated_shortcut.target):
                    # Ensure any stale Redis entry is deleted for SSO
                    redis_delete(f"shortcut:{pattern}")
                    logger.info(f"SSO target for '{pattern}' - cleared Redis cache (SSO never cached)")
                else:
                    shortcut_data = {
                        'pattern': updated_shortcut.pattern,
                        'type': updated_shortcut.type,
                        'target': updated_shortcut.target,
                        'access_count': updated_shortcut.access_count if updated_shortcut.access_count is not None else 0,
                        'created_at': updated_shortcut.created_at,
                        'updated_at': updated_shortcut.updated_at,
                        'data_type': updated_shortcut.type
                    }
                    redis_set(f"shortcut:{pattern}", json.dumps(shortcut_data))
                    logger.debug(f"Redis cache updated for shortcut '{pattern}'.")
        else:
            logger.debug(f"Redis cache not updated for '{pattern}' (Redis disabled).")
    except Exception as e:
        db.session.rollback()
        logger.exception(f"Failed to set/update shortcut '{pattern}' in DB. Rolled back transaction.")
        raise # Re-raise the exception after logging for proper error handling upstream


# --- Upstream Cache helpers ---
def init_upstream_cache_table(db_session):
    logger.debug("Skipping init_upstream_cache_table (handled by Flask-Migrate).")
    pass


def is_upstream_cache_enabled():
    cfg = config.get_configuration()
    enabled = cfg.get('upstream_cache', {}).get('enabled', True)
    logger.debug(f"Upstream cache enabled status: {enabled}")
    return enabled

def cache_upstream_result(pattern: str, upstream_name: str, resolved_url: str, checked_at: str = None):
    """
    Caches the resolved URL for an upstream check result in the database and optionally in Redis.
    SSO URLs are NEVER cached - this function will no-op if resolved_url is SSO.

    Args:
        pattern (str): The shortcut pattern.
        upstream_name (str): The name of the upstream service.
        resolved_url (str): The URL resolved from the upstream check.
        checked_at: Optional ISO timestamp; if not provided, uses current time.
    """
    if is_sso_url(resolved_url):
        logger.warning(f"Blocked caching of SSO URL for '{pattern}' in '{upstream_name}': {resolved_url} - SSO never cached")
        return
    current_time_iso = checked_at or datetime.now(timezone.utc).isoformat()

    cache_entry = UpstreamCache.query.filter_by(
        pattern=pattern,
        upstream_name=upstream_name
    ).first()

    if cache_entry:
        cache_entry.resolved_url = resolved_url
        cache_entry.checked_at = current_time_iso
        logger.info(f"Updated upstream cache for '{pattern}' in '{upstream_name}'.")
    else:
        new_cache_entry = UpstreamCache(
            pattern=pattern,
            upstream_name=upstream_name,
            resolved_url=resolved_url,
            checked_at=current_time_iso
        )
        db.session.add(new_cache_entry)
        logger.info(f"Created new upstream cache entry for '{pattern}' in '{upstream_name}'.")

    try:
        db.session.commit()
        logger.debug(f"DB commit successful for upstream cache '{pattern}'.")

        # update redis is enabled
        if  config.redis_enabled:
            # Prepare data for Redis cache (using the same fields as the DB entry)
            redis_data = {
                'pattern': pattern,
                'upstream_name': upstream_name,
                'resolved_url': resolved_url,
                'checked_at': current_time_iso # Ensure Redis gets the same timestamp
            }
            # Store in Redis under both keys for compatibility
            redis_set(f"upstream_cache:{pattern}", json.dumps(redis_data))
            redis_set(f"upstream_cache:{pattern}:{upstream_name}", json.dumps(redis_data))
            logger.debug(f"Redis cache updated for upstream_cache:'{pattern}' and upstream_cache:'{pattern}:{upstream_name}'.")

    except Exception as e:
        db.session.rollback()
        logger.exception(f"Failed to cache upstream result for '{pattern}' in '{upstream_name}'. Rolled back transaction.")
        raise # Re-raise the exception after logging and rollback


def get_cached_upstream_result(pattern):
    # This function primarily attempts to get from Redis,
    # then calls the DB specific one if not found in Redis,
    # which in turn hydrates Redis.
    if config.redis_enabled:
        val = redis_get(f"upstream_cache:{pattern}")
        if val:
            try:
                result = json.loads(val)
                if is_sso_url(result.get('resolved_url', '')):
                    logger.warning(f"SSO upstream cache hit in Redis for '{pattern}' - purging (SSO never cached)")
                    redis_delete(f"upstream_cache:{pattern}")
                else:
                    logger.debug(f"Upstream cache HIT from Redis for '{pattern}'.")
                    return result
            except json.JSONDecodeError as e:
                logger.error(f"JSON decode error from Redis for upstream_cache:{pattern}: {e}. Deleting corrupt entry.")
                redis_delete(f"upstream_cache:{pattern}") # Delete corrupt entry
            except Exception as e:
                logger.exception(f"Unexpected error processing Redis upstream_cache:{pattern}. Deleting entry.")
                redis_delete(f"upstream_cache:{pattern}")
    # If not in Redis or error, get from DB, which will then hydrate Redis
    logger.debug(f"Upstream cache MISS from Redis for '{pattern}', checking DB.")
    return get_cached_upstream_result_from_db(pattern)

def get_cached_upstream_result_from_db(pattern):
    # This private helper explicitly gets from DB and hydrates Redis.
    cache_entry = UpstreamCache.query.filter_by(pattern=pattern).first()
    if cache_entry:
        # SSO URLs in DB cache are stale/invalid - purge them immediately
        if is_sso_url(cache_entry.resolved_url):
            logger.warning(f"Purging SSO upstream cache entry for '{pattern}' (was {cache_entry.resolved_url}) - SSO never cached")
            try:
                db.session.delete(cache_entry)
                db.session.commit()
                if config.redis_enabled:
                    redis_delete(f"upstream_cache:{pattern}")
            except Exception as e:
                db.session.rollback()
                logger.error(f"Failed to purge SSO cache for '{pattern}': {e}")
            return None
        result = {
            'pattern': cache_entry.pattern,
            'upstream_name': cache_entry.upstream_name,
            'resolved_url': cache_entry.resolved_url,
            'checked_at': cache_entry.checked_at
        }
        # Hydrate Redis - but never hydrate SSO
        if config.redis_enabled:
            if is_sso_url(result['resolved_url']):
                logger.info(f"SSO upstream cache for '{pattern}' not hydrating Redis")
            else:
                try:
                    redis_set(f"upstream_cache:{pattern}", json.dumps(result))
                    logger.debug(f"Upstream cache HIT from DB for '{pattern}'. Hydrated Redis.")
                except Exception as e:
                    logger.error(f"Failed to hydrate Redis with upstream_cache:{pattern}: {e}")
        else:
            logger.debug(f"Upstream cache HIT from DB for '{pattern}' (Redis disabled).")
        return result
    logger.debug(f"Upstream cache not found in DB for '{pattern}'.")
    return None

def list_upstream_cache(upstream_name):
    cached_entries = UpstreamCache.query.filter_by(upstream_name=upstream_name).order_by(UpstreamCache.checked_at.desc()).all()
    logger.debug(f"Listed {len(cached_entries)} upstream cache entries for '{upstream_name}'.")
    # No Redis hydration here, as this is a list retrieval, not a single key lookup
    return [
        {'pattern': entry.pattern, 'resolved_url': entry.resolved_url, 'checked_at': entry.checked_at}
        for entry in cached_entries
    ]

def clear_upstream_cache(pattern, upstream_name=None):
    # Delete from DB
    if upstream_name:
        num_deleted = UpstreamCache.query.filter_by(pattern=pattern, upstream_name=upstream_name).delete()
    else:
        num_deleted = UpstreamCache.query.filter_by(pattern=pattern).delete()
    db.session.commit()
    logger.info(f"Cleared {num_deleted} upstream cache entries from DB for '{pattern}'{f' in {upstream_name}' if upstream_name else ''}.")
    # Delete from Redis
    if config.redis_enabled:
        try:
            redis_delete(f"upstream_cache:{pattern}")
            if upstream_name:
                redis_delete(f"upstream_cache:{pattern}:{upstream_name}")
            else:
                # Remove all possible upstream-specific keys for this pattern
                # (Optional: if you want to be thorough, scan for all matching keys)
                pass
            logger.debug(f"Cleared upstream cache entry from Redis for '{pattern}' and '{pattern}:{upstream_name}'.")
        except Exception as e:
            logger.error(f"Redis DELETE failed for upstream_cache:{pattern}: {e}")

def purge_sso_upstream_cache():
    """Purge all SSO URLs from upstream cache (DB + Redis). Called on startup or admin action."""
    try:
        all_entries = UpstreamCache.query.all()
        purged = 0
        for entry in all_entries:
            if is_sso_url(entry.resolved_url):
                logger.info(f"Purging SSO cache entry: {entry.pattern} -> {entry.resolved_url}")
                db.session.delete(entry)
                purged += 1
                if config.redis_enabled and config.redis_client:
                    try:
                        config.redis_client.delete(f"upstream_cache:{entry.pattern}")
                        config.redis_client.delete(f"upstream_cache:{entry.pattern}:{entry.upstream_name}")
                    except Exception:
                        pass
        if purged:
            db.session.commit()
            logger.info(f"Purged {purged} SSO upstream cache entries (SSO never cached)")
        else:
            logger.debug("No SSO upstream cache entries to purge")
        # Also purge Redis shortcut cache for SSO targets
        if config.redis_enabled and config.redis_client:
            try:
                keys = config.redis_client.keys('shortcut:*')
                for k in keys or []:
                    val = config.redis_client.get(k)
                    if val:
                        try:
                            data = json.loads(val)
                            if is_sso_url(data.get('target', '')):
                                config.redis_client.delete(k)
                                logger.info(f"Purged SSO shortcut Redis cache: {k}")
                        except Exception:
                            continue
                # Purge upstream_cache:* Redis keys that are SSO
                up_keys = config.redis_client.keys('upstream_cache:*')
                for k in up_keys or []:
                    val = config.redis_client.get(k)
                    if val:
                        try:
                            data = json.loads(val)
                            if is_sso_url(data.get('resolved_url', '')):
                                config.redis_client.delete(k)
                                logger.info(f"Purged SSO upstream Redis cache: {k}")
                        except Exception:
                            continue
            except Exception as e:
                logger.error(f"Failed to purge SSO Redis caches: {e}")
        return purged
    except Exception as e:
        logger.exception(f"Failed to purge SSO cache: {e}")
        try:
            db.session.rollback()
        except Exception:
            pass
        return 0



def get_db():
    logger.debug("Returning SQLAlchemy DB instance.")
    return db

# GET/POST: Delete shortcut. Triggered when user visits /delete/<subpath> or submits delete confirmation.
def deleteShortCut(pattern):
    redirect_obj = Redirect.query.filter_by(pattern=pattern).first()
    if redirect_obj:
        db.session.delete(redirect_obj)
        try:
            db.session.commit()
            logger.info(f"Deleted shortcut: '{pattern}'")
            # Invalidate Redis cache for this shortcut
            if config.redis_enabled:
                redis_delete(f"shortcut:{pattern}")
        except Exception as e:
            db.session.rollback()
            logger.exception(f"Failed to delete shortcut '{pattern}'. Rolled back transaction.")
            raise # Re-raise after logging
    else:
        logger.warning(f"Attempted to delete non-existent shortcut: '{pattern}'")


# check redirect db and return true or false if any pattern exist:
def isPatternExists(subpath):
    exists = Redirect.query.filter_by(pattern=subpath).first() is not None
    logger.debug(f"Checking if shortcut '{subpath}' exists: {exists}")
    return exists


def import_redirects_from_json(json_data):
    """
    Imports redirect data from a JSON list.
    Upserts (inserts or updates) each redirect by pattern. If a redirect exists, only update if the imported 'updated_at' is newer.
    Does NOT delete existing redirects.
    Clears Redis cache after import.

    Args:
        json_data (list): A list of dictionaries, each representing a redirect.

    Returns:
        dict: A dictionary with 'success' (bool), 'message' (str), and 'imported_count' (int, optional).
    """
    try:
        if not isinstance(json_data, list):
            logger.error("Import failed: JSON data is not a list.")
            return {'success': False, 'message': 'Invalid JSON data format: expected a list of redirects.'}

        imported_count = 0
        for entry in json_data:
            if not entry.get('pattern') or not entry.get('target'):
                logger.warning(f"Skipping malformed entry during import: {entry}")
                continue
            pattern = entry.get('pattern')
            imported_updated_at = entry.get('updated_at')
            existing = Redirect.query.filter_by(pattern=pattern).first()
            if existing:
                # Only update if imported updated_at is newer
                try:
                    existing_dt = datetime.strptime(existing.updated_at, '%Y-%m-%d %H:%M:%S')
                except Exception:
                    existing_dt = None
                try:
                    imported_dt = datetime.strptime(imported_updated_at, '%Y-%m-%d %H:%M:%S') if imported_updated_at else None
                except Exception:
                    imported_dt = None
                if imported_dt and (not existing_dt or imported_dt > existing_dt):
                    existing.type = entry.get('type', CONSTANTS.DATA_TYPE_STATIC)
                    existing.target = entry.get('target')
                    existing.access_count = entry.get('access_count', existing.access_count)
                    existing.created_at = entry.get('created_at', existing.created_at)
                    existing.updated_at = imported_updated_at
                    existing.created_ip = entry.get('created_ip', existing.created_ip)
                    existing.updated_ip = entry.get('updated_ip', existing.updated_ip)
                    imported_count += 1
            else:
                new_redirect = Redirect(
                    pattern=pattern,
                    type=entry.get('type', CONSTANTS.DATA_TYPE_STATIC),
                    target=entry.get('target'),
                    access_count=entry.get('access_count', 0),
                    created_at=entry.get('created_at', datetime.now(timezone.utc).isoformat(sep=' ', timespec='seconds')),
                    updated_at=entry.get('updated_at', datetime.now(timezone.utc).isoformat(sep=' ', timespec='seconds')),
                    created_ip=entry.get('created_ip', 'import'),
                    updated_ip=entry.get('updated_ip', 'import')
                )
                db.session.add(new_redirect)
                imported_count += 1
        db.session.commit()
        logger.info(f"Imported or updated {imported_count} redirects successfully into DB.")

        # Clear Redis cache if enabled
        if config.redis_enabled:
            if config.redis_client:
                try:
                    keys_to_delete = config.redis_client.keys('shortcut:*')
                    if keys_to_delete:
                        config.redis_client.delete(*keys_to_delete)
                        logger.info(f"Cleared {len(keys_to_delete)} shortcut caches from Redis after import.")
                    else:
                        logger.debug("No shortcut keys found in Redis to clear after import.")
                except Exception as e:
                    logger.error(f"Failed to clear Redis shortcut cache after import: {e}")
            else:
                logger.warning("Redis client not available, skipped clearing shortcut cache after import.")
        else:
            logger.debug("Redis is disabled, skipped clearing shortcut cache after import.")

        return {'success': True, 'message': f'Redirect data imported successfully. {imported_count} records imported or updated.',
                'imported_count': imported_count}

    except (json.JSONDecodeError, ValueError) as e:
        db.session.rollback()
        logger.error(f"Import failed due to JSON/ValueError: {e}")
        return {'success': False, 'message': f'Import failed: Invalid JSON file or data format: {e}'}
    except Exception as e:
        db.session.rollback()
        logger.exception(f"Unexpected error during redirect import.")
        return {'success': False, 'message': f'Import failed: An unexpected error occurred: {e}'}



def destructureSubPath(subPath: str) -> tuple[str, list[str]]:
    """
    Destructures a URL subpath into a base pattern and a list of dynamic properties.

    Examples:
    - "raj"        -> ("raj", [])
    - "json/1"     -> ("json", ["1"])
    - "json/1/2"   -> ("json", ["1", "2"])
    - "/foo/bar "  -> ("foo", ["bar"]) (only first segment is pattern, rest are props)
    Legacy behavior - only first segment is considered pattern.
    For hierarchical patterns (e.g., "x/abc", "y/h/k"), use get_shortcut_for_path() instead.

    Args:
        subPath: The raw subpath string from the URL (e.g., from Flask's <path:subpath>).

    Returns:
        A tuple containing:
        - The base pattern string (e.g., "json", "raj").
        - A list of strings representing the dynamic properties (e.g., ["1"], ["1", "2"]).
    """
    logger.debug(f"Destructuring subpath: '{subPath}'")
    if subPath is None:
        return "", []
    if not isinstance(subPath, str):
        subPath = str(subPath)
    sanitized_subpath = subPath.strip().lower()
    if sanitized_subpath.startswith('/'):
        sanitized_subpath = sanitized_subpath[1:]
    sanitized_subpath = sanitized_subpath.strip('/')
    if not sanitized_subpath:
        return "", []
    segments = sanitized_subpath.split('/')
    pattern = segments[0]
    props = segments[1:] if len(segments) > 1 else []
    return pattern, props


def get_shortcut_for_path(subpath: str):
    """
    Hierarchical shortcut resolver - supports patterns containing slashes.
    E.g., allows shortcuts like "x/abc", "x/def", "y/h/k" as distinct patterns.

    Attempts longest-prefix match for the given subpath.
    For static shortcuts, only exact matches (no remaining props) are considered.
    For dynamic/user-dynamic, remaining segments are treated as dynamic values.
    Returns (shortcut, data_source, resp_time, matched_pattern, remaining_props)
    """
    start_time = time.time()
    if subpath is None:
        return None, None, round(time.time() - start_time, 6), "", []
    if not isinstance(subpath, str):
        subpath = str(subpath)
    sanitized = subpath.strip().lower().strip('/')
    if not sanitized:
        return None, None, round(time.time() - start_time, 6), "", []
    segments = sanitized.split('/')
    # Try longest prefix first (hierarchical)
    for i in range(len(segments), 0, -1):
        candidate = "/".join(segments[:i])
        # Use get_shortcut for each candidate (handles Redis/DB/upstream)
        shortcut, source, _ = get_shortcut(candidate)
        if shortcut:
            remaining = segments[i:]
            shortcut_type = shortcut.get('type') or shortcut.get('data_type')
            # Static shortcuts must be exact match (no extra path)
            if shortcut_type == CONSTANTS.DATA_TYPE_STATIC and remaining:
                logger.debug(f"Skipping static candidate '{candidate}' for subpath '{subpath}' due to remaining {remaining}")
                continue
            elapsed = round(time.time() - start_time, 6)
            logger.debug(f"Hierarchical match: subpath '{subpath}' -> pattern '{candidate}' with remaining {remaining}")
            return shortcut, source, elapsed, candidate, remaining
    # Fallback for legacy patterns that include placeholder in pattern name (e.g., "redir-dyn/{foo}")
    # Handles tests where pattern was stored as "redir-dyn/{foo}" but subpath is "redir-dyn/bar"
    try:
        from model.redirect import Redirect
        dynamic_types = [CONSTANTS.DATA_TYPE_DYNAMIC, CONSTANTS.DATA_TYPE_USER_DYNAMIC]
        # Use sanitized pattern for base extraction
        try:
            dynamic_redirects = Redirect.query.filter(Redirect.type.in_(dynamic_types)).all()
        except Exception:
            dynamic_redirects = []
        for r in dynamic_redirects:
            pat = r.pattern or ""
            # Extract base before any { or [
            base = pat.split("{")[0].split("[")[0].strip().rstrip("/")
            base = sanitize_pattern(base)
            if not base:
                continue
            if sanitized == base or sanitized.startswith(base + "/"):
                remaining = sanitized[len(base):].strip("/").split("/") if len(sanitized) > len(base) else []
                if remaining == [""]:
                    remaining = []
                shortcut = {
                    'pattern': r.pattern,
                    'type': r.type,
                    'target': r.target,
                    'access_count': r.access_count or 0,
                    'created_at': r.created_at,
                    'updated_at': r.updated_at,
                    'data_type': r.type
                }
                elapsed = round(time.time() - start_time, 6)
                logger.debug(f"Legacy dynamic match: subpath '{subpath}' -> stored pattern '{r.pattern}' base '{base}' remaining {remaining}")
                return shortcut, CONSTANTS.data_source_redirect, elapsed, r.pattern, remaining
    except Exception as e:
        logger.debug(f"Legacy dynamic fallback failed: {e}")
    # No hierarchical match found
    elapsed = round(time.time() - start_time, 6)
    logger.debug(f"No hierarchical match for subpath '{subpath}'")
    return None, None, elapsed, "", segments

def sanitize_pattern(pattern: str) -> str:
    """Sanitize and normalize shortcut pattern. Allows hierarchical paths like x/abc."""
    if pattern is None:
        return ""
    if not isinstance(pattern, str):
        pattern = str(pattern)
    p = pattern.strip().lower()
    # Remove leading/trailing slashes
    p = p.strip('/')
    # Collapse multiple slashes
    p = re.sub(r'/+', '/', p)
    # Validate: must not be empty, must contain only allowed chars
    return p


def replacePlaceHolders(target_string, replacement_value):
    """
    Replaces all occurrences of {placeholder} in a string with a given replacement value or list of values.
    If a list is provided, replaces each unique placeholder with the corresponding value (by order of appearance),
    and repeats the last value for any extra placeholders.
    """
    import re
    if isinstance(replacement_value, (list, tuple)):
        placeholders = re.findall(r'\{([^}]+)\}', target_string)
        unique_placeholders = []
        [unique_placeholders.append(p) for p in placeholders if p not in unique_placeholders]
        mapping = {}
        for i, name in enumerate(unique_placeholders):
            if i < len(replacement_value):
                mapping[name] = str(replacement_value[i])
            else:
                mapping[name] = str(replacement_value[-1])
        def repl(match):
            name = match.group(1)
            return mapping.get(name, match.group(0))
        return re.sub(r'\{([^}]+)\}', repl, target_string)
    else:
        return re.sub(r'\{[^}]+\}', str(replacement_value), target_string)


def get_placeholder_vars(target_string: str) -> list[str]:
    """
    Extracts a list of all placeholder variable names from a string.
    Supports both {variable_name} and [variable_name] formats.
    """
    import re
    curly = re.findall(r'\{([^}]+)\}', target_string)
    square = re.findall(r'\[([^\]]+)\]', target_string)
    return square + curly if square or curly else []


def get_upstreams():
    cfg = config.get_configuration()
    return cfg.get('upstreams', [])


def set_upstreams(upstreams):
    cfg = config.get_configuration()
    cfg['upstreams'] = upstreams
    _save_config()

# --- SSO detection for upstream caching ---
SSO_URL_PATTERNS = [
    'accounts.google.com', 'login.microsoftonline.com', 'login.microsoft.com',
    'okta.com', 'oktapreview.com', 'okta-emea.com', 'auth0.com',
    'sts.windows.net', 'login.windows.net', 'adfs', 'saml', 'oauth',
    'openid', 'signin', '/login', '/signin', '/auth', '/sso',
    'pingidentity', 'onelogin', 'accounts.', 'login.', 'auth.', 'sso.'
]

def is_sso_url(url: str) -> bool:
    """
    Detect if a URL is an SSO / login page that should NOT be cached.
    Used to avoid caching upstream hits that require authentication.
    
    Examples:
        - https://accounts.google.com/signin/v2/identifier -> SSO
        - https://company.okta.com/login/login.htm -> SSO
        - https://login.microsoftonline.com/.../oauth2/authorize -> SSO
        - https://docs.google.com/... (not SSO, but may redirect to accounts.google.com)
    """
    if not url or not isinstance(url, str):
        return False
    lower = url.lower()
    for pat in SSO_URL_PATTERNS:
        if pat.lower() in lower:
            return True
    return False

def should_cache_upstream_result(upstream: dict, resolved_url: str) -> bool:
    """
    Determine if an upstream result should be cached.
    SSO URLs are NEVER cached - regardless of per-upstream setting.
    This prevents caching login pages (accounts.google.com, okta, etc).
    """
    if not resolved_url:
        return False
    if is_sso_url(resolved_url):
        logger.warning(f"SSO URL detected - NEVER caching: {resolved_url} (upstream: {upstream.get('name')})")
        return False
    return True

def is_safe_redirect_target(url: str) -> bool:
    """Validate redirect target is safe (http/https only, no javascript/data)."""
    if not url or not isinstance(url, str):
        return False
    url = url.strip()
    lower = url.lower()
    # Block dangerous schemes
    if lower.startswith(('javascript:', 'data:', 'vbscript:', 'file:', 'blob:')):
        return False
    # Must be http/https or protocol-relative (which we treat as https)
    # Allow only http/https
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        # If no scheme, it could be relative path - block for open redirect (we want absolute http/https)
        # For shortener, we require absolute http/https
        if not parsed.scheme:
            return False
        return parsed.scheme.lower() in ('http', 'https')
    except Exception:
        return False

def sanitize_redirect_target(url: str, fallback: str = "/") -> str:
    """Return safe redirect target or fallback if unsafe."""
    if is_safe_redirect_target(url):
        return url
    logger.warning(f"Blocked unsafe redirect target: {url}")
    return fallback

def get_similar_patterns(query: str, limit: int = 3):
    """Suggest similar patterns for not-found (issue #71). Simple ilike."""
    if not query:
        return []
    try:
        from model.redirect import Redirect
        q = query.strip().lower()
        # Try prefix match first, then contains
        candidates = Redirect.query.filter(Redirect.pattern.ilike(f"%{q}%")).limit(limit*2).all()
        # Simple ranking: shortest pattern first
        candidates = sorted(candidates, key=lambda r: len(r.pattern))[:limit]
        return [{'pattern': r.pattern, 'target': r.target, 'type': r.type} for r in candidates]
    except Exception:
        return []

def is_expired(redirect_obj) -> bool:
    """Check if redirect has expired (issue #75)."""
    if not redirect_obj or not getattr(redirect_obj, 'expires_at', None):
        return False
    try:
        exp = redirect_obj.expires_at
        # Try ISO parse
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(exp.replace('Z', '+00:00')) if 'T' in exp else datetime.strptime(exp, '%Y-%m-%d %H:%M:%S')
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) > dt
    except Exception:
        return False

def is_private_visible(redirect_obj, is_admin: bool) -> bool:
    """Check visibility ACL (issue #74): private requires admin."""
    vis = getattr(redirect_obj, 'visibility', 'public') or 'public'
    if vis == 'private' and not is_admin:
        return False
    if vis == 'team' and not is_admin:
        # For now, team requires admin (future: check team membership)
        return False
    return True