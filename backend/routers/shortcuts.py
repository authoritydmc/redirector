"""RESTful API router for managing shortcuts under /api/v1/shortcuts.

Supports:
- GET /api/v1/shortcuts (paginated, search, filter, sort)
- POST /api/v1/shortcuts (create with validation)
- GET /api/v1/shortcuts/{pattern} (retrieve)
- PATCH /api/v1/shortcuts/{pattern} (partial update)
- DELETE /api/v1/shortcuts/{pattern} (delete)
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.cache import build_cache
from backend.core.config import settings
from backend.core.db import get_session
from backend.core.errors import AppError
from backend.models.entities import Shortcut, as_utc
from backend.modules.shortcuts.repository import (
    SQLAlchemyShortcutRepository,
    sanitize_pattern,
)
from backend.modules.shortcuts.schemas import (
    BulkDeleteRequest,
    BulkDeleteResponse,
    ShortcutCreate,
    ShortcutListMeta,
    ShortcutListResponse,
    ShortcutRead,
    ShortcutUpdate,
)

router = APIRouter(prefix="/api/v1/shortcuts", tags=["shortcuts"])

_cache = build_cache(settings.cache_backend, settings.redis_url)


async def get_repo(session: AsyncSession = Depends(get_session)) -> SQLAlchemyShortcutRepository:
    return SQLAlchemyShortcutRepository(session, _cache)


@router.get("", response_model=ShortcutListResponse, summary="List shortcuts")
async def list_shortcuts(
    page: int = Query(1, ge=1, description="Page number"),
    pageSize: int = Query(20, ge=1, le=100, description="Items per page"),
    q: str = Query("", description="Search term across pattern and target"),
    tag: str = Query("", description="Filter by tag"),
    sort: str = Query("updated_at", pattern="^(updated_at|created_at|popular)$"),
    repo: SQLAlchemyShortcutRepository = Depends(get_repo),
) -> ShortcutListResponse:
    rows, total = await repo.list_paged(
        page=page, page_size=pageSize, query=q, tag=tag, sort_by=sort
    )
    return ShortcutListResponse(
        data=[ShortcutRead.model_validate(r) for r in rows],
        meta=ShortcutListMeta(page=page, pageSize=pageSize, total=total),
    )


@router.post("", response_model=ShortcutRead, status_code=status.HTTP_201_CREATED, summary="Create shortcut")
async def create_shortcut(
    body: ShortcutCreate,
    repo: SQLAlchemyShortcutRepository = Depends(get_repo),
) -> ShortcutRead:
    clean_pat = sanitize_pattern(body.pattern)
    if not clean_pat:
        raise AppError(
            "Invalid shortcut pattern",
            status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid pattern: must be alphanumeric or contain - _ . /",
            code="shortcuts:invalid-pattern",
        )
    existing, _ = await repo.lookup(clean_pat)
    if existing is not None:
        raise AppError(
            "Shortcut already exists",
            status=status.HTTP_409_CONFLICT,
            detail=f"Shortcut with pattern '{clean_pat}' already exists",
            code="shortcuts:conflict",
        )

    exp = None
    if body.expires_at:
        try:
            exp = datetime.fromisoformat(body.expires_at.replace("Z", "+00:00"))
        except ValueError:
            raise AppError(
                "Invalid expiry timestamp",
                status=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="expires_at must be an ISO 8601 timestamp string",
                code="shortcuts:invalid-expires-at",
            ) from None

    sc = Shortcut(
        pattern=clean_pat,
        target=body.target,
        type=body.type,
        tags=body.tags,
        visibility=body.visibility,
        expires_at=as_utc(exp),
        owner_email=body.owner_email,
    )
    try:
        created = await repo.create(sc)
    except IntegrityError:
        # Lost the check-then-insert race with a concurrent create.
        raise AppError(
            "Shortcut already exists",
            status=status.HTTP_409_CONFLICT,
            detail=f"Shortcut with pattern '{clean_pat}' already exists",
            code="shortcuts:conflict",
        ) from None
    return ShortcutRead.model_validate(created)


@router.post("/bulk-delete", response_model=BulkDeleteResponse, summary="Bulk delete shortcuts")
async def bulk_delete_shortcuts(
    body: BulkDeleteRequest,
    repo: SQLAlchemyShortcutRepository = Depends(get_repo),
) -> BulkDeleteResponse:
    deleted: list[str] = []
    not_found: list[str] = []
    for pat in body.patterns:
        clean = sanitize_pattern(pat)
        if not clean:
            not_found.append(pat)
            continue
        success = await repo.delete(clean)
        if success:
            deleted.append(clean)
        else:
            not_found.append(clean)

    return BulkDeleteResponse(deleted=deleted, not_found=not_found, count=len(deleted))


@router.get("/{pattern:path}", response_model=ShortcutRead, summary="Get shortcut details")
async def get_shortcut(
    pattern: str,
    repo: SQLAlchemyShortcutRepository = Depends(get_repo),
) -> ShortcutRead:
    sc, _ = await repo.lookup(pattern)
    if sc is None or not isinstance(sc, Shortcut):
        raise AppError(
            "Shortcut not found",
            status=status.HTTP_404_NOT_FOUND,
            detail=f"Shortcut '{pattern}' not found",
            code="shortcuts:not-found",
        )
    return ShortcutRead.model_validate(sc)


@router.patch("/{pattern:path}", response_model=ShortcutRead, summary="Update shortcut")
async def update_shortcut(
    pattern: str,
    body: ShortcutUpdate,
    repo: SQLAlchemyShortcutRepository = Depends(get_repo),
) -> ShortcutRead:
    data = body.model_dump(exclude_unset=True)
    if "expires_at" in data and data["expires_at"]:
        try:
            data["expires_at"] = as_utc(
                datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00"))
            )
        except ValueError:
            raise AppError(
                "Invalid expiry timestamp",
                status=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="expires_at must be an ISO 8601 timestamp string",
                code="shortcuts:invalid-expires-at",
            ) from None

    updated = await repo.update(pattern, data)
    if updated is None:
        raise AppError(
            "Shortcut not found",
            status=status.HTTP_404_NOT_FOUND,
            detail=f"Shortcut '{pattern}' not found",
            code="shortcuts:not-found",
        )
    return ShortcutRead.model_validate(updated)


@router.delete("/{pattern:path}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete shortcut")
async def delete_shortcut(
    pattern: str,
    repo: SQLAlchemyShortcutRepository = Depends(get_repo),
) -> None:
    deleted = await repo.delete(pattern)
    if not deleted:
        raise AppError(
            "Shortcut not found",
            status=status.HTTP_404_NOT_FOUND,
            detail=f"Shortcut '{pattern}' not found",
            code="shortcuts:not-found",
        )
    return None
