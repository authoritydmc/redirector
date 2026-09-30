"""FastAPI router for system and KPI metrics."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.db import get_session
from backend.modules.metrics.service import MetricsService

router = APIRouter(prefix="/api/v1/metrics", tags=["metrics"])


def get_metrics_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MetricsService:
    return MetricsService(session=session)


@router.get("/kpi")
async def get_kpi_metrics(
    service: Annotated[MetricsService, Depends(get_metrics_service)],
) -> dict:
    """Return aggregated KPI metrics (totals, breakdowns, top tags, popularity)."""
    return await service.get_kpi_metrics()


@router.get("/live")
async def get_live_metrics(
    service: Annotated[MetricsService, Depends(get_metrics_service)],
) -> dict:
    """Return process runtime telemetry and live counter snapshots."""
    return await service.get_live_metrics()
