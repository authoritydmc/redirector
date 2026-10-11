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
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.cache import build_cache
from backend.core.config import resolve_setting, settings
from backend.core.db import get_session
from backend.core.policy import get_public_actions
from backend.modules.shortcuts.repository import SQLAlchemyShortcutRepository, is_sso_url
from backend.modules.shortcuts.schemas import Problem
from backend.modules.shortcuts.service import is_safe_redirect_target, resolve
from backend.modules.upstreams.repository import UpstreamRepository
from backend.modules.upstreams.service import UpstreamCheckService

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


_CHECK_STATUS_TEXT = {
    "found": "found — unsafe target, not followed",
    "sso_required": "SSO login required",
    "not_found": "not found",
    "error": "check failed",
    "skipped": "skipped",
}


def _missing_page(pattern: str, suggestions: list[Any],
                  checks: list[dict[str, str]], creation_open: bool) -> str:
    """Human 404 for browser navigation: what was checked (shortcut miss +
    one row per live upstream check, staggered in), close matches, and the
    path to creating the link. API clients keep the JSON problem (see
    below) — HTML is served only on `Accept: text/html`."""
    safe_pattern = html.escape(pattern, quote=True)
    names = [getattr(s, "pattern", "") or "" for s in (suggestions or [])]
    names = [n for n in names if n][:5]
    items = "".join(
        f"<li><a href=\"/{html.escape(n.lstrip('/'), quote=True)}\">r/{html.escape(n, quote=False)}</a></li>"
        for n in names
    )
    suggest = f"<ul>{items}</ul>" if items else ""
    if checks:
        rows = "".join(
            f"<li class=\"check\" style=\"animation-delay:{i * 160}ms\">"
            f"<span class=\"dot\" aria-hidden=\"true\"></span>"
            f"<span class=\"uname\">{html.escape(c['upstream'], quote=False)}</span>"
            f"<span class=\"pill\">{html.escape(c['text'], quote=False)}</span>"
            f"</li>"
            for i, c in enumerate(checks)
        )
        checked = (f"<p class=\"sub\">No shortcut — checked {len(checks)} "
                   f"upstream{'s' if len(checks) != 1 else ''} live:</p>"
                   f"<ul class=\"checks\">{rows}</ul>"
                   f"<p class=\"recheck\"><a href=\"/{html.escape(pattern.strip().strip('/'), quote=True)}\">Re-check</a></p>")
    else:
        checked = ("<p class=\"sub\">No shortcut, and no upstreams are configured to ask.</p>")
    if creation_open:
        action = (f"<p><a class=\"create\" href=\"/?new={html.escape(pattern, quote=True)}\">"
                  f"Create r/{safe_pattern}</a></p>")
    else:
        action = ("<p class=\"sub\">Creating links needs sign-in — "
                  "<a href=\"/login\">sign in as admin</a>.</p>")
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>r/{safe_pattern} — not found</title>
<meta name="robots" content="noindex">
<meta name="color-scheme" content="light dark">
<style>
body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;background:#f7f0e6;color:#3a2e22;font-family:'Space Grotesk',system-ui,sans-serif}}
.card{{max-width:28rem;width:calc(100% - 2rem);background:#fffdf8;border:1px solid #e2d5c0;border-radius:1rem;padding:2rem;text-align:center;box-shadow:0 20px 50px rgba(58,46,34,.15)}}
.pattern{{font-size:1.5rem;font-weight:700;overflow-wrap:anywhere;margin:0 0 .25rem}}
.sub{{color:#6b5d4d;margin:.5rem 0}}
ul{{list-style:none;margin:.5rem 0;padding:0}}
.checks{{text-align:left}}
.check{{display:flex;align-items:center;gap:.5rem;border:1px solid #e2d5c0;border-radius:.6rem;padding:.4rem .6rem;margin-bottom:.4rem;opacity:0;animation:rise .4s ease-out forwards}}
@keyframes rise{{from{{opacity:0;transform:translateY(6px)}}to{{opacity:1;transform:none}}}}
@media (prefers-reduced-motion:reduce){{.check{{opacity:1;animation:none}}}}
.dot{{width:.5rem;height:.5rem;border-radius:9999px;background:#9a3412;flex:none}}
.uname{{font-family:monospace;font-size:.8rem}}
.pill{{margin-left:auto;font-size:.75rem;color:#6b5d4d}}
li a{{color:#9a3412}}
.create{{display:inline-block;border-radius:.75rem;padding:.7rem 1.4rem;font-weight:700;text-decoration:none;background:#9a3412;color:#fff}}
.recheck,.back{{font-size:.85rem;color:#6b5d4d}}
</style></head>
<body><div class="card" role="status">
<p class="pattern">r/{safe_pattern}</p>
<p class="sub">doesn&rsquo;t exist yet.</p>
{checked}
{suggest}
{action}
<p><a class="back" href="/">Back to Shortcuts</a></p>
</div></body></html>"""


async def _browser_missing_response(
    session: AsyncSession,
    wanted: str,
    suggestions: list[Any],
    delay: int,
) -> Response:
    """Browser 404 flow: shortcut missed, so ask every upstream live (same
    fan-out as the check stream). A live hit redirects like any other;
    otherwise render the report page with one row per check."""
    upstreams = await UpstreamRepository(session).list_all()
    checks: list[dict[str, str]] = []
    if upstreams:
        service = UpstreamCheckService(UpstreamRepository(session))
        timeout = httpx.Timeout(3.0, connect=3.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            results = await service.check_all(upstreams, wanted, client)
        for result in results:
            target = result.target_url or ""
            if result.status == "found" and target:
                if is_safe_redirect_target(target):
                    if delay > 0:
                        return HTMLResponse(_countdown_page(target, delay, "upstream"))
                    return Response(status_code=302, headers={"Location": target})
            text = _CHECK_STATUS_TEXT.get(result.status, result.status)
            if result.status == "error" and result.message:
                text = result.message[:120]
            checks.append({
                "upstream": result.upstream_name,
                "text": text,
            })
    public = "shortcuts.create" in await get_public_actions(session)
    return HTMLResponse(_missing_page(wanted, suggestions, checks, public), status_code=404)


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
                      repo: SQLAlchemyShortcutRepository = Depends(get_repo),
                      session: AsyncSession = Depends(get_session)) -> JSONResponse:
    delay, _ = await resolve_setting(session, "auto_redirect_delay")
    res = await resolve(
        pattern, repo, countdown_delay=delay,
        client_ip=request.client.host if request.client else None,
    )
    return JSONResponse(res.model_dump())


@router.get("/{pattern:path}", summary="Resolve and redirect a shortcut")
async def redirect_shortcut(pattern: str, request: Request,
                            repo: SQLAlchemyShortcutRepository = Depends(get_repo),
                            session: AsyncSession = Depends(get_session)) -> Response:
    delay, _ = await resolve_setting(session, "auto_redirect_delay")
    res = await resolve(
        pattern, repo, countdown_delay=delay,
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
    if res.outcome == "not_found" and "text/html" in (request.headers.get("accept") or ""):
        wanted = pattern.strip().strip("/")
        return await _browser_missing_response(session, wanted, res.suggestions or [], delay)
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
