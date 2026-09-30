"""Shortcut repository: cache → db → upstream-cache lookup order (ports v2 get_shortcut).

v2 rules preserved:
- memory/Redis hit wins; corrupt entries are dropped, never served
- SSO targets are NEVER served from cache and never written to it
- upstream-cache rows resolve as static pseudo-shortcuts
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.cache import Cache
from backend.models.entities import (
    Shortcut,
    ShortcutType,
    UpstreamCache,
    UserParam,
)

SHORTCUT_CACHE_PREFIX = "shortcut:"
SHORTCUT_CACHE_TTL = 300

# Ported verbatim from app/utils/utils.py::SSO_URL_PATTERNS.
SSO_URL_PATTERNS = (
    "accounts.google.com", "login.microsoftonline.com", "login.microsoft.com",
    "okta.com", "oktapreview.com", "okta-emea.com", "auth0.com",
    "sts.windows.net", "login.windows.net", "adfs", "saml", "oauth",
    "openid", "signin", "/login", "/signin", "/auth", "/sso",
    "pingidentity", "onelogin", "accounts.", "login.", "auth.", "sso.",
)


def is_sso_url(url: str | None) -> bool:
    """SSO/login URLs must never be cached (stale auth redirects)."""
    if not url or not isinstance(url, str):
        return False
    lower = url.lower()
    return any(pat.lower() in lower for pat in SSO_URL_PATTERNS)


class ShortcutRepository(Protocol):
    async def lookup(self, pattern: str) -> tuple[Shortcut | UpstreamCache | None, str]: ...
    async def increment_access(self, pattern: str) -> None: ...
    async def list_dynamic(self) -> list[Shortcut]: ...
    async def find_similar(self, query: str, limit: int = 3) -> list[Shortcut]: ...
    async def get_user_params(self, pattern: str) -> list[UserParam]: ...


class SQLAlchemyShortcutRepository:
    def __init__(self, session: AsyncSession, cache: Cache) -> None:
        self.session = session
        self.cache = cache

    @staticmethod
    def _snapshot(row: Shortcut) -> dict[str, Any]:
        return {
            "id": row.id,
            "pattern": row.pattern,
            "type": row.type.value,
            "target": row.target,
            "tags": row.tags,
            "visibility": row.visibility.value,
            "expires_at": row.expires_at.isoformat() if row.expires_at else None,
            "owner_email": row.owner_email,
            "created_ip": row.created_ip,
        }

    @staticmethod
    def _from_snapshot(data: dict[str, Any]) -> Shortcut:
        return Shortcut(
            id=data.get("id"),
            pattern=data["pattern"],
            type=ShortcutType(data.get("type", "static")),
            target=data.get("target", ""),
            tags=data.get("tags") or [],
            visibility=data.get("visibility", "public"),
            expires_at=datetime.fromisoformat(data["expires_at"]) if data.get("expires_at") else None,
            owner_email=data.get("owner_email"),
            created_ip=data.get("created_ip"),
        )

    async def lookup(self, pattern: str) -> tuple[Shortcut | UpstreamCache | None, str]:
        cached = await self.cache.get(f"{SHORTCUT_CACHE_PREFIX}{pattern}")
        if cached:
            try:
                data = json.loads(cached)
                target = data.get("resolved_url") or data.get("target", "")
                if is_sso_url(target):
                    await self.cache.delete(f"{SHORTCUT_CACHE_PREFIX}{pattern}")
                elif "resolved_url" in data:
                    return (
                        UpstreamCache(pattern=pattern,
                                      upstream_name=data.get("upstream_name", ""),
                                      resolved_url=data["resolved_url"]), "memory",
                    )
                else:
                    # Served straight from cache (v2 parity); edits invalidate.
                    return self._from_snapshot(data), "memory"
            except (ValueError, KeyError, TypeError):
                await self.cache.delete(f"{SHORTCUT_CACHE_PREFIX}{pattern}")

        row = (await self.session.execute(
            select(Shortcut).where(Shortcut.pattern == pattern))).scalar_one_or_none()
        if row is not None:
            if not is_sso_url(row.target):
                await self.cache.set(
                    f"{SHORTCUT_CACHE_PREFIX}{pattern}",
                    json.dumps(self._snapshot(row)),
                    SHORTCUT_CACHE_TTL,
                )
            return row, "db"

        cached_row = (await self.session.execute(
            select(UpstreamCache).where(UpstreamCache.pattern == pattern)
        )).scalar_one_or_none()
        if cached_row is not None:
            if is_sso_url(cached_row.resolved_url):
                return None, ""
            await self.cache.set(
                f"{SHORTCUT_CACHE_PREFIX}{pattern}",
                json.dumps({"upstream_name": cached_row.upstream_name,
                            "resolved_url": cached_row.resolved_url}),
                SHORTCUT_CACHE_TTL,
            )
            return cached_row, "upstream"
        return None, ""

    async def increment_access(self, pattern: str) -> None:
        row = (await self.session.execute(
            select(Shortcut).where(Shortcut.pattern == pattern))).scalar_one_or_none()
        if row is not None:
            row.access_count = (row.access_count or 0) + 1
            row.updated_at = datetime.now(UTC)
            await self.session.commit()

    async def list_dynamic(self) -> list[Shortcut]:
        result = await self.session.execute(
            select(Shortcut).where(Shortcut.type.in_(
                [ShortcutType.DYNAMIC, ShortcutType.USER_DYNAMIC])))
        return list(result.scalars().all())

    async def find_similar(self, query: str, limit: int = 3) -> list[Shortcut]:
        q = query.strip().lower()
        if not q:
            return []
        result = await self.session.execute(
            select(Shortcut)
            .where(func.lower(Shortcut.pattern).like(f"%{q}%"))
            .order_by(func.length(Shortcut.pattern))
            .limit(limit * 2)
        )
        return list(result.scalars().all())[:limit]

    async def get_user_params(self, pattern: str) -> list[UserParam]:
        result = await self.session.execute(
            select(UserParam).where(UserParam.shortcut_pattern == pattern))
        return list(result.scalars().all())

    async def list_paged(
        self,
        page: int = 1,
        page_size: int = 20,
        query: str = "",
        tag: str = "",
        sort_by: str = "updated_at",
    ) -> tuple[list[Shortcut], int]:
        stmt = select(Shortcut)
        count_stmt = select(func.count(Shortcut.id))

        if query:
            q = f"%{query.strip().lower()}%"
            stmt = stmt.where(func.lower(Shortcut.pattern).like(q) | func.lower(Shortcut.target).like(q))
            count_stmt = count_stmt.where(func.lower(Shortcut.pattern).like(q) | func.lower(Shortcut.target).like(q))

        if tag:
            t = f"%{tag.strip().lower()}%"
            stmt = stmt.where(func.lower(Shortcut.tags).like(t))
            count_stmt = count_stmt.where(func.lower(Shortcut.tags).like(t))

        if sort_by == "popular":
            stmt = stmt.order_by(Shortcut.access_count.desc(), Shortcut.updated_at.desc())
        elif sort_by == "created_at":
            stmt = stmt.order_by(Shortcut.created_at.desc())
        else:
            stmt = stmt.order_by(Shortcut.updated_at.desc())

        total = (await self.session.execute(count_stmt)).scalar() or 0
        offset = max(0, (page - 1) * page_size)
        stmt = stmt.offset(offset).limit(page_size)
        rows = (await self.session.execute(stmt)).scalars().all()
        return list(rows), total

    async def create(self, shortcut: Shortcut) -> Shortcut:
        self.session.add(shortcut)
        await self.session.commit()
        await self.session.refresh(shortcut)
        # Invalidate cache
        await self.cache.delete(f"{SHORTCUT_CACHE_PREFIX}{shortcut.pattern.lower()}")
        return shortcut

    async def update(self, pattern: str, data: dict[str, Any]) -> Shortcut | None:
        row = (await self.session.execute(
            select(Shortcut).where(Shortcut.pattern == pattern.lower())
        )).scalar_one_or_none()
        if row is None:
            return None
        for k, v in data.items():
            if v is not None and hasattr(row, k):
                setattr(row, k, v)
        row.updated_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(row)
        # Invalidate cache
        await self.cache.delete(f"{SHORTCUT_CACHE_PREFIX}{pattern.lower()}")
        return row

    async def delete(self, pattern: str) -> bool:
        row = (await self.session.execute(
            select(Shortcut).where(Shortcut.pattern == pattern.lower())
        )).scalar_one_or_none()
        if row is None:
            return False
        await self.session.delete(row)
        await self.session.commit()
        # Invalidate cache
        await self.cache.delete(f"{SHORTCUT_CACHE_PREFIX}{pattern.lower()}")
        return True


def normalize_subpath(subpath: str | None) -> str:
    if subpath is None or not isinstance(subpath, str):
        return ""
    return subpath.strip().lower().strip("/")


def sanitize_pattern(pattern: str | None) -> str:
    """Port of v2 sanitize_pattern: lowercase, slashes trimmed, no empties."""
    if not pattern or not isinstance(pattern, str):
        return ""
    cleaned = pattern.strip().lower().strip("/")
    return cleaned if all(ch.isalnum() or ch in "-_./" for ch in cleaned) else ""
