"""SSO doctor CLI tests: declared/secret/discovery/role-map paths."""

from __future__ import annotations

import json
from typing import Any

import pytest

pytest.importorskip("sqlmodel")

from backend.cli.sso import main, run_provider_checks  # noqa: E402

METADATA = {
    "issuer": "https://accounts.google.com",
    "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
    "token_endpoint": "https://oauth2.googleapis.com/token",
    "jwks_uri": "https://www.googleapis.com/oauth2/v3/certs",
}

PROVIDER = {
    "id": "google",
    "display_name": "Google",
    "discovery_url": "https://accounts.google.com/.well-known/openid-configuration",
    "client_id": "test-client-id",
    "admin_groups": ["redirect-admins"],
}


class FakeBody:
    def __init__(self, payload: Any) -> None:
        self.payload = payload

    def __enter__(self) -> FakeBody:
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def _env(monkeypatch: pytest.MonkeyPatch, secret: bool = True) -> None:
    monkeypatch.setenv("REDIRECTOR_OIDC_PROVIDERS", json.dumps([PROVIDER]))
    if secret:
        monkeypatch.setenv("REDIRECTOR_OIDC_GOOGLE_CLIENT_SECRET", "s3cr3t")
    else:
        monkeypatch.delenv("REDIRECTOR_OIDC_GOOGLE_CLIENT_SECRET", raising=False)


def _opener(payload: Any = None, fail: bool = False):  # noqa: ANN001,ANN201
    def open_url(url: str, timeout: float = 10) -> FakeBody:
        assert url == PROVIDER["discovery_url"]
        if fail:
            raise OSError("no route to host")
        return FakeBody(METADATA if payload is None else payload)

    return open_url


def test_healthy_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    report = run_provider_checks("google", "https://r.example.com", opener=_opener())
    assert report.problems == 0
    assert report.warnings == 0
    names = {c.name for c in report.checks if c.ok}
    assert {"declared", "client-secret", "discovery", "login-url", "role-map"} <= names


def test_undeclared_provider(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    monkeypatch.delenv("REDIRECTOR_OIDC_PROVIDERS", raising=False)
    assert main(["--provider", "google"]) == 1
    assert "not in REDIRECTOR_OIDC_PROVIDERS" in capsys.readouterr().out


def test_missing_secret_is_a_problem(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, secret=False)
    report = run_provider_checks("google", opener=_opener())
    assert report.problems == 1
    assert any(c.name == "client-secret" and not c.ok for c in report.checks)


def test_unreachable_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    report = run_provider_checks("google", opener=_opener(fail=True))
    assert report.problems == 1
    assert any(c.name == "discovery" and not c.ok for c in report.checks)


def test_empty_admin_groups_warns_only(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = dict(PROVIDER)
    provider["admin_groups"] = []
    monkeypatch.setenv("REDIRECTOR_OIDC_PROVIDERS", json.dumps([provider]))
    monkeypatch.setenv("REDIRECTOR_OIDC_GOOGLE_CLIENT_SECRET", "s3cr3t")
    report = run_provider_checks("google", opener=_opener())
    assert report.problems == 0
    assert report.warnings >= 1
    assert any(c.name == "role-map" and c.warning for c in report.checks)


def test_secret_never_printed(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    _env(monkeypatch)
    run_provider_checks("google", "https://r.example.com", opener=_opener())
    # main() prints too (network-dependent tail); just assert our value
    # never appears in any report output path via direct render:
    out = run_provider_checks("google", "https://r.example.com", opener=_opener()).render()
    assert "s3cr3t" not in out
    assert "REDIRECTOR_OIDC_GOOGLE_CLIENT_SECRET" in out
