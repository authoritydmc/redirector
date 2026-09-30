"""RESTful API router for Upstreams and Upstream Checks under /api/v1/upstreams."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.db import get_session
from backend.models.entities import Upstream
from backend.modules.upstreams.repository import UpstreamRepository
from backend.modules.upstreams.schemas import (
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
    return UpstreamRead.model_validate(await repo.create(up))


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


@router.delete("/cache", summary="Purge upstream shortcut cache")
async def purge_upstream_cache(
    upstream: str | None = Query(None, description="Purge specific upstream only, or all if omitted"),
    repo: UpstreamRepository = Depends(get_upstream_repo),
) -> dict[str, Any]:
    count = await repo.purge_cache(upstream_name=upstream)
    return {"success": True, "purged": count}


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
