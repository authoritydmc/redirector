"""Redirector v3 API entrypoint (replaces Flask's app/__init__.py + wsgi.py).

Run:  uvicorn backend.main:app --reload
Docs: /docs (Swagger), /openapi.json
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.core.config import settings
from backend.routers import health


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # M1: stateless boot. EPIC-04 adds: migrate → seed → warm cache here.
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Redirector API",
        version=settings.app_version,
        lifespan=lifespan,
    )
    app.include_router(health.router)
    return app


app = create_app()
