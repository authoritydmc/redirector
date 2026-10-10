"""Admin config schema + effective-setting precedence (config UX slice)."""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlmodel")

_testclient = pytest.importorskip("fastapi.testclient")
TestClient = _testclient.TestClient
_sa_asyncio = pytest.importorskip("sqlalchemy.ext.asyncio")
async_sessionmaker = _sa_asyncio.async_sessionmaker
create_async_engine = _sa_asyncio.create_async_engine
_sqlmodel = pytest.importorskip("sqlmodel")
SQLModel = _sqlmodel.SQLModel
_core_config = pytest.importorskip("backend.core.config")
resolve_setting = _core_config.resolve_setting
app_settings = _core_config.settings
_core_db = pytest.importorskip("backend.core.db")
get_session = _core_db.get_session
_main = pytest.importorskip("backend.main")
create_app = _main.create_app
_entities = pytest.importorskip("backend.models.entities")
Setting = _entities.Setting


def _boot(tmp_path: Any) -> Any:
    db_path = tmp_path / "config-test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app()

    async def override_get_session():
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    return app, factory


def _token(client: TestClient) -> dict[str, str]:
    login = client.post("/api/v1/auth/login", json={"password": app_settings.admin_password})
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_schema_lists_editable_and_env_managed(tmp_path: Any) -> None:
    app, _ = _boot(tmp_path)
    with TestClient(app) as client:
        headers = _token(client)
        body = client.get("/api/v1/admin/config", headers=headers).json()
        schema = {item["key"]: item for item in body["schema"]}
        assert set(["auto_redirect_delay", "jwt_access_token_expire_minutes",
                    "auth_lockout_max_attempts", "auth_lockout_window_minutes"]) <= set(schema)
        delay = schema["auto_redirect_delay"]
        assert delay["title"] and delay["description"]
        assert delay["env_var"] == "REDIRECTOR_AUTO_REDIRECT_DELAY"
        assert delay["source"] in ("environment", "database", "default")
        env_keys = {item["key"] for item in schema.values() if item.get("readonly") and item.get("type") == "secret"}
        assert {"admin_password", "jwt_secret"} <= env_keys
        for item in schema.values():
            if item["key"] in ("admin_password", "jwt_secret"):
                assert item["value"] is None, "secrets must never be valued"


def test_patch_coerces_and_rejects(tmp_path: Any) -> None:
    app, _ = _boot(tmp_path)
    with TestClient(app) as client:
        headers = _token(client)
        ok = client.patch("/api/v1/admin/config", headers=headers,
                          json={"settings": {"auto_redirect_delay": "3"}})
        assert ok.status_code == 200
        body = client.get("/api/v1/admin/config", headers=headers).json()
        schema = {item["key"]: item for item in body["schema"]}
        assert schema["auto_redirect_delay"]["value"] == 3
        assert schema["auto_redirect_delay"]["source"] == "database"
        bad = client.patch("/api/v1/admin/config", headers=headers,
                           json={"settings": {"auto_redirect_delay": 99}})
        assert bad.status_code == 422
        assert bad.json()["code"] == "admin:invalid-setting"
        junk = client.patch("/api/v1/admin/config", headers=headers,
                            json={"settings": {"auto_redirect_delay": "soon"}})
        assert junk.status_code == 422


def test_resolve_precedence(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    async def scenario() -> tuple[Any, Any, Any]:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/prec.db")
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        async with factory() as session:
            default = await resolve_setting(session, "auto_redirect_delay")
            async with factory() as writer:
                writer.add(Setting(key="auto_redirect_delay", value=0))
                await writer.commit()
            async with factory() as session2:
                from_db = await resolve_setting(session2, "auto_redirect_delay")
            monkeypatch.setenv("REDIRECTOR_AUTO_REDIRECT_DELAY", "7")
            async with factory() as session3:
                from_env = await resolve_setting(session3, "auto_redirect_delay")
            return default, from_db, from_env

    default, from_db, from_env = asyncio.run(scenario())
    assert default[1] in ("environment", "default")
    assert from_db == (0, "database")
    assert from_env == (7, "environment")


def test_custom_keys_still_passthrough(tmp_path: Any) -> None:
    app, _ = _boot(tmp_path)
    with TestClient(app) as client:
        headers = _token(client)
        res = client.patch("/api/v1/admin/config", headers=headers,
                           json={"settings": {"welcome_message": "Hello"}})
        assert res.status_code == 200
        body = client.get("/api/v1/admin/config", headers=headers).json()
        assert body["custom"]["welcome_message"] == "Hello"
