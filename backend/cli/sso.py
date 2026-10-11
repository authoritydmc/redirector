"""SSO doctor: validate one OIDC provider without a browser (EPIC-05 task 9 slice).

Catches the common misconfigurations before users do: provider not
declared, client secret missing, discovery unreachable/malformed, login
URL not buildable, and the classic empty `admin_groups` (valid config —
nobody can log in until groups are mapped, so it warns).

Usage:
  PYTHONPATH=. python -m backend.cli.sso --provider google
  PYTHONPATH=. python -m backend.cli.sso --provider entra --login-base-url https://r.example.com

Exit codes: 0 healthy (warnings allowed), 1 problems found, 2 bad usage.
Secrets are never printed — only the env var name that must hold them.
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from backend.cli.doctor import Report
from backend.modules.sso import oidc
from backend.modules.sso.providers import (
    ConfiguredProvider,
    OidcProviderConfig,
    load_oidc_providers,
    provider_secret,
)

Opener = Callable[..., Any]


def _default_opener(url: str, timeout: float) -> Any:
    return urllib.request.urlopen(url, timeout=timeout)


def check_declared(report: Report, provider_id: str) -> OidcProviderConfig | None:
    try:
        declared = load_oidc_providers()
    except ValueError as exc:
        report.add("declared", False, str(exc))
        return None
    for config in declared:
        if config.id == provider_id:
            report.add("declared", True, f"{config.id} ({config.display_name})")
            return config
    report.add("declared", False,
               f"{provider_id!r} not in REDIRECTOR_OIDC_PROVIDERS "
               f"(declared: {', '.join(p.id for p in declared) or 'none'})")
    return None


def check_secret(report: Report, config: OidcProviderConfig) -> str | None:
    secret = provider_secret(config)
    if secret:
        report.add("client-secret", True,
                   f"set via {config.secret_env_var} (value never shown)")
        return secret
    report.add("client-secret", False,
               f"{config.secret_env_var} is not set — "
               "set it and restart (login refuses without it)")
    return None


def check_discovery(report: Report, provider: ConfiguredProvider,
                    opener: Opener = _default_opener) -> dict[str, Any] | None:
    url = provider.config.discovery_url
    try:
        with opener(url, timeout=10) as resp:
            metadata = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        report.add("discovery", False,
                   f"{url} unreachable or not JSON ({exc}) — is the IdP "
                   "reachable from this server (DNS/TLS/outbound)?")
        return None
    if not isinstance(metadata, dict):
        report.add("discovery", False, "discovery document is not a JSON object")
        return None
    missing = [k for k in ("issuer", "authorization_endpoint", "token_endpoint", "jwks_uri")
               if not metadata.get(k)]
    if missing:
        report.add("discovery", False,
                   f"discovery document lacks: {', '.join(missing)}")
        return None
    report.add("discovery", True, f"issuer {metadata['issuer']}")
    return metadata


def check_login_url(report: Report, provider: ConfiguredProvider,
                    metadata: dict[str, Any], login_base_url: str | None) -> None:
    callback_path = f"/api/v1/auth/oidc/{provider.id}/callback"
    if not login_base_url:
        report.add("callback-url", False,
                   f"register <public-base>{callback_path} at the IdP "
                   "(pass --login-base-url to preview the full URL)",
                   warning=True)
        return
    callback_url = login_base_url.rstrip("/") + callback_path
    try:
        challenge = oidc.build_login_url(metadata, provider, callback_url)
    except ValueError as exc:
        report.add("login-url", False, f"could not build login URL: {exc}")
        return
    if provider.config.client_id not in challenge.url:
        report.add("login-url", False, "built URL lacks our client_id")
        return
    report.add("login-url", True, f"register {callback_url} at the IdP")


def check_role_map(report: Report, provider: ConfiguredProvider) -> None:
    groups = provider.config.admin_groups
    if not groups:
        report.add("role-map", False,
                   "admin_groups is empty — valid, but nobody can log in "
                   "via this provider until groups are mapped",
                   warning=True)
        return
    report.add("role-map", True, f"{len(groups)} admin group(s) mapped")


def run_provider_checks(provider_id: str, login_base_url: str | None = None,
                        opener: Opener = _default_opener) -> Report:
    report = Report()
    config = check_declared(report, provider_id.strip().lower())
    if config is None:
        return report
    secret = check_secret(report, config)
    if secret is None:
        return report
    provider = ConfiguredProvider(config=config, client_secret=secret)
    metadata = check_discovery(report, provider, opener)
    if metadata is None:
        return report
    check_login_url(report, provider, metadata, login_base_url)
    check_role_map(report, provider)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate one OIDC provider end-to-end.")
    parser.add_argument("--provider", required=True, help="provider id from REDIRECTOR_OIDC_PROVIDERS")
    parser.add_argument("--login-base-url", default=None,
                        help="public base URL (https://r.example.com) to preview the callback URL")
    args = parser.parse_args(argv)
    report = run_provider_checks(args.provider, args.login_base_url)
    print(report.render())
    return 1 if report.problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
