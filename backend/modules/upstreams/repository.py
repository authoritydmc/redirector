"""Repository for Upstreams and Upstream Cache using async SQLAlchemy."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.entities import Upstream, UpstreamCache, UpstreamCheckLog


class UpstreamRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_all(self) -> list[Upstream]:
        result = await self.session.execute(select(Upstream).order_by(Upstream.name))
        return list(result.scalars().all())

    async def get_by_name(self, name: str) -> Upstream | None:
        result = await self.session.execute(
            select(Upstream).where(Upstream.name == name)
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, upstream_id: int) -> Upstream | None:
        result = await self.session.execute(
            select(Upstream).where(Upstream.id == upstream_id)
        )
        return result.scalar_one_or_none()

    async def create(self, upstream: Upstream) -> Upstream:
        self.session.add(upstream)
        await self.session.commit()
        await self.session.refresh(upstream)
        return upstream

    async def update(self, upstream_id: int, data: dict[str, Any]) -> Upstream | None:
        row = await self.get_by_id(upstream_id)
        if row is None:
            return None
        for k, v in data.items():
            if v is not None and hasattr(row, k):
                setattr(row, k, v)
        await self.session.commit()
        await self.session.refresh(row)
        return row

    async def delete(self, upstream_id: int) -> bool:
        row = await self.get_by_id(upstream_id)
        if row is None:
            return False
        # Also clean up cache entries for this upstream
        await self.session.execute(
            delete(UpstreamCache).where(UpstreamCache.upstream_name == row.name)
        )
        await self.session.delete(row)
        await self.session.commit()
        return True

    # --- Upstream Cache Operations ---

    async def list_cache(self, upstream_name: str | None = None) -> list[UpstreamCache]:
        stmt = select(UpstreamCache)
        if upstream_name:
            stmt = stmt.where(UpstreamCache.upstream_name == upstream_name)
        result = await self.session.execute(stmt.order_by(UpstreamCache.checked_at.desc()))
        return list(result.scalars().all())

    async def purge_cache(self, upstream_name: str | None = None) -> int:
        stmt = delete(UpstreamCache)
        if upstream_name:
            stmt = stmt.where(UpstreamCache.upstream_name == upstream_name)
        result = await self.session.execute(stmt)
        await self.session.commit()
        return result.rowcount

    async def save_cache(
        self, pattern: str, upstream_name: str, resolved_url: str
    ) -> UpstreamCache:
        """Insert or refresh one upstream-cache row (resync write path)."""
        existing = (await self.session.execute(
            select(UpstreamCache).where(
                UpstreamCache.pattern == pattern,
                UpstreamCache.upstream_name == upstream_name,
            )
        )).scalar_one_or_none()
        now = datetime.now(UTC)
        if existing is not None:
            existing.resolved_url = resolved_url
            existing.checked_at = now
            await self.session.commit()
            await self.session.refresh(existing)
            return existing
        row = UpstreamCache(
            pattern=pattern,
            upstream_name=upstream_name,
            resolved_url=resolved_url,
            checked_at=now,
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return row

    async def clear_cache_entry(self, pattern: str, upstream_name: str) -> bool:
        """Delete one upstream-cache row; False when nothing was stored."""
        row = (await self.session.execute(
            select(UpstreamCache).where(
                UpstreamCache.pattern == pattern,
                UpstreamCache.upstream_name == upstream_name,
            )
        )).scalar_one_or_none()
        if row is None:
            return False
        await self.session.delete(row)
        await self.session.commit()
        return True

    async def log_check(
        self,
        pattern: str,
        upstream_name: str,
        check_url: str,
        result: str,
        detail: str,
        cached: bool = False,
    ) -> None:
        # Upsert check log
        existing = (await self.session.execute(
            select(UpstreamCheckLog).where(
                UpstreamCheckLog.pattern == pattern,
                UpstreamCheckLog.upstream_name == upstream_name,
            )
        )).scalar_one_or_none()

        now = datetime.now(UTC)
        if existing:
            existing.check_url = check_url
            existing.result = result
            existing.detail = detail
            existing.tried_at = now
            existing.count += 1
            existing.cached = cached
        else:
            log_entry = UpstreamCheckLog(
                pattern=pattern,
                upstream_name=upstream_name,
                check_url=check_url,
                result=result,
                detail=detail,
                tried_at=now,
                count=1,
                cached=cached,
            )
            self.session.add(log_entry)
        await self.session.commit()
