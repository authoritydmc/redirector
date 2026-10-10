"""Env-first settings. Replaces app/config.py's JSON god-object.

Every setting is overridable via environment (`REDIRECTOR_*`, plus
`DATABASE_URL` / `REDIS_URL` directly). The v2 `redirect.config.json`
is NOT read here — a one-shot `import-v2` command (EPIC-04) migrates it.
Importing this module must never touch the network, disk state, or DB.

Editable-at-runtime settings (EDITABLE_SCHEMA) resolve per request through
`resolve_setting`: an explicitly set `REDIRECTOR_*` wins, else a `settings`
table row (admin UI), else the builtin default. Everything secret or
infra-shaped is display-only (ENV_MANAGED_SETTINGS): shown by name, never
valued, change requires a restart.
"""

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _version_from_file() -> str:
    try:
        return (PROJECT_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "0.0.0-dev"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REDIRECTOR_", extra="ignore")

    app_name: str = "redirector"
    app_version: str = _version_from_file()
    data_dir: Path = PROJECT_ROOT / "data"
    database_url: str = "sqlite+aiosqlite:///./data/redirect.db"
    redis_url: str = "redis://localhost:6379/0"
    # Shortcut lookup cache: "memory" (per-process, default — no broker
    # needed) or "redis" (shared across API/worker processes). Same Cache
    # protocol either way; Redis outages degrade to DB reads, never 500s.
    cache_backend: str = "memory"
    # Background job execution: "in-process" (asyncio tasks, default — no
    # broker needed) or "arq" (Redis broker + `arq` worker processes).
    # The DB row + SSE surface is identical; only execution moves.
    job_backend: str = "in-process"
    # React build served at /app (EPIC-02 task 7 / M2). Override for custom
    # builds; the mount degrades to nothing when index.html is absent.
    spa_dir: Path = PROJECT_ROOT / "frontend" / "dist"
    # Account lockout (EPIC-05 task 11): failed logins per IP inside the
    # window that trigger a 429 lockout.
    auth_lockout_max_attempts: int = 10
    auth_lockout_window_minutes: int = 15
    log_level: str = "INFO"
    auto_redirect_delay: int = 1  # seconds before redirect; 0 = instant 302
    admin_password: str = "admin"
    jwt_secret: str = "redirector-insecure-dev-secret-change-in-production"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60


@lru_cache
def get_settings() -> Settings:  # FastAPI Depends-compatible singleton
    return Settings()


settings = get_settings()


def _env_var(key: str) -> str:
    return "REDIRECTOR_" + key.upper()


# Settings an admin may change at runtime (DB row, no restart). Everything
# here is read per request — never cache the resolved values in-process.
EDITABLE_SCHEMA: dict[str, dict[str, Any]] = {
    "auto_redirect_delay": {
        "title": "Redirect delay",
        "description": "Seconds before redirecting (0 = instant 302).",
        "type": "int",
        "min": 0,
        "max": 10,
    },
    "jwt_access_token_expire_minutes": {
        "title": "Session lifetime",
        "description": "Minutes a login token stays valid.",
        "type": "int",
        "min": 5,
        "max": 1440,
    },
    "auth_lockout_max_attempts": {
        "title": "Lockout threshold",
        "description": "Failed logins per IP inside the window before 429 lockout.",
        "type": "int",
        "min": 1,
        "max": 100,
    },
    "auth_lockout_window_minutes": {
        "title": "Lockout window",
        "description": "Minutes over which failed logins are counted.",
        "type": "int",
        "min": 1,
        "max": 1440,
    },
}

# Shown by name only (never valued): secrets and restart-only infra.
ENV_MANAGED_SETTINGS: list[dict[str, str]] = [
    {"key": "admin_password", "title": "Admin password", "description": "Set REDIRECTOR_ADMIN_PASSWORD and restart."},
    {"key": "jwt_secret", "title": "JWT secret", "description": "Set REDIRECTOR_JWT_SECRET and restart."},
    {"key": "database_url", "title": "Database", "description": "Set REDIRECTOR_DATABASE_URL and restart."},
    {"key": "redis_url", "title": "Redis", "description": "Set REDIRECTOR_REDIS_URL and restart."},
    {"key": "cache_backend", "title": "Cache backend", "description": "Set REDIRECTOR_CACHE_BACKEND and restart."},
    {"key": "job_backend", "title": "Job backend", "description": "Set REDIRECTOR_JOB_BACKEND and restart."},
    {"key": "log_level", "title": "Log level", "description": "Set REDIRECTOR_LOG_LEVEL and restart."},
    {"key": "data_dir", "title": "Data directory", "description": "Set REDIRECTOR_DATA_DIR and restart."},
]


def coerce_setting(key: str, raw: Any) -> Any:
    """Validate + coerce a PATCH value for an editable key. Raises ValueError."""
    meta = EDITABLE_SCHEMA[key]
    if isinstance(raw, bool) or isinstance(raw, float):
        raise ValueError(f"{key} must be an integer")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be an integer") from None
    if value < meta["min"] or value > meta["max"]:
        raise ValueError(f"{key} must be between {meta['min']} and {meta['max']}")
    return value


async def resolve_setting(session: AsyncSession, key: str) -> tuple[Any, str]:
    """Effective (value, source) for an editable key.

    Precedence: explicitly set `REDIRECTOR_*` env wins, else the `settings`
    table row (admin UI / import-v2), else the builtin default. Sources are
    `"environment"`, `"database"`, `"default"`.
    """
    from backend.models.entities import Setting  # deferred: models import config

    env_var = _env_var(key)
    if env_var in os.environ:
        return coerce_setting(key, os.environ[env_var]), "environment"
    row = (
        await session.execute(select(Setting).where(Setting.key == key))
    ).scalar_one_or_none()
    if row is not None and row.value is not None:
        return coerce_setting(key, row.value), "database"
    return Settings.model_fields[key].default, "default"
