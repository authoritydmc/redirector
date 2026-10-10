"""FastAPI router for application configuration and settings."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import (
    EDITABLE_SCHEMA,
    ENV_MANAGED_SETTINGS,
    coerce_setting,
    resolve_setting,
    settings,
)
from backend.core.db import get_session
from backend.core.errors import AppError
from backend.core.security import ADMIN_READ, ADMIN_WRITE, RequireScopes
from backend.models.entities import Setting, utcnow

router = APIRouter(prefix="/api/v1/admin/config", tags=["admin-config"])


class ConfigUpdate(BaseModel):
    settings: dict[str, Any]


@router.get("", response_model=dict[str, Any], summary="Get system configuration")
async def get_config(
    _admin: Annotated[dict[str, Any], Depends(RequireScopes(ADMIN_READ))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """Return active system configuration, editable schema, and stored settings."""
    db_settings_res = await session.execute(select(Setting))
    db_settings = {s.key: s.value for s in db_settings_res.scalars().all()}

    schema: list[dict[str, Any]] = []
    for key, meta in EDITABLE_SCHEMA.items():
        value, source = await resolve_setting(session, key)
        schema.append({
            "key": key,
            "title": meta["title"],
            "description": meta["description"],
            "type": meta.get("type", "int"),
            "min": meta.get("min"),
            "max": meta.get("max"),
            "env_var": "REDIRECTOR_" + key.upper(),
            "value": value,
            "source": source,
            "readonly": source == "environment",
        })
    for item in ENV_MANAGED_SETTINGS:
        schema.append({**item, "type": "secret", "value": None, "source": "environment", "readonly": True})

    return {
        "app_name": settings.app_name,
        "app_version": settings.app_version,
        "log_level": settings.log_level,
        "auto_redirect_delay": settings.auto_redirect_delay,
        "custom": db_settings,
        "schema": schema,
    }


@router.patch("", response_model=dict[str, Any], summary="Update system settings")
async def update_config(
    payload: ConfigUpdate,
    _admin: Annotated[dict[str, Any], Depends(RequireScopes(ADMIN_WRITE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """Update settings entries in the database.

    Known editable keys are validated + coerced (422 otherwise); anything
    else is stored verbatim under `custom` for import fidelity.
    """
    for key, val in payload.settings.items():
        if key in EDITABLE_SCHEMA:
            try:
                val = coerce_setting(key, val)
            except ValueError as exc:
                raise AppError(
                    "Invalid setting value",
                    status=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=str(exc),
                    code="admin:invalid-setting",
                ) from None
        existing = await session.get(Setting, key)
        if existing:
            existing.value = val
            existing.updated_at = utcnow()
        else:
            session.add(Setting(key=key, value=val))
    await session.commit()
    return {"status": "updated", "keys": list(payload.settings.keys())}
