"""Public site policy (no auth): which mutating actions the admin opened up."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.db import get_session
from backend.core.policy import KNOWN_PUBLIC_ACTIONS, get_public_actions

router = APIRouter(prefix="/api/v1/site", tags=["site"])


class SitePolicy(BaseModel):
    public_actions: list[str]
    known_actions: list[str] = list(KNOWN_PUBLIC_ACTIONS)


@router.get("/policy", response_model=SitePolicy, summary="Public action policy")
async def site_policy(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """Which mutating actions are open to anonymous clients (admin-curated)."""
    return {"public_actions": sorted(await get_public_actions(session))}
