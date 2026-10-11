"""Who gets in: IdP group → internal role mapping (EPIC-05 task 6).

Single-tenant install, default deny: a login mints a JWT only when one of
the user's IdP groups (or `roles`, which is where Entra ID puts app-role
assignments) intersects the provider's `admin_groups`. Everything else
JIT-provisions as `viewer` — recorded for audit, but it mints nothing
until per-user roles land (follow-up). An explicitly unverified email
also denies: automation rarely needs it, phishing resistance is worth it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from backend.modules.sso.providers import OidcProviderConfig

ADMIN_ROLE = "admin"
VIEWER_ROLE = "viewer"


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if isinstance(item, (str, int))]
    return []


def user_groups(claims: Mapping[str, Any], provider: OidcProviderConfig) -> list[str]:
    """Group membership from the configured claim, plus `roles` (Entra
    app roles) and `wids` (Entra directory roles) when present."""
    groups = _as_str_list(claims.get(provider.groups_claim))
    for fallback in ("roles", "wids"):
        if fallback != provider.groups_claim:
            groups.extend(_as_str_list(claims.get(fallback)))
    seen: set[str] = set()
    ordered = []
    for group in groups:
        if group not in seen:
            seen.add(group)
            ordered.append(group)
    return ordered


def resolve_role(claims: Mapping[str, Any], provider: OidcProviderConfig) -> str:
    """`"admin"` when mapped, else `"viewer"` (deny at mint time)."""
    verified = claims.get("email_verified")
    if verified is False:
        return VIEWER_ROLE
    wanted = set(provider.admin_groups)
    if wanted and wanted.intersection(user_groups(claims, provider)):
        return ADMIN_ROLE
    return VIEWER_ROLE
