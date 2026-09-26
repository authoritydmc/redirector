"""v3 domain models (SQLModel). See docs/refactor-epics/04-data-cache.md.

Clean v3 schema — breaking from v2 on purpose (not yet public):
- real DateTime(timezone=True) columns (v2 stored ISO strings in TEXT)
- Visibility as Enum, tags as JSON list (v2: comma-separated string)
- upstreams as a table (v2: buried in redirect.config.json JSON)
- expires_at as DateTime (v2: string)

v2 data comes over via a best-effort `import-v2` script, not a migration.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from sqlalchemy import JSON, Column, Enum as SAEnum, UniqueConstraint
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    """SQLite round-trips DateTime(timezone=True) as naive; Postgres as aware.

    Normalize at the boundary so expiry comparisons never mix naive/aware.
    """
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


JSONValue = dict[str, Any] | list[Any] | str | int | float | bool | None


class ShortcutType(str, Enum):
    STATIC = "static"
    DYNAMIC = "dynamic"
    USER_DYNAMIC = "user-dynamic"


class Visibility(str, Enum):
    PUBLIC = "public"
    UNLISTED = "unlisted"
    PRIVATE = "private"
    TEAM = "team"


class Shortcut(SQLModel, table=True):
    __tablename__ = "shortcuts"

    id: int | None = Field(default=None, primary_key=True)
    pattern: str = Field(unique=True, index=True)
    # values_callable: persist 'static', not 'STATIC' (Enum stores names by default)
    type: ShortcutType = Field(
        default=ShortcutType.STATIC,
        sa_column=Column(SAEnum(ShortcutType, values_callable=lambda cls: [m.value for m in cls])),
    )
    target: str = Field()
    access_count: int = Field(default=0)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    created_ip: str | None = Field(default=None)
    updated_ip: str | None = Field(default=None)
    tags: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    visibility: Visibility = Field(
        default=Visibility.PUBLIC,
        sa_column=Column(SAEnum(Visibility, values_callable=lambda cls: [m.value for m in cls])),
    )
    expires_at: datetime | None = Field(default=None, index=True)
    owner_email: str | None = Field(default=None, index=True)

    def is_expired(self, at: datetime | None = None) -> bool:
        ref = as_utc(at) or utcnow()
        expires = as_utc(self.expires_at)
        return expires is not None and expires <= ref


class Upstream(SQLModel, table=True):
    __tablename__ = "upstreams"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)
    base_url: str = Field()
    fail_url: str | None = Field(default=None)
    fail_status_code: int | None = Field(default=None)
    verify_ssl: bool = Field(default=True)
    skip_sso_cache: bool = Field(default=False)


class UpstreamCache(SQLModel, table=True):
    __tablename__ = "upstream_cache"

    pattern: str = Field(primary_key=True)
    upstream_name: str = Field(primary_key=True, index=True)
    resolved_url: str | None = Field(default=None)
    checked_at: datetime = Field(default_factory=utcnow)


class UpstreamCheckLog(SQLModel, table=True):
    __tablename__ = "upstream_check_log"
    __table_args__ = (UniqueConstraint("pattern", "upstream_name", name="uq_pattern_upstream"),)

    id: int | None = Field(default=None, primary_key=True)
    pattern: str = Field(index=True)
    upstream_name: str = Field(index=True)
    check_url: str | None = Field(default=None)
    result: str | None = Field(default=None)
    detail: str | None = Field(default=None)
    tried_at: datetime = Field(default_factory=utcnow)
    count: int = Field(default=1)
    cached: bool = Field(default=False)


class UserParam(SQLModel, table=True):
    __tablename__ = "user_params"
    __table_args__ = (UniqueConstraint("shortcut_pattern", "param_name", name="uq_shortcut_param"),)

    id: int | None = Field(default=None, primary_key=True)
    shortcut_pattern: str = Field(index=True)
    param_name: str = Field()
    description: str | None = Field(default=None)
    required: bool = Field(default=False)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Setting(SQLModel, table=True):
    """DB-backed non-secret settings (replaces redirect.config.json as source of truth)."""

    __tablename__ = "settings"

    key: str = Field(primary_key=True)
    value: JSONValue = Field(sa_column=Column(JSON))
    updated_at: datetime = Field(default_factory=utcnow)
