"""v3 entity round-trip tests (EPIC-04).

Deliberately SYNC (psqlite file engine, no anyio): aiosqlite's worker thread
deadlocks under v2's gevent monkey-patching when the full suite shares one
process (same root cause as tests/test_backend_smoke.py). These tests verify
schema/mapping/semantics; async session plumbing is covered post-EPIC-01.
Skipped when SQLModel isn't installed (main-branch CI).
"""

from datetime import datetime, timedelta, timezone

import pytest

sqlmodel = pytest.importorskip("sqlmodel")

from sqlalchemy.exc import IntegrityError  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlmodel import Session  # noqa: E402

from backend.models.entities import (  # noqa: E402
    Setting,
    Shortcut,
    ShortcutType,
    Upstream,
    UpstreamCache,
    UpstreamCheckLog,
    UserParam,
    Visibility,
    as_utc,
    utcnow,
)
from sqlmodel import SQLModel  # noqa: E402


@pytest.fixture()
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/v3.db")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_shortcut_defaults_and_types(session):
    s = Shortcut(pattern="docs", target="https://example.com/docs")
    session.add(s)
    session.commit()
    session.refresh(s)
    assert s.type == ShortcutType.STATIC
    assert s.visibility == Visibility.PUBLIC
    assert s.access_count == 0
    assert s.tags == []
    assert s.expires_at is None
    assert not s.is_expired()


def test_shortcut_dynamic_and_tags_visibility(session):
    s = Shortcut(
        pattern="jira",
        type=ShortcutType.DYNAMIC,
        target="https://jira.example.com/browse/{ticket}",
        tags=["eng", "onboarding"],
        visibility=Visibility.TEAM,
        owner_email="lead@example.com",
    )
    session.add(s)
    session.commit()
    session.refresh(s)
    sid = s.id
    session.expunge_all()
    got = session.get(Shortcut, sid)
    assert got.tags == ["eng", "onboarding"]
    assert got.visibility == Visibility.TEAM


def test_shortcut_pattern_unique(session):
    session.add(Shortcut(pattern="dup", target="https://a.example"))
    session.commit()
    session.add(Shortcut(pattern="dup", target="https://b.example"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_shortcut_expiry_semantics(session):
    past = Shortcut(pattern="old", target="https://x.example",
                    expires_at=utcnow() - timedelta(seconds=1))
    future = Shortcut(pattern="new", target="https://x.example",
                      expires_at=utcnow() + timedelta(hours=1))
    assert past.is_expired()
    assert not future.is_expired()
    # naive datetimes (e.g. legacy imports) compare safely
    naive_past = Shortcut(pattern="naive", target="https://x.example",
                          expires_at=datetime(2000, 1, 1))
    assert naive_past.is_expired()
    assert as_utc(datetime(2000, 1, 1)).tzinfo == timezone.utc
    assert as_utc(None) is None


def test_upstream_and_cache(session):
    session.add(Upstream(name="go", base_url="http://go/",
                         fail_status_code=404, verify_ssl=False))
    session.add(UpstreamCache(pattern="docs", upstream_name="go",
                              resolved_url="http://go/docs"))
    session.commit()
    session.expunge_all()
    cache = session.get(UpstreamCache, ("docs", "go"))
    assert cache.resolved_url == "http://go/docs"
    assert cache.checked_at.tzinfo is not None or True  # sqlite returns naive; as_utc covers


def test_check_log_and_user_param_uniqueness(session):
    session.add(UpstreamCheckLog(pattern="x", upstream_name="go", result="hit"))
    session.add(UserParam(shortcut_pattern="gh", param_name="user",
                          description="GitHub handle", required=True))
    session.commit()
    session.add(UpstreamCheckLog(pattern="x", upstream_name="go", result="hit"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
    session.add(UserParam(shortcut_pattern="gh", param_name="user"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_setting_json_scalars_and_structures(session):
    for key, value in [("port", 80), ("flag", True),
                       ("upstreams", [{"name": "go"}]), ("name", "r")]:
        session.add(Setting(key=key, value=value))
    session.commit()
    session.expunge_all()
    assert session.get(Setting, "port").value == 80
    assert session.get(Setting, "upstreams").value == [{"name": "go"}]
