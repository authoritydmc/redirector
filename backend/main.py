"""Redirector v3 API entrypoint (replaces Flask's app/__init__.py + wsgi.py).

Run:  uvicorn backend.main:app --reload
Docs: /docs (Swagger), /openapi.json
"""

import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy.exc import OperationalError

from backend.core.config import settings
from backend.core.db import SessionFactory
from backend.core.db import engine as db_engine
from backend.core.errors import register_error_handlers
from backend.modules.jobs.redis import redis_settings_from_url
from backend.modules.jobs.runner import ArqJobRunner, JobRunner
from backend.routers import auth as auth_router
from backend.routers import backup as backup_router
from backend.routers import config as config_router
from backend.routers import health
from backend.routers import jobs as jobs_router
from backend.routers import metrics as metrics_router
from backend.routers import qr as qr_router
from backend.routers import resolve as resolve_router
from backend.routers import shortcuts as shortcuts_router
from backend.routers import site as site_router
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
    if not hasattr(app.state, "jobs"):
        if settings.job_backend == "arq":
            app.state.jobs = ArqJobRunner(
                SessionFactory, redis_settings_from_url(settings.redis_url))
        else:
            app.state.jobs = JobRunner(SessionFactory)
    app.state.rate_limit_store = {}
    try:
        reaped = await app.state.jobs.reap_stale()
    except OperationalError:
        # Fresh database whose tables are created lazily (tests) or an
        # install that hasn't bootstrapped yet: nothing to reap.
        logger.debug("jobs table not present yet, skipping stale reap")
        reaped = 0
    if reaped:
        logger.warning("marked %d stale job(s) failed (previous process)", reaped)
    logger.info("redirector v%s starting (data_dir=%s)", settings.app_version, settings.data_dir)
    yield
    runner: JobRunner | None = getattr(app.state, "jobs", None)
    if runner is not None:
        await runner.shutdown()
    # Drop pooled DB connections: loop-pinned aiosqlite handles hang
    # interpreter exit otherwise (see workers/settings.on_shutdown).
    await db_engine.dispose()
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
            {"name": "admin-backup", "description": "Backup archives (admin only)"},
            {"name": "auth", "description": "Login + current admin"},
            {"name": "jobs", "description": "Background jobs + progress streams"},
            {"name": "qr", "description": "QR code generation"},
            {"name": "resolve", "description": "Redirect hot path + debug"},
            {"name": "site", "description": "Public site policy"},
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
    app.include_router(backup_router.router)
    app.include_router(jobs_router.router)
    app.include_router(config_router.router)
    app.include_router(qr_router.router)
    app.include_router(shortcuts_router.router)
    app.include_router(site_router.router)
    app.include_router(upstreams_router.router)
    app.include_router(metrics_router.router)
    _mount_spa(app)
    # Catch-all /{pattern} lives in resolve_router: register LAST so
    # /healthz, /docs, /openapi.json, /api/* and /app/* keep matching first.
    app.include_router(resolve_router.router)
    return app


def _mount_spa(app: FastAPI) -> None:
    """Serve the React build at /app when it exists (EPIC-02 task 7 / M2).

    Manual file serving (not a StaticFiles mount, which answers its own
    404s): unknown /app/* paths fall back to index.html for client-side
    routing, and the mount degrades to nothing when no build is present
    (backend CI installs no node).
    """
    dist = settings.spa_dir
    index = dist / "index.html"
    if not index.is_file():
        return
    logger.info("serving SPA from %s at /app", dist)

    async def spa_root() -> FileResponse:
        return FileResponse(index)

    async def spa_file(path: str) -> FileResponse:
        candidate = (dist / path).resolve() if path else index
        if path and candidate.is_file() and _is_within(dist, candidate):
            return FileResponse(candidate)
        return FileResponse(index)

    app.get("/app", include_in_schema=False)(spa_root)
    app.get("/app/{path:path}", include_in_schema=False)(spa_file)


def _is_within(root: Path, candidate: Path) -> bool:
    """True when `candidate` resolves inside `root` (normalizes `..`)."""
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError, RuntimeError):
        return False


app = create_app()
