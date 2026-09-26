"""Liveness + readiness. No auth, no DB required for /healthz."""

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
async def healthz() -> Health:
    return Health()


@router.get("/readyz", response_model=Readiness, summary="Readiness probe")
async def readyz() -> Readiness:
    from backend.core.config import settings  # local import: keeps router import cheap

    writable = False
    try:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        probe = settings.data_dir / ".readyz-probe"
        probe.touch()
        probe.unlink()
        writable = True
    except OSError:
        writable = False
    return Readiness(
        status="ready" if writable else "degraded",
        version=settings.app_version,
        data_dir_writable=writable,
    )
