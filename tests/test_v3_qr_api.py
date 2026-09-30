"""Tests for QR router and Health aliases."""

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from backend.main import create_app


@pytest.fixture()
def client():
    app = create_app()
    with TestClient(app) as tc:
        yield tc


def test_health_and_healthz(client: TestClient):
    h_resp = client.get("/health")
    assert h_resp.status_code == 200
    assert h_resp.json()["status"] == "ok"

    hz_resp = client.get("/healthz")
    assert hz_resp.status_code == 200
    assert hz_resp.json()["status"] == "ok"


def test_qr_png_image(client: TestClient):
    resp = client.get("/qr/team/docs")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    # PNG signature check: \x89PNG
    assert resp.content.startswith(b"\x89PNG")


def test_qr_api_json(client: TestClient):
    resp = client.get("/api/v1/qr?pattern=team/docs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["pattern"] == "team/docs"
    assert "qr_base64" in data
    assert len(data["qr_base64"]) > 50


def test_qr_png_filename_is_header_safe(client: TestClient):
    # Quotes/semicolons in the path must not reach Content-Disposition raw
    # (encoded CRLF never survives the transport; '"' does and would split
    # the quoted-string). Old code emitted filename="a"b_qr.png.
    for raw, safe in [("a%22b", "a_b"), ("a%3Bb", "a_b"), ("a%20b", "a_b")]:
        resp = client.get(f"/qr/{raw}")
        assert resp.status_code == 200
        assert resp.headers["content-disposition"] == f'inline; filename="{safe}_qr.png"'
