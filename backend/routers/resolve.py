"""Resolve router: JSON debug endpoint + the redirect hot path.

GET /api/v1/resolve  — resolver debug (always 200, `outcome` field).
GET /{pattern}        — permanent home of the redirect hot path (unversioned).
  Dual-serve note (EPIC-08 M1–M2): while Flask still serves HTML, the proxy
  keeps THIS route shadowed and only /api/v1/* reaches FastAPI. At M3 the
  proxy flips and this becomes the live redirect path — no code change.

Response mapping: redirect → 302 (or minimal countdown page when the
auto-redirect delay is > 0); need_params → 422 problem (React renders the
usage UI); not_found → 404 + suggestions; gone → 410; forbidden → 403;
unsafe → 400. SSO targets always get no-store (v2 parity).
"""

from __future__ import annotations

import html

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.cache import MemoryCache
from backend.core.config import settings
from backend.core.db import get_session
from backend.modules.shortcuts.repository import SQLAlchemyShortcutRepository, is_sso_url
from backend.modules.shortcuts.schemas import Problem
from backend.modules.shortcuts.service import resolve

router = APIRouter()

# M1 single-process cache. EPIC-04 swaps this for RedisCache built from
# settings when REDIRECTOR_REDIS_URL is configured (same Cache protocol).
_memory_cache = MemoryCache()


async def get_repo(session: AsyncSession = Depends(get_session)) -> SQLAlchemyShortcutRepository:
    return SQLAlchemyShortcutRepository(session, _memory_cache)


def _no_store(response: Response, target: str | None) -> None:
    if target and is_sso_url(target):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"


def _countdown_page(target: str, delay: int, source: str | None) -> str:
    safe = html.escape(target, quote=True)
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<title>Redirecting…</title><meta http-equiv="refresh" content="{delay};url={safe}">
<meta name="robots" content="noindex"></head>
<body><p>Redirecting to <a href="{safe}">{safe}</a> in {delay}s…</p>
<!-- via redirector ({html.escape(source or 'db')}) --></body></html>"""


@router.get("/api/v1/resolve", summary="Resolve a subpath (debug)")
async def api_resolve(pattern: str, request: Request,
                      repo: SQLAlchemyShortcutRepository = Depends(get_repo)) -> JSONResponse:
    res = await resolve(
        pattern, repo, countdown_delay=settings.auto_redirect_delay,
        client_ip=request.client.host if request.client else None,
    )
    return JSONResponse(res.model_dump())


@router.get("/{pattern:path}", summary="Resolve and redirect a shortcut")
async def redirect_shortcut(pattern: str, request: Request,
                            repo: SQLAlchemyShortcutRepository = Depends(get_repo)) -> Response:
    res = await resolve(
        pattern, repo, countdown_delay=settings.auto_redirect_delay,
        client_ip=request.client.host if request.client else None,
    )
    if res.outcome == "redirect":
        assert res.target is not None
        resp: Response
        if res.countdown_delay > 0:
            resp = HTMLResponse(_countdown_page(res.target, res.countdown_delay, res.source))
        else:
            resp = Response(status_code=302, headers={"Location": res.target})
        _no_store(resp, res.target)
        return resp
    status = {"need_params": 422, "not_found": 404, "gone": 410,
              "forbidden": 403, "unsafe": 400}[res.outcome]
    problem = Problem(
        title={"need_params": "Missing shortcut parameters",
               "not_found": "Shortcut not found",
               "gone": "Shortcut expired",
               "forbidden": "Shortcut is private",
               "unsafe": "Unsafe redirect target"}[res.outcome],
        status=status, detail=res.pattern, code=f"resolve:{res.outcome}",
        suggestions=res.suggestions, missing_params=res.missing_params,
    )
    return JSONResponse(problem.model_dump(), status_code=status)
