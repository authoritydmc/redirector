"""OIDC provider configuration (EPIC-05 task 6, multi-IdP).

Providers are env-driven (restart to change) — secrets hygiene first:
non-secret wiring lives in one JSON document, each client secret in its
own env var, never in the DB or logs.

    REDIRECTOR_OIDC_PROVIDERS='[{"id": "google", "display_name": "Google",
      "discovery_url": "https://accounts.google.com/.well-known/openid-configuration",
      "client_id": "xxx.apps.googleusercontent.com",
      "admin_groups": ["redirect-admins"]}]'
    REDIRECTOR_OIDC_GOOGLE_CLIENT_SECRET='...'

Per-provider fields:
- `id`: slug, used in URLs and in the secret env name (`REDIRECTOR_OIDC_<ID>_CLIENT_SECRET`,
  uppercased, dashes → underscores).
- `display_name`: shown on the login button.
- `discovery_url`: `.../.well-known/openid-configuration`.
- `client_id`: the IdP-side client identifier.
- `groups_claim` (default `"groups"`): ID-token/userinfo claim carrying
  group membership. Entra ID also lands here when group claims are
  configured; its `roles` claim is checked too.
- `admin_groups`: any match grants the admin role. Empty = nobody via
  this provider (explicit opt-in — SSO never widens access by default).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class OidcProviderConfig:
    id: str
    display_name: str
    discovery_url: str
    client_id: str
    groups_claim: str = "groups"
    admin_groups: tuple[str, ...] = ()

    @property
    def secret_env_var(self) -> str:
        slug = re.sub(r"[^a-z0-9]+", "_", self.id.lower()).strip("_").upper()
        return f"REDIRECTOR_OIDC_{slug}_CLIENT_SECRET"


def _parse_providers(raw: str) -> list[OidcProviderConfig]:
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"REDIRECTOR_OIDC_PROVIDERS is not valid JSON: {exc}") from None
    if not isinstance(data, list):
        raise ValueError("REDIRECTOR_OIDC_PROVIDERS must be a JSON list")
    providers = []
    seen: set[str] = set()
    for index, entry in enumerate(data):
        if not isinstance(entry, dict):
            raise ValueError(f"REDIRECTOR_OIDC_PROVIDERS[{index}] must be an object")
        pid = str(entry.get("id", "")).strip().lower()
        if not pid or not re.fullmatch(r"[a-z0-9-]+", pid):
            raise ValueError(
                f"REDIRECTOR_OIDC_PROVIDERS[{index}].id must be a slug "
                "(lowercase letters, digits, dashes)")
        if pid in seen:
            raise ValueError(f"duplicate OIDC provider id: {pid!r}")
        seen.add(pid)
        discovery_url = str(entry.get("discovery_url", "")).strip()
        client_id = str(entry.get("client_id", "")).strip()
        if not discovery_url or not client_id:
            raise ValueError(
                f"OIDC provider {pid!r} needs discovery_url and client_id")
        groups = entry.get("admin_groups", [])
        if not isinstance(groups, list) or any(not isinstance(g, str) for g in groups):
            raise ValueError(f"OIDC provider {pid!r}: admin_groups must be a list of strings")
        providers.append(OidcProviderConfig(
            id=pid,
            display_name=str(entry.get("display_name", "")).strip() or pid.title(),
            discovery_url=discovery_url,
            client_id=client_id,
            groups_claim=str(entry.get("groups_claim", "")).strip() or "groups",
            admin_groups=tuple(g.strip() for g in groups if g.strip()),
        ))
    return providers


def load_oidc_providers() -> list[OidcProviderConfig]:
    """Parse providers from the environment (read per call — no caching,
    so tests and config reloads never see stale wiring)."""
    raw = os.environ.get("REDIRECTOR_OIDC_PROVIDERS", "").strip()
    if not raw:
        return []
    return _parse_providers(raw)


def provider_secret(provider: OidcProviderConfig) -> str | None:
    """Client secret from its dedicated env var (None when unset — the
    provider then lists as unconfigured and login refuses)."""
    return os.environ.get(provider.secret_env_var) or None


@dataclass(frozen=True)
class ConfiguredProvider:
    """A provider complete with secret — safe to advertise and use."""

    config: OidcProviderConfig = field(compare=True)
    client_secret: str = field(compare=False, repr=False)

    @property
    def id(self) -> str:
        return self.config.id

    @property
    def display_name(self) -> str:
        return self.config.display_name


def configured_providers() -> list[ConfiguredProvider]:
    """Providers that are both declared and secreted (login-ready)."""
    ready = []
    for provider in load_oidc_providers():
        secret = provider_secret(provider)
        if secret:
            ready.append(ConfiguredProvider(config=provider, client_secret=secret))
    return ready


def get_configured_provider(provider_id: str) -> ConfiguredProvider | None:
    wanted = provider_id.strip().lower()
    for provider in configured_providers():
        if provider.id == wanted:
            return provider
    return None
