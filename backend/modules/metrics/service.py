"""Async service for computing instance KPI aggregates and live process telemetry."""

from __future__ import annotations

import os
import platform
import time
from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import settings
from backend.models.entities import (
    Shortcut,
    Upstream,
    UpstreamCache,
    as_utc,
    utcnow,
)


class MetricsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_kpi_metrics(self) -> dict[str, Any]:
        now = utcnow()
        rows_res = await self.session.execute(select(Shortcut))
        rows = list(rows_res.scalars().all())

        total = len(rows)
        hits = [r.access_count or 0 for r in rows]
        total_hits = sum(hits)
        zero_hit = sum(1 for h in hits if h <= 0)

        week_ago = now - timedelta(days=7)
        month_ago = now - timedelta(days=30)

        def _created_at(r: Shortcut) -> datetime:
            return as_utc(r.created_at) or now

        created_7d = sum(1 for r in rows if _created_at(r) >= week_ago)
        created_30d = sum(1 for r in rows if _created_at(r) >= month_ago)

        top = max(rows, key=lambda r: r.access_count or 0, default=None)

        # Breakdowns
        by_type: Counter[str] = Counter()
        type_hits: Counter[str] = Counter()
        by_visibility: Counter[str] = Counter()
        tag_counter: Counter[str] = Counter()
        buckets = {"0": 0, "1-10": 0, "11-100": 0, "101+": 0}

        for r in rows:
            stype = str(r.type.value if hasattr(r.type, 'value') else r.type).lower()
            by_type[stype] += 1
            type_hits[stype] += r.access_count or 0

            vis = str(r.visibility.value if hasattr(r.visibility, 'value') else r.visibility).lower()
            by_visibility[vis] += 1

            for tag in (r.tags or []):
                tag_clean = tag.strip().lower()
                if tag_clean:
                    tag_counter[tag_clean] += 1

            h = r.access_count or 0
            if h <= 0:
                buckets["0"] += 1
            elif h <= 10:
                buckets["1-10"] += 1
            elif h <= 100:
                buckets["11-100"] += 1
            else:
                buckets["101+"] += 1

        # Upstream metrics
        upstreams_count = (
            await self.session.execute(select(func.count()).select_from(Upstream))
        ).scalar() or 0
        upstream_cache_count = (
            await self.session.execute(select(func.count()).select_from(UpstreamCache))
        ).scalar() or 0

        return {
            "overview": {
                "total_shortcuts": total,
                "total_hits": total_hits,
                "avg_hits": round(total_hits / total, 1) if total else 0,
                "zero_hit_count": zero_hit,
                "zero_hit_pct": round(100 * zero_hit / total, 1) if total else 0,
                "created_7d": created_7d,
                "created_30d": created_30d,
                "most_popular": (
                    {"pattern": top.pattern, "target": top.target, "hits": top.access_count or 0}
                    if top else None
                ),
            },
            "breakdowns": {
                "by_type": [
                    {"type": t, "shortcuts": by_type[t], "hits": type_hits[t]}
                    for t in sorted(by_type)
                ],
                "by_visibility": [
                    {"visibility": v, "shortcuts": by_visibility[v]}
                    for v in sorted(by_visibility)
                ],
                "top_tags": [
                    {"tag": tag, "shortcuts": count}
                    for tag, count in tag_counter.most_common(10)
                ],
                "hit_buckets": [
                    {"bucket": b, "shortcuts": buckets[b]} for b in ("0", "1-10", "11-100", "101+")
                ],
            },
            "upstreams": {
                "configured": upstreams_count,
                "cached_entries": upstream_cache_count,
            },
        }

    async def get_live_metrics(self) -> dict[str, Any]:
        sc_count = (
            await self.session.execute(select(func.count()).select_from(Shortcut))
        ).scalar() or 0
        hits_sum = (
            await self.session.execute(select(func.sum(Shortcut.access_count)))
        ).scalar() or 0

        proc_info: dict[str, Any] = {
            "version": settings.app_version,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "timestamp": utcnow().isoformat(),
        }

        try:
            import psutil
            p = psutil.Process(os.getpid())
            proc_info["memory_mb"] = round(p.memory_info().rss / (1024 * 1024), 2)
            proc_info["cpu_percent"] = p.cpu_percent(interval=None)
            proc_info["uptime_seconds"] = int(time.time() - p.create_time())
        except Exception:
            pass

        return {
            "status": "healthy",
            "process": proc_info,
            "counts": {
                "total_shortcuts": sc_count,
                "total_hits": hits_sum,
            },
        }
