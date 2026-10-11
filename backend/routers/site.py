"""Public site policy (no auth): which mutating actions the admin opened up."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.db import get_session
from backend.core.errors import AppError
from backend.core.policy import KNOWN_PUBLIC_ACTIONS, PUBLIC_ACTIONS_KEY, get_public_actions
from backend.core.security import ADMIN_WRITE, RequireScopes, require_jwt_session
from backend.models.entities import Setting

router = APIRouter(prefix="/api/v1/site", tags=["site"])


class SitePolicy(BaseModel):
    public_actions: list[str]
    known_actions: list[str] = list(KNOWN_PUBLIC_ACTIONS)


class SitePolicyUpdate(BaseModel):
    public_actions: list[str]


@router.get("/policy", response_model=SitePolicy, summary="Public action policy")
async def site_policy(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """Which mutating actions are open to anonymous clients (admin-curated)."""
    return {"public_actions": sorted(await get_public_actions(session))}


@router.put("/policy", response_model=SitePolicy, summary="Curate the public action policy")
async def update_site_policy(
    body: SitePolicyUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _admin: Annotated[dict[str, Any], Depends(RequireScopes(ADMIN_WRITE))],
) -> dict[str, Any]:
    """Replace the admin-curated public-action list (unknown actions 422).
    Empty list = default deny: every mutation needs an admin identity.
    Interactive session only: a leaked key must not widen access."""
    require_jwt_session(_admin)
    unknown = sorted(set(body.public_actions) - set(KNOWN_PUBLIC_ACTIONS))
    if unknown:
        raise AppError(
            "Unknown public action",
            status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown action(s): {', '.join(unknown)} "
                   f"(known: {', '.join(KNOWN_PUBLIC_ACTIONS)})",
            code="policy:unknown-action",
        )
    ordered = sorted(set(body.public_actions))
    row = await session.get(Setting, PUBLIC_ACTIONS_KEY)
    if row is None:
        session.add(Setting(key=PUBLIC_ACTIONS_KEY, value=ordered))
    else:
        row.value = ordered
    await session.commit()
    return {"public_actions": ordered}
