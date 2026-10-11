"""SSO router: multi-IdP OIDC logins with JIT provisioning (EPIC-05 task 6).

Browser flows (redirects, not JSON): the IdP calls back here, we answer
with a 302 to the SPA login page carrying either `#access_token=...` or
`#error=<code>`. Fragments never reach server logs; the SPA adopts the
token into the same JWT session local logins use, so all downstream
RBAC/API-key logic is identical.

Error codes (fragment `error`): unknown-provider, invalid-state,
bad-discovery, token-exchange, invalid-token, bad-claims, not-authorized.
"""

from __future__ import annotations

from typing import Annotated, Any
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import settings
from backend.core.db import get_session
from backend.core.errors import AppError
from backend.core.ratelimit import RateLimit
from backend.core.security import create_access_token, log_auth_event
from backend.modules.sso import oidc
from backend.modules.sso.providers import (
    ConfiguredProvider,
    configured_providers,
    get_configured_provider,
)
from backend.modules.sso.repository import OidcStateRepository, SsoUserRepository
from backend.modules.sso.service import ADMIN_ROLE, resolve_role

router = APIRouter(prefix="/api/v1/auth", tags=["sso"])

# Own bucket (not the password-login one): IdP round-trips are user-paced,
# and a shared bucket would let SSO starts starve password logins.
sso_limiter = RateLimit("20/minute")


class SsoProviderRead(BaseModel):
    id: str
    display_name: str


def _login_url_with(error: str | None = None, token: str | None = None) -> str:
    base = settings.sso_login_url
    if error is not None:
        return f"{base}#error={quote(error)}"
    return f"{base}#access_token={quote(token or '')}"


def _deny(login_url_error: str) -> RedirectResponse:
    return RedirectResponse(url=_login_url_with(error=login_url_error),
                            status_code=status.HTTP_302_FOUND)


def _callback_url(request: Request, provider_id: str) -> str:
    base = str(request.base_url).rstrip("/")
    return f"{base}/api/v1/auth/oidc/{provider_id}/callback"


def _provider_or_404(provider_id: str) -> ConfiguredProvider:
    provider = get_configured_provider(provider_id)
    if provider is None:
        raise AppError(
            "Unknown SSO provider",
            status=status.HTTP_404_NOT_FOUND,
            detail=f"No configured OIDC provider {provider_id!r} "
                   "(declare it and set its client-secret env var)",
            code="sso:unknown-provider",
        )
    return provider


@router.get("/providers", response_model=list[SsoProviderRead], summary="List SSO providers")
async def list_providers() -> list[SsoProviderRead]:
    """Login-ready IdP buttons (public — ids and display names only)."""
    return [SsoProviderRead(id=p.id, display_name=p.display_name)
            for p in configured_providers()]


@router.get("/oidc/{provider_id}/login", summary="Start an OIDC login")
async def oidc_login(
    provider_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    _rate_limited: Annotated[None, Depends(sso_limiter)],
) -> RedirectResponse:
    """302 to the IdP with PKCE + state + nonce; the verifier waits in
    `oidc_states` for the callback (10-minute TTL, one-shot)."""
    provider = _provider_or_404(provider_id)
    try:
        metadata = await oidc.fetch_discovery(provider.config.discovery_url)
    except (httpx.HTTPError, ValueError) as exc:
        raise AppError(
            "Identity provider unreachable",
            status=status.HTTP_502_BAD_GATEWAY,
            detail=f"Discovery failed for {provider_id!r}: {exc}",
            code="sso:bad-discovery",
        ) from exc
    try:
        challenge = oidc.build_login_url(
            metadata, provider, _callback_url(request, provider.id))
    except ValueError as exc:
        raise AppError(
            "Identity provider misconfigured",
            status=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
            code="sso:bad-discovery",
        ) from exc
    await OidcStateRepository(session).create(
        challenge.state, provider.id, challenge.code_verifier, challenge.nonce)
    return RedirectResponse(url=challenge.url, status_code=status.HTTP_302_FOUND)


@router.get("/oidc/{provider_id}/callback", summary="Finish an OIDC login")
async def oidc_callback(
    provider_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    _rate_limited: Annotated[None, Depends(sso_limiter)],
    code: str | None = None,
    state: str | None = None,
) -> RedirectResponse:
    """Exchange the code (PKCE), verify the ID token, JIT-provision, and
    mint the standard admin JWT for group-mapped users. Every failure
    redirects to the login page with a machine-readable `#error=` code."""
    provider = get_configured_provider(provider_id)
    if provider is None or not code or not state:
        return _deny("unknown-provider" if provider is None else "invalid-state")
    states = OidcStateRepository(session)
    stored = await states.consume(state, provider.id)
    if stored is None:
        return _deny("invalid-state")
    try:
        metadata = await oidc.fetch_discovery(provider.config.discovery_url)
    except (httpx.HTTPError, ValueError):
        return _deny("bad-discovery")
    try:
        token = await oidc.exchange_code(
            metadata, provider, _callback_url(request, provider.id),
            code, stored.code_verifier)
        claims = await oidc.parse_token(metadata, provider, token, stored.nonce)
        oidc.verify_claims(claims, provider, stored.nonce)
    except (httpx.HTTPError, ValueError) as exc:
        await log_auth_event(session, "sso.token_failed", _ip(request),
                             {"provider": provider.id, "reason": str(exc)[:120]})
        return _deny("invalid-token")
    subject = claims.get("sub")
    if not subject or not isinstance(subject, str):
        return _deny("bad-claims")
    role = resolve_role(claims, provider.config)
    users = SsoUserRepository(session)
    user = await users.upsert_login(
        provider.id, subject,
        _claim_str(claims.get("email")), _claim_str(claims.get("name")), role)
    if role != ADMIN_ROLE:
        await log_auth_event(session, "sso.denied", _ip(request),
                             {"provider": provider.id, "user_id": user.id})
        return _deny("not-authorized")
    await log_auth_event(session, "sso.login", _ip(request),
                         {"provider": provider.id, "user_id": user.id})
    jwt = create_access_token(data={"sub": f"sso:{provider.id}:{subject}", "role": "admin"})
    return RedirectResponse(url=_login_url_with(token=jwt),
                            status_code=status.HTTP_302_FOUND)


def _claim_str(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _ip(request: Request) -> str | None:
    host = request.client.host if request.client else None
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip().lower()
    return host
