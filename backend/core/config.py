"""Env-first settings. Replaces app/config.py's JSON god-object.

Every setting is overridable via environment (`REDIRECTOR_*`, plus
`DATABASE_URL` / `REDIS_URL` directly). The v2 `redirect.config.json`
is NOT read here — a one-shot `import-v2` command (EPIC-04) migrates it.
Importing this module must never touch the network, disk state, or DB.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

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
