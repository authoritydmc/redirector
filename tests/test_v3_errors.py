"""Tests for the RFC 7807 problem envelope (backend/core/errors.py)."""

import pytest

pytest.importorskip("fastapi")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.core.errors import AppError, register_error_handlers  # noqa: E402


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/boom")
    async def boom() -> None:
        raise AppError(
            "Shortcut expired",
            status=410,
            detail="old-link",
            code="resolve:gone",
            missing_params=["q"],
        )

    return TestClient(app, raise_server_exceptions=False)


def test_app_error_renders_problem_json(client: TestClient) -> None:
    resp = client.get("/boom")
    assert resp.status_code == 410
    assert resp.headers["content-type"] == "application/problem+json"
    body = resp.json()
    assert body == {
        "type": "about:blank",
        "title": "Shortcut expired",
        "status": 410,
        "detail": "old-link",
        "code": "resolve:gone",
        "suggestions": [],
        "missing_params": ["q"],
    }


def test_app_error_defaults() -> None:
    err = AppError("Bad input")
    assert err.status == 400
    assert err.code == "error:400"
    problem = err.to_problem()
    assert problem["suggestions"] == []
    assert problem["missing_params"] == []
