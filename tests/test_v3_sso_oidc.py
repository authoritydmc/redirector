"""Tests for multi-IdP OIDC SSO (EPIC-05 task 6).

Network boundary (discovery / token exchange / JWKS verify) is stubbed;
what's under test is ours: provider config parsing, group→role mapping,
PKCE challenge shape, state handling, JIT provisioning, and JWT mint.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jwt")
sqlmodel = pytest.importorskip("sqlmodel")
pytest.importorskip("authlib")

from fastapi.testclient import TestClient  # noqa: E401,E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backend.core.db import get_session  # noqa: E402
from backend.main import create_app  # noqa: E402
from backend.models.entities import OidcState, SsoUser  # noqa: E402
from backend.modules.sso import oidc  # noqa: E402
from backend.modules.sso.providers import (  # noqa: E402
    configured_providers,
    get_configured_provider,
    load_oidc_providers,
)
from backend.modules.sso.service import resolve_role  # noqa: E402

GOOGLE_PROVIDER = {
    "id": "google",
    "display_name": "Google",
    "discovery_url": "https://accounts.google.com/.well-known/openid-configuration",
    "client_id": "test-client-id",
    "admin_groups": ["redirect-admins"],
}
GOOGLE_SECRET_ENV = "REDIRECTOR_OIDC_GOOGLE_CLIENT_SECRET"

METADATA = {
    "issuer": "https://accounts.google.com",
    "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
    "token_endpoint": "https://oauth2.googleapis.com/token",
    "jwks_uri": "https://www.googleapis.com/oauth2/v3/certs",
}


@pytest.fixture()
def app_scope():
    """(client, session_factory): HTTP surface plus direct DB access for
    reading server-side OIDC state rows the browser never sees."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    app = create_app()

    async def override_get_session():
        async with engine.begin() as conn:
            await conn.run_sync(sqlmodel.SQLModel.metadata.create_all)
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as tc:
        yield tc, factory, engine
    _run(engine.dispose())


@pytest.fixture()
def client(app_scope):  # noqa: ANN001,ANN201
    tc, _factory, _engine = app_scope
    return tc


def _run(coro):  # noqa: ANN001,ANN201
    return asyncio.run(coro)


def _env_with_google(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIRECTOR_OIDC_PROVIDERS", json.dumps([GOOGLE_PROVIDER]))
    monkeypatch.setenv(GOOGLE_SECRET_ENV, "test-secret")


def _stub_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_discovery(url: str) -> dict[str, Any]:
        assert url == GOOGLE_PROVIDER["discovery_url"]
        return METADATA

    monkeypatch.setattr(oidc, "fetch_discovery", fake_discovery)


def _state_nonce(factory: Any, state: str) -> str:
    async def _get() -> str:
        async with factory() as session:
            row = (await session.execute(
                select(OidcState).where(OidcState.state == state))).scalar_one_or_none()
            assert row is not None
            return row.nonce

    return _run(_get())


def _users(factory: Any) -> list[SsoUser]:
    async def _all() -> list[SsoUser]:
        async with factory() as session:
            rows = await session.execute(select(SsoUser))
            return list(rows.scalars().all())

    return _run(_all())


def _login_state(client: TestClient) -> str:
    resp = client.get("/api/v1/auth/oidc/google/login", follow_redirects=False)
    assert resp.status_code == 302
    return parse_qs(urlparse(resp.headers["location"]).query)["state"][0]


def _stub_exchange(monkeypatch: pytest.MonkeyPatch, claims: dict[str, Any] | None,
                   fail: bool = False) -> None:
    async def fake_exchange(*args: Any, **kwargs: Any) -> dict[str, Any]:
        if fail:
            raise ValueError("bad code")
        return {"access_token": "at", "id_token": "idt"}

    async def fake_parse(*args: Any, **kwargs: Any) -> dict[str, Any]:
        assert claims is not None
        return claims

    monkeypatch.setattr(oidc, "exchange_code", fake_exchange)
    monkeypatch.setattr(oidc, "parse_token", fake_parse)


def _claims(nonce: str, groups: list[str] | None = None) -> dict[str, Any]:
    return {
        "iss": METADATA["issuer"],
        "aud": GOOGLE_PROVIDER["client_id"],
        "nonce": nonce,
        "sub": "google-sub-1",
        "email": "admin@example.com",
        "email_verified": True,
        "name": "Admin",
        "groups": groups if groups is not None else ["redirect-admins"],
    }


def _callback(client: TestClient, state: str, code: str = "auth-code") -> Any:
    return client.get(
        f"/api/v1/auth/oidc/google/callback?code={code}&state={state}",
        follow_redirects=False)


def test_providers_empty_by_default(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REDIRECTOR_OIDC_PROVIDERS", raising=False)
    assert client.get("/api/v1/auth/providers").json() == []


def test_providers_lists_only_secreted(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIRECTOR_OIDC_PROVIDERS", json.dumps([
        GOOGLE_PROVIDER,
        {"id": "entra", "display_name": "Entra",
         "discovery_url": "https://login.microsoftonline.com/x/v2.0/.well-known/openid-configuration",
         "client_id": "entra-client", "admin_groups": ["sg-admins"]},
    ]))
    monkeypatch.setenv(GOOGLE_SECRET_ENV, "test-secret")
    # entra has no secret env -> not login-ready -> not advertised.
    body = client.get("/api/v1/auth/providers").json()
    assert body == [{"id": "google", "display_name": "Google"}]
    assert "secret" not in json.dumps(body).lower()


def test_provider_config_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIRECTOR_OIDC_PROVIDERS", "not-json")
    with pytest.raises(ValueError):
        load_oidc_providers()
    monkeypatch.setenv("REDIRECTOR_OIDC_PROVIDERS", json.dumps([GOOGLE_PROVIDER, GOOGLE_PROVIDER]))
    with pytest.raises(ValueError):
        load_oidc_providers()
    bad = dict(GOOGLE_PROVIDER)
    del bad["client_id"]
    monkeypatch.setenv("REDIRECTOR_OIDC_PROVIDERS", json.dumps([bad]))
    with pytest.raises(ValueError):
        load_oidc_providers()
    _env_with_google(monkeypatch)
    assert [p.id for p in load_oidc_providers()] == ["google"]
    assert get_configured_provider("google") is not None
    assert get_configured_provider("nope") is None
    assert configured_providers()[0].client_secret == "test-secret"


def test_role_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    _env_with_google(monkeypatch)
    config = load_oidc_providers()[0]
    assert resolve_role({"groups": ["redirect-admins"]}, config) == "admin"
    assert resolve_role({"roles": ["redirect-admins"]}, config) == "admin"
    assert resolve_role({"groups": ["everyone"]}, config) == "viewer"
    assert resolve_role({}, config) == "viewer"
    assert resolve_role(
        {"groups": ["redirect-admins"], "email_verified": False}, config) == "viewer"


def test_pkce_pair_matches() -> None:
    verifier, challenge = oidc.pkce_pair()
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    assert challenge == expected
    assert len(verifier) >= 43


def test_login_redirects_to_idp(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _env_with_google(monkeypatch)
    _stub_discovery(monkeypatch)
    resp = client.get("/api/v1/auth/oidc/google/login", follow_redirects=False)
    assert resp.status_code == 302
    parsed = urlparse(resp.headers["location"])
    assert parsed.netloc == "accounts.google.com"
    query = parse_qs(parsed.query)
    assert query["client_id"] == ["test-client-id"]
    assert query["code_challenge_method"] == ["S256"]
    assert "code_challenge" in query and "state" in query and "nonce" in query
    assert query["redirect_uri"] == ["http://testserver/api/v1/auth/oidc/google/callback"]


def test_login_unknown_provider_404(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REDIRECTOR_OIDC_PROVIDERS", raising=False)
    resp = client.get("/api/v1/auth/oidc/google/login", follow_redirects=False)
    assert resp.status_code == 404
    assert resp.json()["code"] == "sso:unknown-provider"


def test_callback_mints_jwt_and_jit_provisions(app_scope: Any,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    client, factory, _engine = app_scope
    _env_with_google(monkeypatch)
    _stub_discovery(monkeypatch)
    state = _login_state(client)
    _stub_exchange(monkeypatch, _claims(_state_nonce(factory, state)))

    resp = _callback(client, state)
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location.startswith("/app/login#access_token="), location
    token = location.split("#access_token=", 1)[1]

    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json() == {"role": "admin", "sub": "sso:google:google-sub-1"}

    users = _users(factory)
    assert len(users) == 1
    assert (users[0].provider, users[0].subject, users[0].role) == ("google", "google-sub-1", "admin")
    assert users[0].email == "admin@example.com"

    # State is one-shot: replaying it fails closed.
    replay = _callback(client, state)
    assert replay.status_code == 302
    assert replay.headers["location"].endswith("#error=invalid-state")


def test_callback_denies_unmapped_user(app_scope: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    client, factory, _engine = app_scope
    _env_with_google(monkeypatch)
    _stub_discovery(monkeypatch)
    state = _login_state(client)
    _stub_exchange(monkeypatch, _claims(_state_nonce(factory, state), groups=["everyone"]))

    resp = _callback(client, state)
    assert resp.status_code == 302
    assert resp.headers["location"].endswith("#error=not-authorized")

    users = _users(factory)
    assert len(users) == 1 and users[0].role == "viewer"


def test_callback_rejects_unknown_state(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _env_with_google(monkeypatch)
    resp = _callback(client, "nope-not-a-state")
    assert resp.status_code == 302
    assert resp.headers["location"].endswith("#error=invalid-state")


def test_callback_rejects_expired_state(app_scope: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    client, factory, engine = app_scope
    _env_with_google(monkeypatch)

    async def _plant_expired() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(sqlmodel.SQLModel.metadata.create_all)
        async with factory() as session:
            session.add(OidcState(
                state="stale", provider="google", code_verifier="v", nonce="n",
                created_at=datetime.now(UTC) - timedelta(minutes=30),
                expires_at=datetime.now(UTC) - timedelta(minutes=20)))
            await session.commit()

    _run(_plant_expired())
    resp = _callback(client, "stale")
    assert resp.status_code == 302
    assert resp.headers["location"].endswith("#error=invalid-state")


def test_callback_rejects_exchange_failure(app_scope: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    client, factory, _engine = app_scope
    _env_with_google(monkeypatch)
    _stub_discovery(monkeypatch)
    state = _login_state(client)
    _stub_exchange(monkeypatch, None, fail=True)
    resp = _callback(client, state)
    assert resp.status_code == 302
    assert resp.headers["location"].endswith("#error=invalid-token")


def test_callback_rejects_nonce_mismatch(app_scope: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    client, factory, _engine = app_scope
    _env_with_google(monkeypatch)
    _stub_discovery(monkeypatch)
    state = _login_state(client)
    _stub_exchange(monkeypatch, _claims("wrong-nonce"))
    resp = _callback(client, state)
    assert resp.status_code == 302
    assert resp.headers["location"].endswith("#error=invalid-token")
