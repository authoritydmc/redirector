"""RESTful API router for Upstreams and Upstream Checks under /api/v1/upstreams."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.db import get_session
from backend.models.entities import Upstream
from backend.modules.upstreams.repository import UpstreamRepository
from backend.modules.upstreams.schemas import (
    CachePurgeResult,
    CacheResyncRequest,
    CacheResyncResponse,
    CheckLogEntry,
    UpstreamCacheEntry,
    UpstreamCreate,
    UpstreamRead,
    UpstreamUpdate,
)
from backend.modules.upstreams.service import UpstreamCheckService

router = APIRouter(prefix="/api/v1/upstreams", tags=["upstreams"])


async def get_upstream_repo(session: AsyncSession = Depends(get_session)) -> UpstreamRepository:
    return UpstreamRepository(session)


@router.get("", response_model=list[UpstreamRead], summary="List all upstreams")
async def list_upstreams(repo: UpstreamRepository = Depends(get_upstream_repo)) -> list[UpstreamRead]:
    return [UpstreamRead.model_validate(u) for u in await repo.list_all()]


@router.post("", response_model=UpstreamRead, status_code=status.HTTP_201_CREATED, summary="Create an upstream")
async def create_upstream(
    body: UpstreamCreate,
    repo: UpstreamRepository = Depends(get_upstream_repo),
) -> UpstreamRead:
    existing = await repo.get_by_name(body.name)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Upstream with name '{body.name}' already exists",
        )
    up = Upstream(
        name=body.name.strip(),
        base_url=body.base_url.strip(),
        fail_url=body.fail_url.strip() if body.fail_url else None,
        fail_status_code=body.fail_status_code,
        verify_ssl=body.verify_ssl,
        skip_sso_cache=body.skip_sso_cache,
    )
    try:
        return UpstreamRead.model_validate(await repo.create(up))
    except IntegrityError:
        # Lost the check-then-insert race with a concurrent create.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Upstream with name '{body.name}' already exists",
        ) from None


@router.get("/cache", response_model=list[UpstreamCacheEntry], summary="List cached upstream shortcuts")
async def list_upstream_cache(
    upstream: str | None = Query(None, description="Filter by upstream name"),
    repo: UpstreamRepository = Depends(get_upstream_repo),
) -> list[UpstreamCacheEntry]:
    rows = await repo.list_cache(upstream_name=upstream)
    return [
        UpstreamCacheEntry(
            pattern=r.pattern,
            upstream_name=r.upstream_name,
            resolved_url=r.resolved_url,
            checked_at=r.checked_at.isoformat(),
        )
        for r in rows
    ]


@router.delete("/cache", response_model=CachePurgeResult, summary="Purge upstream shortcut cache")
async def purge_upstream_cache(
    upstream: str | None = Query(None, description="Purge specific upstream only, or all if omitted"),
    repo: UpstreamRepository = Depends(get_upstream_repo),
) -> CachePurgeResult:
    count = await repo.purge_cache(upstream_name=upstream)
    return CachePurgeResult(success=True, purged=count)


@router.post("/cache/resync", response_model=CacheResyncResponse, summary="Resync upstream cache")
async def resync_upstream_cache(
    body: CacheResyncRequest,
    repo: UpstreamRepository = Depends(get_upstream_repo),
) -> CacheResyncResponse:
    """Re-check one pattern (or all cached ones) and reconcile cache rows.

    Synchronous by design at this scale; graduate to arq workers (EPIC-04/06)
    if resync-all outgrows the 3 s-per-check request budget.
    """
    upstream = await repo.get_by_name(body.upstream)
    if upstream is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Upstream '{body.upstream}' not found",
        )
    if body.pattern:
        patterns = [body.pattern.strip().strip("/")]
    else:
        patterns = [r.pattern for r in await repo.list_cache(upstream_name=upstream.name)]

    service = UpstreamCheckService(repo)
    timeout = httpx.Timeout(3.0, connect=3.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        results, updated, cleared = await service.refresh_patterns(upstream, patterns, client)
    return CacheResyncResponse(
        success=True,
        upstream=upstream.name,
        checked=len(patterns),
        updated=updated,
        cleared=cleared,
        results=results,
    )


@router.delete(
    "/cache/{upstream}/{pattern:path}",
    response_model=CachePurgeResult,
    summary="Purge one cached upstream entry",
)
async def purge_cache_entry(
    upstream: str,
    pattern: str,
    repo: UpstreamRepository = Depends(get_upstream_repo),
) -> CachePurgeResult:
    deleted = await repo.clear_cache_entry(pattern, upstream)
    return CachePurgeResult(success=True, purged=1 if deleted else 0)


@router.get(
    "/check-logs",
    response_model=list[CheckLogEntry],
    summary="List upstream check logs",
)
async def list_check_logs(
    upstream: str | None = Query(None, description="Filter by upstream name"),
    limit: int = Query(50, ge=1, le=200, description="Max entries, newest first"),
    repo: UpstreamRepository = Depends(get_upstream_repo),
) -> list[CheckLogEntry]:
    rows = await repo.list_check_logs(upstream_name=upstream, limit=limit)
    return [
        CheckLogEntry(
            id=r.id,
            pattern=r.pattern,
            upstream_name=r.upstream_name,
            check_url=r.check_url,
            result=r.result,
            detail=r.detail,
            tried_at=r.tried_at.isoformat(),
            count=r.count,
            cached=r.cached,
        )
        for r in rows
    ]


@router.delete(
    "/check-logs",
    response_model=CachePurgeResult,
    summary="Clear upstream check logs",
)
async def clear_check_logs(
    upstream: str | None = Query(None, description="Clear one upstream only, or all if omitted"),
    repo: UpstreamRepository = Depends(get_upstream_repo),
) -> CachePurgeResult:
    count = await repo.clear_check_logs(upstream_name=upstream)
    return CachePurgeResult(success=True, purged=count)


# NOTE: static sub-paths (/cache, /check/...) must stay ABOVE /{upstream_id}:
# Starlette matches in registration order and "cache" fails the int converter
# with 422 instead of falling through to the later route.
@router.patch("/{upstream_id}", response_model=UpstreamRead, summary="Update an upstream")
async def update_upstream(
    upstream_id: int,
    body: UpstreamUpdate,
    repo: UpstreamRepository = Depends(get_upstream_repo),
) -> UpstreamRead:
    updated = await repo.update(upstream_id, body.model_dump(exclude_unset=True))
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Upstream id {upstream_id} not found",
        )
    return UpstreamRead.model_validate(updated)


@router.delete("/{upstream_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete an upstream")
async def delete_upstream(
    upstream_id: int,
    repo: UpstreamRepository = Depends(get_upstream_repo),
) -> None:
    deleted = await repo.delete(upstream_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Upstream id {upstream_id} not found",
        )
    return None


@router.get("/check/stream/{pattern:path}", summary="Stream real-time upstream resolution check via SSE")
async def stream_upstream_check(
    pattern: str,
    repo: UpstreamRepository = Depends(get_upstream_repo),
) -> StreamingResponse:
    service = UpstreamCheckService(repo)
    upstreams = await repo.list_all()

    async def event_generator() -> AsyncIterator[str]:
        # EPIC-01 timeout budget: 3s per upstream check (fast-fail on SSE).
        timeout = httpx.Timeout(3.0, connect=3.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            yield f"data: {json.dumps({'message': f'Starting check for pattern: {pattern}'})}\n\n"
            for up in upstreams:
                res = await service.check_single(up, pattern, client)
                yield f"data: {res.model_dump_json()}\n\n"
                if res.status in ("found", "sso_required"):
                    break
            yield f"data: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
