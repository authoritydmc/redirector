"""Smoke tests for the FastAPI scaffold. Skipped when backend deps aren't installed."""

import json
import logging
import re

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("sqlmodel")

from fastapi.testclient import TestClient  # noqa: E402

from backend.main import create_app  # noqa: E402


@pytest.fixture()
def client():
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
        "/api/v1/admin/backup",
        "/api/v1/jobs",
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


def test_access_log_emits_method_path_status(client, caplog):
    with caplog.at_level(logging.INFO, logger="redirector.access"):
        assert client.get("/healthz").status_code == 200
    assert any(
        r.name == "redirector.access"
        and "GET /healthz" in r.getMessage()
        and "200" in r.getMessage()
        for r in caplog.records
    ), "expected one redirector.access record for the request"
