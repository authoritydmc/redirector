"""Tests for version normalization, comparison and the update check.

Every "Unknown version" / phantom-update bug so far was a mismatch between the
three formats in play (``3.2.0`` from VERSION, ``3.2.0+154.gabc`` from git
describe, ``v3.2.0`` from GitHub), so the normalization is asserted directly.
"""

import pytest

from app import create_app, db
from app.utils import versioning as V


class _Resp:
    def __init__(self, status=200, payload=None, text="", ok=True):
        self.status_code = status
        self._payload = payload or {}
        self.text = text
        self.ok = ok and status < 400

    def json(self):
        return self._payload


def _stub_get(mapping):
    """Map URL substrings to _Resp objects (or exceptions to raise)."""
    def fake(url, timeout=3):
        for key, value in mapping.items():
            if key in url:
                if isinstance(value, Exception):
                    raise value
                return value
        raise AssertionError(f"unexpected URL {url}")
    return fake


def test_normalize_handles_all_formats():
    assert V.normalize_version("3.2.0") == "3.2.0"
    assert V.normalize_version("v3.2.0") == "3.2.0"
    assert V.normalize_version("  v3.2.0\n") == "3.2.0"
    assert V.normalize_version("3.2.0+154.gcfae5be") == "3.2.0"
    assert V.normalize_version("v3.2.0+5.g1234abcd") == "3.2.0"
    assert V.normalize_version("") == ""
    assert V.normalize_version(None) == ""
    assert V.normalize_version("unknown") == ""
    assert V.normalize_version("release-please--v3.2.0") == "3.2.0"


def test_parse_and_compare():
    assert V.parse_semver("v3.2.0") == (3, 2, 0)
    assert V.parse_semver("3.10.0") == (3, 10, 0)
    assert V.parse_semver("nope") is None
    assert V.compare_semver("3.10.0", "3.9.0") == 1
    assert V.compare_semver("v3.2.0", "3.2.0+154.gcfae5be") == 0
    assert V.compare_semver("2.2.0", "3.2.0") == -1
    assert V.compare_semver("???", "3.2.0") == 0
    assert V.compare_semver("3.2.0", "???") == 0


def test_newer_release_detected(monkeypatch):
    monkeypatch.setattr(
        V.requests, "get",
        _stub_get({"api.github.com": _Resp(payload={"tag_name": "v3.3.0"})}),
    )
    result = V.check_for_updates("3.2.0+154.gcfae5be")
    assert result["success"] is True
    assert result["current"] == "3.2.0"
    assert result["current_full"] == "3.2.0+154.gcfae5be"
    assert result["latest"] == "3.3.0"
    assert result["update_available"] is True
    assert result["source"] == "github-release"
    assert result["checked_at"]


def test_older_release_is_not_an_update(monkeypatch):
    """The historical bug: latest tag 2.2.0 vs dev build 3.1.0+143.g2aee161."""
    monkeypatch.setattr(
        V.requests, "get",
        _stub_get({"api.github.com": _Resp(payload={"tag_name": "v2.2.0"})}),
    )
    result = V.check_for_updates("3.1.0+143.g2aee161")
    assert result["success"] is True
    assert result["latest"] == "2.2.0"
    assert result["update_available"] is False


def test_missing_releases_fall_back_to_version_file(monkeypatch):
    monkeypatch.setattr(
        V.requests, "get",
        _stub_get({
            "api.github.com": _Resp(status=404, text="not found", ok=False),
            "raw.githubusercontent.com": _Resp(text="3.4.0\n"),
        }),
    )
    result = V.check_for_updates("3.2.0")
    assert result["success"] is True
    assert result["latest"] == "3.4.0"
    assert result["update_available"] is True
    assert result["source"] == "version-file"


def test_offline_check_fails_closed(monkeypatch):
    monkeypatch.setattr(
        V.requests, "get",
        _stub_get({"github": ConnectionError("down")}),
    )
    result = V.check_for_updates("3.2.0")
    assert result["success"] is False
    assert result["update_available"] is False
    assert result["error"]


def test_rate_limit_is_not_an_update(monkeypatch):
    monkeypatch.setattr(
        V.requests, "get",
        _stub_get({"api.github.com": _Resp(status=403, text="API rate limit exceeded", ok=False)}),
    )
    result = V.check_for_updates("3.2.0")
    assert result["success"] is False
    assert result["error"] == "rate-limited"
    assert result["update_available"] is False


def test_endpoint_caches_for_a_day(monkeypatch):
    from app.routes import version_routes

    calls = []

    def fake(url, timeout=3):
        calls.append(url)
        return _Resp(payload={"tag_name": "v9.9.9"})

    monkeypatch.setattr(V.requests, "get", fake)
    cache = version_routes._version_check_cache
    saved = dict(cache)
    cache.update({"timestamp": 0, "result": None, "error": False})
    flask_app = create_app()
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as client:
        with flask_app.app_context():
            db.create_all()
            try:
                first = client.get("/api/latest-version").get_json()
                second = client.get("/api/latest-version").get_json()
            finally:
                db.drop_all()
                cache.update(saved)
    assert first["success"] is True
    assert first["update_available"] is True
    assert first["cached"] is False
    assert second["cached"] is True
    assert second["latest"] == first["latest"]
    assert len(calls) == 1  # second call served from cache
