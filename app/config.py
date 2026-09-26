import json
import os
import logging
import secrets
import string
import redis

# Must come before any logging call: a non-UTF-8 console otherwise raises
# UnicodeEncodeError on the first emoji and aborts the process on import.
from .utils.console import force_utf8_streams

force_utf8_streams()

from .utils.paths import (
    PORTABLE_DB_SUFFIX,
    SQLITE_PREFIX,
    portable_uri_for_path,
    resolve_data_dir,
    resolve_database_uri,
    sqlite_path_from_uri,
)


class Config:
    """Handles application configurations."""

    def __init__(self):
        """Initialize configuration settings."""
        # First setup paths to access config
        self.setup_logging("INFO")  # Set default logging level to DEBUG
        self.PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        # Single source of truth for persistent state. Honours REDIRECTOR_DATA_DIR
        # so Docker, systemd, launchd, Task Scheduler and tests can each point
        # the whole install somewhere else without touching code.
        self.DATA_DIR = resolve_data_dir()
        self.CONFIG_FILE = os.path.join(self.DATA_DIR, 'redirect.config.json')
        self.CONFIG_BACKUP_FILE = os.path.join(self.DATA_DIR, 'redirect.config.json.bak')
        # Detect Docker environment
        self.RUNNING_IN_DOCKER = os.path.exists('/.dockerenv') or os.getenv('DOCKER_CONTAINER') is not None
        self.start_mode = "Gunicorn MODE"
        # Load basic config (used for logging level)
        temp_cfg = self.load_raw_config()
        self.ensure_config_defaults(temp_cfg)
        self.setup_logging(temp_cfg.get("log_level", "INFO"))



        # Now reload config fully (including Redis, etc.)
        self.logger.debug(f"Config file path: {self.CONFIG_FILE}")
        self.cfg = temp_cfg

        # Repair a database path that points at a location this host does not
        # have (moved repo, new machine, different mount point).
        self.repair_database_path()

        # Redis configuration
        self.redis_cfg = self.cfg.get('redis', {})
        self.redis_enabled = self.redis_cfg.get('enabled', False)
        self.redis_host = self.redis_cfg.get('host', 'redis')
        try:
            self.redis_port = int(self.redis_cfg.get('port', 6379))
        except ValueError:
            self.logger.error("Invalid Redis port in config, defaulting to 6379.")
            self.redis_port = 6379
        self.redis_client = None
        # repair_database_path() already set both the portable stored value
        # (cfg['database']) and the absolute resolved_database used at runtime.

    def get_configuration(self):
        return self.cfg

    def setup_logging(self, log_level_str):
        """Set up logging to print logs to the console with dynamic level."""
        level = getattr(logging, log_level_str.upper(), logging.DEBUG)
        logging.basicConfig(
            format="%(asctime)s - %(levelname)s - %(message)s",
            level=level
        )
        self.logger = logging.getLogger(__name__)
        self.logger.debug(f"Logging initialized with level: {log_level_str.upper()}")

    def _atomic_write_json(self, filepath, data):
        """Write JSON atomically using temporary file to prevent corruption on crash or container termination."""
        temp_file = f"{filepath}.tmp.{os.getpid()}"
        try:
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_file, filepath)
            # Create a backup copy for disaster recovery
            if filepath == self.CONFIG_FILE:
                try:
                    import shutil
                    shutil.copy2(filepath, self.CONFIG_BACKUP_FILE)
                except Exception:
                    pass
            return True
        except Exception as e:
            if hasattr(self, 'logger'):
                self.logger.error(f"❌ Atomic write failed for {filepath}: {e}")
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except Exception:
                    pass
            raise

    def save(self):
        """Persist the in-memory config through the atomic writer.

        Every code path that changes the config must go through here (or
        :meth:`update_from_flat_dict`). Writing the file directly is what
        allowed a half-flushed config to destroy the admin password and MFA
        secrets during an unclean container shutdown.
        """
        sorted_config = dict(sorted(self.cfg.items(), key=lambda x: x[0].lower()))
        self._atomic_write_json(self.CONFIG_FILE, sorted_config)
        self.cfg = sorted_config
        return sorted_config

    def get_value(self, key, default=None):
        """Read a possibly nested key using dot notation (``mfa.secret``)."""
        node = self.cfg
        for part in key.split('.'):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set_value(self, key, value, save=True):
        """Write a possibly nested key using dot notation, then persist."""
        parts = key.split('.')
        node = self.cfg
        for part in parts[:-1]:
            if not isinstance(node.get(part), dict):
                node[part] = {}
            node = node[part]
        node[parts[-1]] = value
        if save:
            self.save()
        return value

    def repair_database_path(self):
        """Split the configured database path into a stored and a usable form.

        Two different values are needed and conflating them is what made older
        releases fragile:

        * the **stored** value must be portable (``sqlite:///redirect.db``) so a
          copied ``data/`` folder works on any machine, OS or mount point;
        * the **resolved** value must be absolute, because SQLAlchemy resolves a
          relative SQLite path against the *current working directory*, which is
          ``/app`` in a container and whatever shell the operator used on a
          host - never the data directory.

        A stored absolute path that no longer exists on this host is also
        repaired here: releases before the portability fix wrote values like
        ``sqlite:///D:\\projects\\redirector\\data\\redirect.db``, and after a
        move or re-clone SQLite would create a fresh empty file, making the
        install look wiped.
        """
        stored = self.cfg.get('database')
        resolved, heal_from = resolve_database_uri(stored, self.DATA_DIR)
        self.resolved_database = resolved

        if heal_from is not None:
            self.logger.warning(
                "⚠️ Configured database %r does not exist on this host. Re-anchored to %r "
                "(your data stays in the data directory).",
                heal_from, resolved,
            )

        # Work out the portable value to persist.
        resolved_path = sqlite_path_from_uri(resolved) if resolved else None
        portable = (
            portable_uri_for_path(resolved_path, self.DATA_DIR)
            if resolved_path
            else resolved
        )

        if portable != stored:
            self.cfg['database'] = portable
            try:
                self.save()
                self.logger.info(
                    "ℹ️ Database path stored as %r so the data directory stays portable "
                    "(resolved to %r).",
                    portable, resolved,
                )
            except Exception as exc:
                self.logger.warning("Could not persist portable database path: %s", exc)

    def load_raw_config(self):
        """Load the config safely, falling back to backup if corrupted or empty."""
        if not os.path.exists(self.CONFIG_FILE) or os.path.getsize(self.CONFIG_FILE) == 0:
            if hasattr(self, 'CONFIG_BACKUP_FILE') and os.path.exists(self.CONFIG_BACKUP_FILE) and os.path.getsize(self.CONFIG_BACKUP_FILE) > 0:
                try:
                    with open(self.CONFIG_BACKUP_FILE, 'r', encoding='utf-8') as bf:
                        backup_data = json.load(bf)
                    self._atomic_write_json(self.CONFIG_FILE, backup_data)
                    return backup_data
                except Exception:
                    pass
            try:
                default = self.get_default_config()
                self._atomic_write_json(self.CONFIG_FILE, default)
                print(f"\n🔐 Admin Password (save this): {default['admin_password']}")
                return default
            except IOError:
                return {}

        try:
            with open(self.CONFIG_FILE, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
                return config_data
        except (IOError, json.JSONDecodeError) as e:
            if hasattr(self, 'CONFIG_BACKUP_FILE') and os.path.exists(self.CONFIG_BACKUP_FILE):
                try:
                    with open(self.CONFIG_BACKUP_FILE, 'r', encoding='utf-8') as bf:
                        backup_data = json.load(bf)
                    print(f"⚠️ Primary configuration file was corrupted ({e}). Restored safely from backup.")
                    self._atomic_write_json(self.CONFIG_FILE, backup_data)
                    return backup_data
                except Exception:
                    pass
            return {}

    def get_redis_default_config(self):
        """Fetch Redis configurations dynamically."""
        try:
            redis_host = os.getenv('REDIS_HOST', 'redis' if self.RUNNING_IN_DOCKER else 'localhost')
            redis_port = 6379
            return {
                "host": redis_host,
                "port": redis_port
            }
        except Exception as e:
            self.logger.error(f"❌ Error fetching Redis default config: {e}")
            return {
                "host": "localhost",
                "port": 6379
            }

    def init_redis(self):
        """Initialize Redis client and handle connection errors."""
        try:
            if not self.redis_enabled:
                self.logger.debug("🔗 Redis is disabled, skipping initialization.")
                return
            self.logger.debug(f"🔗 Initializing Redis client at {self.redis_host}:{self.redis_port}")
            self.redis_client = redis.Redis(
                host=self.redis_host,
                port=self.redis_port,
                decode_responses=True,
                socket_connect_timeout=1
            )
            self.redis_client.ping() 

            self.logger.info(f"✅ Redis connected at {self.redis_host}:{self.redis_port}")
        except redis.exceptions.ConnectionError as e:
            self.logger.warning(f"⚠️ Redis connection failed: {e}")
            self.redis_enabled = False
            self.redis_client = None
        except Exception as e:
            self.logger.exception("❌ Unexpected error initializing Redis.")
            self.redis_enabled = False
            self.redis_client = None

    def reconnect_redis(self):
        """Attempt to reconnect to Redis (e.g., after failure)."""
        if not self.redis_enabled:
            return
        self.logger.debug("🔄 Attempting Redis reconnection...")
        self.init_redis()

    def get_default_config(self):
        """Generate default config values including secure admin password."""
        random_pwd = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(12))
        redis_default = self.get_redis_default_config()

        _default_config = {
            "_config_version": 1,
            "port": 80,
            "auto_redirect_delay": 1,
            # Portable on purpose: relative to the data directory, so a copied
            # data/ folder keeps working on any OS, machine or mount point.
            "database": SQLITE_PREFIX + PORTABLE_DB_SUFFIX,
            "admin_password": random_pwd,
            # Persisted so admin sessions survive restarts and upgrades, and so
            # every gunicorn worker shares one key (a random per-process key
            # made MFA logins fail depending on which worker answered).
            "session_secret": secrets.token_urlsafe(48),
            "delete_requires_password": True,
            "upstreams": [],
            "log_level": "INFO",
            "redis": {
                "enabled": True,
                "host": redis_default.get("host"),
                "port": redis_default.get("port")
            },
            "upstream_cache": {
                "enabled": True
            },
            "mfa": {
                "enabled": False,
                "secret": None,
                "backup_codes": [],
                "passkeys": []
            },
            "setup_completed": False
        }
        # Sort the dictionary by keys (case-insensitive)
        sorted_default_config = dict(sorted(_default_config.items(), key=lambda x: x[0].lower()))
        return sorted_default_config


    def to_dict(self):
        """Expose config for external usage like in templates."""
        return self.cfg
    
    def ensure_config_defaults(self, config):
        """
        Compare the current config with default config and auto-add missing keys.
        It updates the config file on disk if any missing keys are found.
        """
        default = self.get_default_config()
        changed = self._merge_config_recursive(config, default)

        # If config updated, write back to file
        if changed:
            try:
                self._atomic_write_json(self.CONFIG_FILE, config)
                self.logger.info("🛠 Config file updated with missing defaults.")
            except Exception as e:
                self.logger.warning(f"⚠️ Failed to write updated config: {e}")

    def _merge_config_recursive(self, current, default):
        """
        Recursively merge missing keys from `default` into `current`.
        Returns True if any changes were made.
        """
        changed = False
        for key, value in default.items():
            if key not in current:
                current[key] = value
                changed = True
            elif isinstance(value, dict) and isinstance(current.get(key), dict):
                # Recurse into nested dicts
                if self._merge_config_recursive(current[key], value):
                    changed = True
        return changed

    def update_from_flat_dict(self, new_data):
        """Update config from a flat dict (supports dot notation for nested keys), then save and reload."""
        # Update self.cfg in-place so changes persist
        current = self.cfg
        # Guard the full dotted name as well, so a form field cannot smuggle in
        # a protected secret through a nested path.
        readonly_keys = {'config_version', '_config_version', 'admin_password', 'session_secret'}
        def set_nested(cfg, key_path, value):
            keys = key_path.split('.')
            d = cfg
            for k in keys[:-1]:
                if k not in d or not isinstance(d[k], dict):
                    d[k] = {}
                d = d[k]
            old = d.get(keys[-1], value)
            if isinstance(old, bool):
                d[keys[-1]] = value.lower() == 'true' if isinstance(value, str) else bool(value)
            elif isinstance(old, int):
                try:
                    d[keys[-1]] = int(value)
                except Exception:
                    d[keys[-1]] = value
            else:
                d[keys[-1]] = value
        for k, v in new_data.items():
            if k in readonly_keys or k.split('.')[-1] in readonly_keys:
                continue
            if '.' in k:
                set_nested(current, k, v)
            else:
                if k in current:
                    if isinstance(current[k], bool):
                        current[k] = v.lower() == 'true' if isinstance(v, str) else bool(v)
                    elif isinstance(current[k], int):
                        try:
                            current[k] = int(v)
                        except Exception:
                            current[k] = v
                    else:
                        current[k] = v
                else:
                    current[k] = v
        # Sort keys for consistency and persist through the atomic writer.
        self.save()
        self.logger.info("Config updated successfully.")
        self.reload()

    def reload(self):
        """Reload config from disk and update all attributes."""
        temp_cfg = self.load_raw_config()
        self.ensure_config_defaults(temp_cfg)
        self.cfg = temp_cfg
        self.redis_cfg = self.cfg.get('redis', {})
        self.redis_enabled = self.redis_cfg.get('enabled', False)
        self.redis_host = self.redis_cfg.get('host', 'redis')
        try:
            self.redis_port = int(self.redis_cfg.get('port', 6379))
        except ValueError:
            self.redis_port = 6379
        self.resolved_database = self.cfg.get('database')
        self.database = self.cfg.get('database')
        self.repair_database_path()
        self.init_redis()
        self.logger.info("Config reloaded from disk.")

config=Config()

def get_config_data():
    """Return the current config as a dict for admin UI."""
    return config.to_dict()

def save_config_data(new_data):
    """Update config file with new_data and reload config object. Uses Config method."""
    config.update_from_flat_dict(new_data)