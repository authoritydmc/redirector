"""Authorization-code + PKCE against any OIDC discovery document.

Thin authlib wrappers stay isolated here so endpoint tests can stub the
network boundary (`fetch_discovery`, `exchange_code`, `parse_token`)
while the pure pieces (`build_login_url`, `verify_claims`) are unit-tested
directly. PKCE values are generated explicitly (never read off client
internals) so the stored verifier is exactly what the IdP must see back.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from typing import Any

import httpx
from authlib.integrations.httpx_client import AsyncOAuth2Client

from backend.modules.sso.providers import ConfiguredProvider

DISCOVERY_TIMEOUT_SECONDS = 10.0
TOKEN_TIMEOUT_SECONDS = 15.0
CLOCK_SKEW_LEEWAY_SECONDS = 60


@dataclass(frozen=True)
class LoginChallenge:
    url: str
    state: str
    nonce: str
    code_verifier: str


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def pkce_pair() -> tuple[str, str]:
    """(code_verifier, code_challenge[S256]) per RFC 7636."""
    verifier = _b64url(secrets.token_bytes(64))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


async def fetch_discovery(discovery_url: str) -> dict[str, Any]:
    """GET the `.../.well-known/openid-configuration` document."""
    async with httpx.AsyncClient(timeout=DISCOVERY_TIMEOUT_SECONDS) as client:
        resp = await client.get(discovery_url)
        resp.raise_for_status()
        data = resp.json()
    if not isinstance(data, dict):
        raise ValueError("discovery document is not a JSON object")
    return data


def build_login_url(
    metadata: dict[str, Any],
    provider: ConfiguredProvider,
    redirect_uri: str,
) -> LoginChallenge:
    """Authorization URL + the secrets the callback must see again."""
    authorization_endpoint = metadata.get("authorization_endpoint")
    if not authorization_endpoint:
        raise ValueError("discovery document lacks authorization_endpoint")
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier, challenge = pkce_pair()
    client = AsyncOAuth2Client(
        client_id=provider.config.client_id,
        redirect_uri=redirect_uri,
        scope="openid email profile",
    )
    url, _ = client.create_authorization_url(
        authorization_endpoint,
        state=state,
        nonce=nonce,
        code_challenge=challenge,
        code_challenge_method="S256",
    )
    return LoginChallenge(url=url, state=state, nonce=nonce, code_verifier=verifier)


async def exchange_code(
    metadata: dict[str, Any],
    provider: ConfiguredProvider,
    redirect_uri: str,
    code: str,
    code_verifier: str,
) -> dict[str, Any]:
    """Trade the callback `code` for tokens (PKCE verifier required)."""
    token_endpoint = metadata.get("token_endpoint")
    if not token_endpoint:
        raise ValueError("discovery document lacks token_endpoint")
    client = AsyncOAuth2Client(
        client_id=provider.config.client_id,
        client_secret=provider.client_secret,
        redirect_uri=redirect_uri,
        scope="openid email profile",
    )
    token = await client.fetch_token(
        token_endpoint,
        code=code,
        code_verifier=code_verifier,
        timeout=TOKEN_TIMEOUT_SECONDS,
    )
    return dict(token)


async def parse_token(
    metadata: dict[str, Any],
    provider: ConfiguredProvider,
    token: dict[str, Any],
    nonce: str,
) -> dict[str, Any]:
    """Cryptographically verify the ID token (signature, iss, aud, exp,
    nonce) against the discovery JWKS. Raises on anything invalid."""
    jwks_uri = metadata.get("jwks_uri")
    if not jwks_uri:
        raise ValueError("discovery document lacks jwks_uri")
    client = AsyncOAuth2Client(
        client_id=provider.config.client_id,
        client_secret=provider.client_secret,
    )
    async with httpx.AsyncClient(timeout=DISCOVERY_TIMEOUT_SECONDS) as http:
        resp = await http.get(jwks_uri)
        resp.raise_for_status()
        jwks = resp.json()
    claims = client.parse_id_token(
        token,
        nonce=nonce,
        jwks=jwks,
        leeway=CLOCK_SKEW_LEEWAY_SECONDS,
    )
    if claims.get("iss") != metadata.get("issuer"):
        raise ValueError("ID token issuer mismatch")
    return dict(claims)


def verify_claims(claims: dict[str, Any], provider: ConfiguredProvider, nonce: str) -> None:
    """Defense-in-depth over the cryptographic check: pin audience and
    nonce explicitly so a test (or a future authlib behavior change) can
    see the exact requirement. Raises ValueError on mismatch."""
    aud = claims.get("aud")
    audiences = [aud] if isinstance(aud, str) else list(aud or [])
    if provider.config.client_id not in audiences:
        raise ValueError("ID token audience mismatch")
    if not claims.get("nonce") or claims["nonce"] != nonce:
        raise ValueError("ID token nonce mismatch")
