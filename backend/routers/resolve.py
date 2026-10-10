"""Resolve router: JSON debug endpoint + the redirect hot path.

GET /api/v1/resolve  — resolver debug (always 200, `outcome` field).
GET /{pattern}        — the live redirect path (catch-all, registered last).

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

from backend.core.cache import build_cache
from backend.core.config import settings
from backend.core.db import get_session
from backend.modules.shortcuts.repository import SQLAlchemyShortcutRepository, is_sso_url
from backend.modules.shortcuts.schemas import Problem
from backend.modules.shortcuts.service import resolve

router = APIRouter()

# Process-wide lookup cache (EPIC-04): per-process memory by default, shared
# Redis with REDIRECTOR_CACHE_BACKEND=redis. Same Cache protocol either way.
_cache = build_cache(settings.cache_backend, settings.redis_url)


async def get_repo(session: AsyncSession = Depends(get_session)) -> SQLAlchemyShortcutRepository:
    return SQLAlchemyShortcutRepository(session, _cache)


def _no_store(response: Response, target: str | None) -> None:
    if target and is_sso_url(target):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"


def _display_host(target: str) -> str:
    """Hostname for the countdown headline; falls back to the raw target."""
    try:
        from urllib.parse import urlsplit

        host = urlsplit(target).hostname or ""
        return host if host else target
    except ValueError:
        return target


def _countdown_page(target: str, delay: int, source: str | None) -> str:
    safe = html.escape(target, quote=True)
    host = html.escape(_display_host(target), quote=True)
    icon = (
        "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' "
        "viewBox='0 0 24 24' fill='none' stroke='%23a78bfa' stroke-width='2' "
        "stroke-linecap='round' stroke-linejoin='round'%3E%3Crect width='24' "
        "height='24' rx='6' fill='%2314101f' stroke='none'/%3E%3Cpath d='M10 "
        "13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71'/%3E%3Cpath "
        "d='M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71'/%3E%3C/svg%3E"
    )
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Redirecting to {host}…</title><link rel="icon" href="{icon}">
<meta http-equiv="refresh" content="{delay};url={safe}">
<meta name="robots" content="noindex">
<meta name="color-scheme" content="dark light">
<style>
body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;background:#14101f;color:#f2efff;font-family:'Space Grotesk',system-ui,sans-serif}}
.card{{max-width:26rem;width:calc(100% - 2rem);background:#1d1730;border:1px solid #352c52;border-radius:1rem;padding:2rem;text-align:center;box-shadow:0 20px 50px rgba(0,0,0,.4)}}
.mark{{width:2.5rem;height:2.5rem;margin:0 auto}}
.host{{font-size:1.25rem;font-weight:700;overflow-wrap:anywhere;margin:.75rem 0 .25rem}}
.count{{font-size:2rem;font-weight:700;color:#a78bfa}}
.go,.stay{{display:inline-block;margin-top:1rem;border-radius:.5rem;padding:.6rem 1.2rem;font-size:.9rem;text-decoration:none;cursor:pointer;border:1px solid transparent}}
.go{{background:#a78bfa;color:#1c1038;font-weight:700}}
.stay{{background:transparent;color:#b3a8d6;border-color:#352c52;margin-left:.5rem}}
.hint{{margin-top:1rem;font-size:.75rem;color:#b3a8d6;overflow-wrap:anywhere}}
</style></head>
<body><div class="card" role="status">
<img class="mark" alt="" src="{icon}">
<p class="host">{host}</p>
<p>Redirecting in <span class="count" id="n">{delay}</span>s…</p>
<p><a class="go" href="{safe}">Go now</a><button class="stay" type="button" onclick="window.stop();document.getElementById('n').textContent='—'">Stay here</button></p>
<p class="hint">{safe}</p>
</div>
<script>var n={delay},el=document.getElementById('n');var t=setInterval(function(){{n-=1;if(n<=0){{clearInterval(t)}}else{{el.textContent=n}}}},1000);</script>
</body></html>"""


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
