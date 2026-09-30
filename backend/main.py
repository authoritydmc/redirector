"""Redirector v3 API entrypoint (replaces Flask's app/__init__.py + wsgi.py).

Run:  uvicorn backend.main:app --reload
Docs: /docs (Swagger), /openapi.json
"""

import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response

from backend.core.config import settings
from backend.core.errors import register_error_handlers
from backend.routers import auth as auth_router
from backend.routers import config as config_router
from backend.routers import health
from backend.routers import metrics as metrics_router
from backend.routers import qr as qr_router
from backend.routers import resolve as resolve_router
from backend.routers import shortcuts as shortcuts_router
from backend.routers import upstreams as upstreams_router

logger = logging.getLogger("redirector")
access_logger = logging.getLogger("redirector.access")


def configure_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # M1: stateless boot. EPIC-04 adds: migrate → seed → warm cache here.
    logger.info("redirector v%s starting (data_dir=%s)", settings.app_version, settings.data_dir)
    yield
    logger.info("redirector shutting down")


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(
        title="Redirector API",
        description="Async FastAPI backend for Redirector (EPIC-01). "
        "Shortcuts, upstreams, resolve hot path, auth, metrics, QR.",
        version=settings.app_version,
        lifespan=lifespan,
        openapi_tags=[
            {"name": "ops", "description": "Liveness / readiness probes"},
            {"name": "shortcuts", "description": "Shortcut CRUD + bulk operations"},
            {"name": "upstreams", "description": "Upstream CRUD, cache, live checks"},
            {"name": "metrics", "description": "KPI aggregates + process telemetry"},
            {"name": "admin-config", "description": "DB-backed settings (admin only)"},
            {"name": "auth", "description": "Login + current admin"},
            {"name": "qr", "description": "QR code generation"},
            {"name": "resolve", "description": "Redirect hot path + debug"},
        ],
    )
    register_error_handlers(app)

    @app.middleware("http")
    async def access_log(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000
        access_logger.info(
            "%s %s -> %s %.1fms",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
        )
        return response

    app.include_router(health.router)
    app.include_router(auth_router.router)
    app.include_router(config_router.router)
    app.include_router(qr_router.router)
    app.include_router(shortcuts_router.router)
    app.include_router(upstreams_router.router)
    app.include_router(metrics_router.router)
    # Catch-all /{pattern} lives in resolve_router: register LAST so
    # /healthz, /docs, /openapi.json and /api/* keep matching first.
    app.include_router(resolve_router.router)
    return app


app = create_app()
