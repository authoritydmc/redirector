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

from sqlalchemy import func, select, update
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
# Negative caching (EPIC-06): total misses park a sentinel briefly so
# scanner traffic for unknown patterns costs one DB read per TTL, not one
# per request. Strictly less stale than the pre-existing 300 s positive
# caching (upstream resync already accepts that window — see
# UpstreamRepository.save_cache, which has no memory-cache invalidation).
NEGATIVE_CACHE_TTL = 60
CACHE_MISS_SENTINEL = "__redirector_miss__"

LookupResult = tuple[Shortcut | UpstreamCache | None, str]

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

    async def lookup(self, pattern: str) -> LookupResult:
        key = f"{SHORTCUT_CACHE_PREFIX}{pattern}"
        raw = await self.cache.get(key)
        if raw is None:
            return await self._cold_lookup(key, pattern)
        if raw == CACHE_MISS_SENTINEL:
            return None, ""
        parsed = self._parse(pattern, raw)
        if parsed is not None:
            return parsed
        # Corrupt or SSO-evicted entry: drop it and take the miss path once.
        await self.cache.delete(key)
        return await self._cold_lookup(key, pattern)

    async def _cold_lookup(self, key: str, pattern: str) -> LookupResult:
        """Miss path with stampede guard (EPIC-06 singleflight).

        Concurrent cold lookups for `key` share one DB read. The leader is
        served its own DB-loaded row (single-caller semantics unchanged);
        followers decode the shared stored string, so ORM rows are never
        shared across per-request sessions.
        """
        holder: dict[str, LookupResult] = {}

        async def _factory() -> tuple[str | None, int]:
            store, ttl, direct = await self._miss_load(pattern)
            holder["direct"] = direct
            return store, ttl

        raw = await self.cache.get_or_compute(key, _factory)
        if raw is None:
            # Explicitly uncacheable (SSO target): leader serves its own
            # DB-loaded row; a follower loads directly in its own session.
            direct = holder.get("direct")
            if direct is not None:
                return direct
            return await self._read_uncacheable(pattern)
        if raw == CACHE_MISS_SENTINEL:
            return None, ""
        direct = holder.get("direct")
        if direct is not None:
            # Leader: pre-singleflight result verbatim. Followers carry no
            # holder entry and decode the shared store below.
            return direct
        parsed = self._parse(pattern, raw)
        if parsed is not None:
            return parsed
        await self.cache.delete(key)
        return await self._read_uncacheable(pattern)

    async def _miss_load(self, pattern: str) -> tuple[str | None, int, LookupResult]:
        """DB read behind a cold cache miss.

        Returns (store_value, ttl, direct) where `direct` is the exact
        pre-singleflight lookup result the leader serves. Total misses
        store a short-TTL sentinel (negative caching); SSO rows return
        None as store_value so they are served from DB but never cached
        (v2 parity).
        """
        row = (await self.session.execute(
            select(Shortcut).where(Shortcut.pattern == pattern))).scalar_one_or_none()
        if row is not None:
            if is_sso_url(row.target):
                return None, 0, (row, "db")
            return json.dumps(self._snapshot(row)), SHORTCUT_CACHE_TTL, (row, "db")

        cached_row = (await self.session.execute(
            select(UpstreamCache).where(UpstreamCache.pattern == pattern)
        )).scalar_one_or_none()
        if cached_row is not None:
            if is_sso_url(cached_row.resolved_url):
                return None, 0, (None, "")
            return json.dumps({"upstream_name": cached_row.upstream_name,
                               "resolved_url": cached_row.resolved_url}), SHORTCUT_CACHE_TTL, (cached_row, "upstream")
        return CACHE_MISS_SENTINEL, NEGATIVE_CACHE_TTL, (None, "")

    async def _read_uncacheable(self, pattern: str) -> LookupResult:
        """Direct DB read that never touches the cache (SSO/corrupt paths).

        SSO shortcut targets are served from the DB every time but never
        stored; SSO upstream rows resolve to nothing.
        """
        row = (await self.session.execute(
            select(Shortcut).where(Shortcut.pattern == pattern))).scalar_one_or_none()
        if row is not None:
            return row, "db"
        cached_row = (await self.session.execute(
            select(UpstreamCache).where(UpstreamCache.pattern == pattern)
        )).scalar_one_or_none()
        if cached_row is not None:
            if is_sso_url(cached_row.resolved_url):
                return None, ""
            return cached_row, "upstream"
        return None, ""

    @staticmethod
    def _parse(pattern: str, raw: str) -> LookupResult | None:
        """Decode a stored cache string; None = corrupt or SSO (drop it)."""
        try:
            data = json.loads(raw)
            target = data.get("resolved_url") or data.get("target", "")
            if is_sso_url(target):
                return None
            if "resolved_url" in data:
                return (
                    UpstreamCache(pattern=pattern,
                                  upstream_name=data.get("upstream_name", ""),
                                  resolved_url=data["resolved_url"]), "memory",
                )
            # Served straight from cache (v2 parity); edits invalidate.
            return SQLAlchemyShortcutRepository._from_snapshot(data), "memory"
        except (ValueError, KeyError, TypeError):
            return None

    async def increment_access(self, pattern: str) -> None:
        # Single atomic UPDATE: concurrent redirects must not lose counts
        # to read-modify-write races.
        await self.session.execute(
            update(Shortcut)
            .where(Shortcut.pattern == pattern)
            .values(
                access_count=Shortcut.access_count + 1,
                updated_at=datetime.now(UTC),
            )
        )
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
