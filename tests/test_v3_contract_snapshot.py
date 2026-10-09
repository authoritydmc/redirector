"""Schemathesis-style contract snapshot (EPIC-03, last open box).

Drives every operation in the app's live OpenAPI document with inert
inputs and asserts the contract surface holds without executing anything
destructive:

- GET: unknown ids/patterns → 200/401/403/404/422, never 500, JSON bodies
  (binary QR PNG and endless SSE streams are allowlisted/skipped).
- POST/PATCH with `{}` (invalid shape) → 401/403/422, never 2xx — invalid
  input must not create anything.
- DELETE of unknown ids → 401/403/404/422.

Any new route that 500s, returns HTML, or creates on garbage input fails
here. Runs on a scratch file DB; no auth token (the matrix covers both
public-200 and gated-401 outcomes).
"""

from __future__ import annotations

import re
from typing import Any

import pytest

_testclient = pytest.importorskip("fastapi.testclient")
TestClient = _testclient.TestClient
_sa_asyncio = pytest.importorskip("sqlalchemy.ext.asyncio")
async_sessionmaker = _sa_asyncio.async_sessionmaker
create_async_engine = _sa_asyncio.create_async_engine
_sqlmodel = pytest.importorskip("sqlmodel")
SQLModel = _sqlmodel.SQLModel
_core_db = pytest.importorskip("backend.core.db")
get_session = _core_db.get_session
_main = pytest.importorskip("backend.main")
create_app = _main.create_app

# Path params get values that match their converters but hit nothing.
_PROBE_VALUES = {
    "pattern": "contract-probe-missing",
    "name": "contract-probe-missing",
    "upstream": "contract-probe-missing",
    "job_id": "999999999",
    "key_id": "999999999",
    "upstream_id": "999999999",
}

# Endless or binary bodies the generic JSON assertions cannot cover.
_SKIP_PREFIXES = (
    "/api/v1/upstreams/check/stream/",
    "/events",
)
_BINARY_PREFIXES = ("/qr/",)


def _concrete(path: str) -> str:
    def fill(match: re.Match[str]) -> str:
        name = match.group(1)
        assert name in _PROBE_VALUES, f"new path param needs a probe value: {name}"
        return _PROBE_VALUES[name]

    return re.sub(r"\{([^}]+)\}", fill, path)


def _client(tmp_path: Any) -> Any:
    db_path = tmp_path / "contract.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app()

    async def override_get_session():
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    return TestClient(app)


def test_contract_snapshot(tmp_path: Any) -> None:
    with _client(tmp_path) as client:
        spec = client.get("/openapi.json").json()
        assert spec["paths"], "empty OpenAPI document"
        probed = 0
        for path, operations in spec["paths"].items():
            route = _concrete(path)
            if route.startswith(_SKIP_PREFIXES):
                continue
            for method in operations:
                probed += 1
                if method == "get":
                    resp = client.get(route)
                    assert resp.status_code in (200, 401, 403, 404, 422), (method, route, resp.status_code)
                    if route.startswith(_BINARY_PREFIXES):
                        assert resp.headers["content-type"].startswith("image/"), route
                    else:
                        # Errors render RFC 7807 application/problem+json.
                        assert "json" in resp.headers["content-type"], route
                        assert isinstance(resp.json(), (dict, list)), route
                elif method in ("post", "patch"):
                    resp = client.request(method, route, json={})
                    assert resp.status_code in (401, 403, 404, 422), (method, route, resp.status_code)
                    assert isinstance(resp.json(), dict), route
                elif method == "delete":
                    # Purge-style routes succeed publicly (200 + JSON count);
                    # 204/404 bodies vary by route; the status is the contract.
                    resp = client.delete(route)
                    assert resp.status_code in (200, 204, 401, 403, 404, 422), (method, route, resp.status_code)
                    if resp.content:
                        assert "json" in resp.headers["content-type"], route
                        resp.json()
                else:  # pragma: no cover - new HTTP verbs must extend this matrix
                    raise AssertionError(f"unhandled method in contract: {method} {route}")
        assert probed > 0, "no operations probed"
