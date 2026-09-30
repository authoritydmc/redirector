"""RESTful API router for managing shortcuts under /api/v1/shortcuts.

Supports:
- GET /api/v1/shortcuts (paginated, search, filter, sort)
- POST /api/v1/shortcuts (create with validation)
- GET /api/v1/shortcuts/{pattern} (retrieve)
- PATCH /api/v1/shortcuts/{pattern} (partial update)
- DELETE /api/v1/shortcuts/{pattern} (delete)
"""

from __future__ import annotations

from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.cache import MemoryCache
from backend.core.db import get_session
from backend.models.entities import Shortcut, as_utc
from backend.modules.shortcuts.repository import (
    SQLAlchemyShortcutRepository,
    sanitize_pattern,
)
from backend.modules.shortcuts.schemas import (
    Problem,
    ShortcutCreate,
    ShortcutListResponse,
    ShortcutRead,
    ShortcutUpdate,
)

router = APIRouter(prefix="/api/v1/shortcuts", tags=["shortcuts"])

_memory_cache = MemoryCache()


async def get_repo(session: AsyncSession = Depends(get_session)) -> SQLAlchemyShortcutRepository:
    return SQLAlchemyShortcutRepository(session, _memory_cache)


@router.get("", response_model=ShortcutListResponse, summary="List shortcuts")
async def list_shortcuts(
    page: int = Query(1, ge=1, description="Page number"),
    pageSize: int = Query(20, ge=1, le=100, description="Items per page"),
    q: str = Query("", description="Search term across pattern and target"),
    tag: str = Query("", description="Filter by tag"),
    sort: str = Query("updated_at", regex="^(updated_at|created_at|popular)$"),
    repo: SQLAlchemyShortcutRepository = Depends(get_repo),
):
    rows, total = await repo.list_paged(
        page=page, page_size=pageSize, query=q, tag=tag, sort_by=sort
    )
    return ShortcutListResponse(
        data=[ShortcutRead.model_validate(r) for r in rows],
        meta={
            "page": page,
            "pageSize": pageSize,
            "total": total,
        },
    )


@router.post("", response_model=ShortcutRead, status_code=status.HTTP_201_CREATED, summary="Create shortcut")
async def create_shortcut(
    body: ShortcutCreate,
    repo: SQLAlchemyShortcutRepository = Depends(get_repo),
):
    clean_pat = sanitize_pattern(body.pattern)
    if not clean_pat:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid pattern: must be alphanumeric or contain - _ . /",
        )
    existing, _ = await repo.lookup(clean_pat)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Shortcut with pattern '{clean_pat}' already exists",
        )

    exp = None
    if body.expires_at:
        try:
            exp = datetime.fromisoformat(body.expires_at.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="expires_at must be an ISO 8601 timestamp string",
            )

    sc = Shortcut(
        pattern=clean_pat,
        target=body.target,
        type=body.type,
        tags=body.tags,
        visibility=body.visibility,
        expires_at=as_utc(exp),
        owner_email=body.owner_email,
    )
    created = await repo.create(sc)
    return ShortcutRead.model_validate(created)


@router.get("/{pattern:path}", response_model=ShortcutRead, summary="Get shortcut details")
async def get_shortcut(
    pattern: str,
    repo: SQLAlchemyShortcutRepository = Depends(get_repo),
):
    sc, _ = await repo.lookup(pattern)
    if sc is None or not isinstance(sc, Shortcut):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Shortcut '{pattern}' not found",
        )
    return ShortcutRead.model_validate(sc)


@router.patch("/{pattern:path}", response_model=ShortcutRead, summary="Update shortcut")
async def update_shortcut(
    pattern: str,
    body: ShortcutUpdate,
    repo: SQLAlchemyShortcutRepository = Depends(get_repo),
):
    data = body.model_dump(exclude_unset=True)
    if "expires_at" in data and data["expires_at"]:
        try:
            data["expires_at"] = as_utc(
                datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00"))
            )
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="expires_at must be an ISO 8601 timestamp string",
            )

    updated = await repo.update(pattern, data)
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Shortcut '{pattern}' not found",
        )
    return ShortcutRead.model_validate(updated)


@router.delete("/{pattern:path}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete shortcut")
async def delete_shortcut(
    pattern: str,
    repo: SQLAlchemyShortcutRepository = Depends(get_repo),
):
    deleted = await repo.delete(pattern)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Shortcut '{pattern}' not found",
        )
    return None
