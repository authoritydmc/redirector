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
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.cache import build_cache
from backend.core.config import settings
from backend.core.db import get_session
from backend.core.errors import AppError
from backend.core.policy import RequireAction
from backend.core.security import ADMIN_WRITE, RequireScopes, caller_email, get_current_admin, security
from backend.models.entities import Shortcut, Visibility, as_utc
from backend.modules.shortcuts.repository import (
    SQLAlchemyShortcutRepository,
    sanitize_pattern,
)
from backend.modules.shortcuts.schemas import (
    BulkDeleteRequest,
    BulkDeleteResponse,
    BulkImportRequest,
    BulkImportResponse,
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


async def _optional_identity(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any] | None:
    """Identity when a valid token rides along, else None. Mirrors
    RequireAction's leniency: on public actions an invalid token is
    ignored rather than rejected (enforcement itself stays untouched)."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        return None
    try:
        return await get_current_admin(credentials, session)
    except AppError:
        return None


async def _owned_shortcut(
    pattern: str,
    admin: Annotated[dict[str, Any], Depends(get_current_admin)],
    repo: Annotated[SQLAlchemyShortcutRepository, Depends(get_repo)],
) -> Shortcut:
    """Row for PATCH/DELETE: admins (scopes) pass; local users pass for
    links they own (owner_email match); everything else 403. Missing
    rows 404 exactly like before (same message/code)."""
    sc, _ = await repo.lookup(pattern)
    if sc is None or not isinstance(sc, Shortcut):
        raise AppError(
            "Shortcut not found",
            status=status.HTTP_404_NOT_FOUND,
            detail=f"Shortcut '{pattern}' not found",
            code="shortcuts:not-found",
        )
    granted = admin.get("scopes") or []
    if "*" in granted or ADMIN_WRITE in granted:
        return sc
    if admin.get("auth_method") == "jwt" and admin.get("role") == "user":
        email = admin.get("sub")
        if isinstance(email, str) and sc.owner_email and sc.owner_email == email:
            return sc
    raise AppError(
        "Admin privileges required",
        status=status.HTTP_403_FORBIDDEN,
        detail="Only the owner or an admin can change this link",
        code="auth:forbidden",
    )


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
    _auth: Annotated[dict[str, Any], Depends(RequireAction("shortcuts.create"))],
    me: Annotated[dict[str, Any] | None, Depends(_optional_identity)],
    session: Annotated[AsyncSession, Depends(get_session)],
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

    # Creator stamp (the stored username): signed-in users/SSO back it
    # with their verified email unless they overrode it.
    email = await caller_email(me, session) if me is not None else None
    owner = (body.owner_email or "").strip() or email

    # Team links ride on real memberships (site admins bypass).
    from backend.modules.teams.repository import TeamRepository

    team_id = body.team_id
    if body.visibility == Visibility.TEAM:
        if team_id is None:
            raise AppError(
                "Team link needs a team",
                status=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Pick the team this link belongs to",
                code="teams:link-needs-team",
            )
        team_repo = TeamRepository(session)
        if await team_repo.get(team_id) is None:
            raise AppError(
                "Team not found",
                status=status.HTTP_404_NOT_FOUND,
                detail=f"No team with id {team_id}",
                code="teams:not-found",
            )
        is_site_admin = me is not None and me.get("role") == "admin"
        member = email is not None and await team_repo.member_role(team_id, email) is not None
        if not is_site_admin and not member:
            raise AppError(
                "Team membership required",
                status=status.HTTP_403_FORBIDDEN,
                detail="Only team members can post team links",
                code="teams:not-member",
            )
    else:
        team_id = None

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
        owner_email=owner,
        team_id=team_id,
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
    _admin: Annotated[dict[str, Any], Depends(RequireScopes(ADMIN_WRITE))],
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


@router.post("/bulk-import", response_model=BulkImportResponse, summary="Bulk import shortcuts")
async def bulk_import_shortcuts(
    body: BulkImportRequest,
    _admin: Annotated[dict[str, Any], Depends(RequireScopes(ADMIN_WRITE))],
    repo: SQLAlchemyShortcutRepository = Depends(get_repo),
) -> BulkImportResponse:
    """Upsert shortcuts by pattern (insert new, overwrite existing). Invalid
    rows are skipped, never fatal — the response reports every outcome."""
    inserted = 0
    updated = 0
    skipped: list[str] = []
    for item in body.shortcuts:
        clean_pat = sanitize_pattern(item.pattern)
        if not clean_pat:
            skipped.append(item.pattern)
            continue
        exp = None
        if item.expires_at:
            try:
                exp = datetime.fromisoformat(item.expires_at.replace("Z", "+00:00"))
            except ValueError:
                skipped.append(item.pattern)
                continue
        data = {
            "target": item.target,
            "type": item.type,
            "tags": item.tags,
            "visibility": item.visibility,
            "expires_at": as_utc(exp),
            "owner_email": item.owner_email,
        }
        existing, _ = await repo.lookup(clean_pat)
        if existing is None:
            try:
                await repo.create(
                    Shortcut(
                        pattern=clean_pat,
                        target=item.target,
                        type=item.type,
                        tags=item.tags,
                        visibility=item.visibility,
                        expires_at=as_utc(exp),
                        owner_email=item.owner_email,
                    )
                )
            except IntegrityError:
                skipped.append(item.pattern)
                continue
            inserted += 1
        else:
            await repo.update(clean_pat, data)
            updated += 1
    return BulkImportResponse(
        inserted=inserted, updated=updated, skipped=skipped,
        count=inserted + updated,
    )


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
    _owner: Annotated[Shortcut, Depends(_owned_shortcut)],
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
    _owner: Annotated[Shortcut, Depends(_owned_shortcut)],
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
