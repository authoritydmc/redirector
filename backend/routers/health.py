"""Liveness + readiness. No auth, no DB required for /healthz."""

import asyncio

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["ops"])


class Health(BaseModel):
    status: str = "ok"


class Readiness(BaseModel):
    status: str
    version: str
    data_dir_writable: bool


@router.get("/healthz", response_model=Health, summary="Liveness probe")
@router.get("/health", response_model=Health, summary="Liveness probe (alias)")
async def healthz() -> Health:
    return Health()


@router.get("/readyz", response_model=Readiness, summary="Readiness probe")
async def readyz() -> Readiness:
    from backend.core.config import settings  # local import: keeps router import cheap

    def _probe() -> bool:
        try:
            settings.data_dir.mkdir(parents=True, exist_ok=True)
            probe = settings.data_dir / ".readyz-probe"
            probe.touch()
            probe.unlink()
            return True
        except OSError:
            return False

    # Filesystem I/O is sync — run off the event loop.
    writable = await asyncio.to_thread(_probe)
    return Readiness(
        status="ready" if writable else "degraded",
        version=settings.app_version,
        data_dir_writable=writable,
    )
