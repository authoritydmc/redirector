"""FastAPI router for application configuration and settings."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import settings
from backend.core.db import get_session
from backend.core.security import get_current_admin
from backend.models.entities import Setting, utcnow

router = APIRouter(prefix="/api/v1/admin/config", tags=["admin-config"])


class ConfigUpdate(BaseModel):
    settings: dict[str, Any]


@router.get("", response_model=dict[str, Any], summary="Get system configuration")
async def get_config(
    _admin: Annotated[dict[str, Any], Depends(get_current_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """Return active system configuration and stored settings."""
    db_settings_res = await session.execute(select(Setting))
    db_settings = {s.key: s.value for s in db_settings_res.scalars().all()}

    return {
        "app_name": settings.app_name,
        "app_version": settings.app_version,
        "log_level": settings.log_level,
        "auto_redirect_delay": settings.auto_redirect_delay,
        "custom": db_settings,
    }


@router.patch("", response_model=dict[str, Any], summary="Update system settings")
async def update_config(
    payload: ConfigUpdate,
    _admin: Annotated[dict[str, Any], Depends(get_current_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """Update settings entries in the database."""
    for key, val in payload.settings.items():
        existing = await session.get(Setting, key)
        if existing:
            existing.value = val
            existing.updated_at = utcnow()
        else:
            session.add(Setting(key=key, value=val))
    await session.commit()
    return {"status": "updated", "keys": list(payload.settings.keys())}
