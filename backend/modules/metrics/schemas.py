"""Response schemas for KPI aggregates + live telemetry.

Mirrors exactly what MetricsService returns so /docs carries real shapes
(EPIC-01 OpenAPI coverage). The service stays dict-based internally; the
router validates into these models at the boundary.
"""

from __future__ import annotations

from pydantic import BaseModel


class MostPopular(BaseModel):
    pattern: str
    target: str
    hits: int


class KpiOverview(BaseModel):
    total_shortcuts: int
    total_hits: int
    avg_hits: float
    zero_hit_count: int
    zero_hit_pct: float
    created_7d: int
    created_30d: int
    most_popular: MostPopular | None = None


class ByType(BaseModel):
    type: str
    shortcuts: int
    hits: int


class ByVisibility(BaseModel):
    visibility: str
    shortcuts: int


class TopTag(BaseModel):
    tag: str
    shortcuts: int


class HitBucket(BaseModel):
    bucket: str
    shortcuts: int


class KpiBreakdowns(BaseModel):
    by_type: list[ByType]
    by_visibility: list[ByVisibility]
    top_tags: list[TopTag]
    hit_buckets: list[HitBucket]


class UpstreamStats(BaseModel):
    configured: int
    cached_entries: int


class KpiResponse(BaseModel):
    overview: KpiOverview
    breakdowns: KpiBreakdowns
    upstreams: UpstreamStats


class ProcessInfo(BaseModel):
    version: str
    python: str
    platform: str
    timestamp: str
    memory_mb: float | None = None
    cpu_percent: float | None = None
    uptime_seconds: int | None = None


class LiveCounts(BaseModel):
    total_shortcuts: int
    total_hits: int


class CacheStats(BaseModel):
    hits: int
    misses: int
    hit_rate: float | None = None


class LiveResponse(BaseModel):
    status: str
    process: ProcessInfo
    counts: LiveCounts
    cache: CacheStats
