"""FastAPI router for system and KPI metrics."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.db import get_session
from backend.modules.metrics.schemas import KpiResponse, LiveResponse
from backend.modules.metrics.service import MetricsService

router = APIRouter(prefix="/api/v1/metrics", tags=["metrics"])


def get_metrics_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MetricsService:
    return MetricsService(session=session)


@router.get("/kpi", response_model=KpiResponse, summary="Aggregated KPI metrics")
async def get_kpi_metrics(
    service: Annotated[MetricsService, Depends(get_metrics_service)],
) -> KpiResponse:
    """Return aggregated KPI metrics (totals, breakdowns, top tags, popularity)."""
    return KpiResponse.model_validate(await service.get_kpi_metrics())


@router.get("/live", response_model=LiveResponse, summary="Live process telemetry")
async def get_live_metrics(
    service: Annotated[MetricsService, Depends(get_metrics_service)],
) -> LiveResponse:
    """Return process runtime telemetry and live counter snapshots."""
    return LiveResponse.model_validate(await service.get_live_metrics())
