"""Smoke tests for the v3 FastAPI scaffold. Skipped when v3 deps aren't installed
(e.g. main-branch CI, which only installs the Flask requirements)."""

import json
import re

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("sqlmodel")

# The v2 Flask app runs `gevent.monkey.patch_all()` when the app is built
# (app/__init__.py). If that happens in this pytest process AFTER anyio/ssl
# were already imported (e.g. v2 app tests ran first), the patched
# socket/threading primitives deadlock Starlette's TestClient portal and the
# suite hangs forever. Collection-time state is stale (v2 tests patch at
# RUNTIME), so the guard must run lazily inside a fixture. Removing gevent
# (EPIC-01) un-breaks the combined run for good.
def _gevent_patched_at_runtime() -> bool:
    try:
        from gevent import monkey as _gevent_monkey
    except ImportError:  # gevent not installed (local dev without it)
        return False
    return _gevent_monkey.is_module_patched("socket")

from fastapi.testclient import TestClient  # noqa: E402

from backend.main import create_app  # noqa: E402


@pytest.fixture()
def client():
    if _gevent_patched_at_runtime():
        pytest.skip(
            "gevent monkey-patching (v2 Flask app, patched at runtime) "
            "deadlocks TestClient; run this file in its own pytest process "
            "(see EPIC-01)"
        )
    return TestClient(create_app())


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_readyz_reports_version(client):
    body = client.get("/readyz").json()
    assert body["status"] in ("ready", "degraded")
    assert body["version"]
    assert isinstance(body["data_dir_writable"], bool)


def test_openapi_lists_health_routes(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert "/healthz" in paths and "/readyz" in paths


def test_openapi_covers_all_routers(client):
    paths = client.get("/openapi.json").json()["paths"]
    for prefix in (
        "/api/v1/shortcuts",
        "/api/v1/upstreams",
        "/api/v1/metrics",
        "/api/v1/auth",
        "/api/v1/admin/config",
        "/api/v1/qr",
        "/api/v1/resolve",
        "/qr/",
        "/healthz",
        "/readyz",
        "/{pattern}",
    ):
        assert any(p == prefix or p.startswith(prefix) for p in paths), prefix


def test_openapi_operations_have_summaries(client):
    ops = [
        op
        for path, methods in client.get("/openapi.json").json()["paths"].items()
        for op in methods.values()
    ]
    assert ops
    missing = [op.get("operationId") for op in ops if not op.get("summary")]
    assert not missing, f"operations without summary: {missing}"


def test_openapi_refs_resolve(client):
    spec = client.get("/openapi.json").json()
    schemas = set(spec.get("components", {}).get("schemas", {}))
    refs = set(re.findall(r"#/components/schemas/(\w+)", json.dumps(spec)))
    assert refs, "expected $ref schemas in spec"
    assert refs <= schemas, f"dangling refs: {refs - schemas}"
