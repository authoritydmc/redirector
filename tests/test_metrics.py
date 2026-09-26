"""Tests for the KPI dashboard, realtime page and their JSON APIs."""

from datetime import datetime, timedelta, timezone

import pytest

from app import create_app, db
from model.redirect import Redirect
from model.upstream_check_log import UpstreamCheckLog


@pytest.fixture
def client():
    flask_app = create_app()
    flask_app.config['TESTING'] = True
    with flask_app.test_client() as c:
        with flask_app.app_context():
            db.create_all()
            now = datetime.now(timezone.utc)
            db.session.add_all([
                Redirect(type='static', pattern='docs', target='https://example.com/docs',
                         access_count=10, tags='eng,docs', visibility='public',
                         created_at=(now - timedelta(days=2)).isoformat(sep=' '),
                         updated_at=now.isoformat(sep=' ')),
                Redirect(type='dynamic', pattern='jira', target='https://jira.example.com',
                         access_count=0, tags='eng', visibility='team',
                         created_at=(now - timedelta(days=40)).isoformat(sep=' '),
                         updated_at=(now - timedelta(days=40)).isoformat(sep=' ')),
                Redirect(type='static', pattern='old-link', target='https://example.com/old',
                         access_count=5, visibility='public',
                         created_at=(now - timedelta(days=10)).isoformat(sep=' '),
                         updated_at=(now - timedelta(days=1)).isoformat(sep=' '),
                         expires_at=(now - timedelta(days=1)).isoformat()),
            ])
            db.session.add_all([
                UpstreamCheckLog(pattern='docs', upstream_name='wiki', result='ok',
                                 count=4, cached=True),
                UpstreamCheckLog(pattern='jira', upstream_name='tracker', result='ok',
                                 count=2, cached=False),
            ])
            db.session.commit()
        with c.session_transaction() as sess:
            sess['admin_logged_in'] = True
        yield c
        with flask_app.app_context():
            db.drop_all()


def test_pages_require_login():
    flask_app = create_app()
    flask_app.config['TESTING'] = True
    with flask_app.test_client() as c:
        with flask_app.app_context():
            db.create_all()
            try:
                # No Accept header: the decorator answers JSON 401. A browser
                # Accept header gets the login redirect instead.
                assert c.get('/metrics').status_code == 401
                assert c.get('/metrics/live').status_code == 401
                assert c.get('/metrics', headers={'Accept': 'text/html'}).status_code == 302
                r = c.get('/api/kpi', headers={'Accept': 'application/json'})
                assert r.status_code == 401
                r = c.get('/api/metrics/live', headers={'Accept': 'application/json'})
                assert r.status_code == 401
            finally:
                db.drop_all()


def test_pages_render(client):
    assert client.get('/metrics').status_code == 200
    assert client.get('/metrics/live').status_code == 200


def test_kpi_api_math(client):
    payload = client.get('/api/kpi').get_json()
    assert payload['ok'] is True
    overview = payload['overview']
    assert overview['total_shortcuts'] == 3
    assert overview['total_hits'] == 15
    assert overview['avg_hits'] == 5.0
    assert overview['zero_hit_count'] == 1
    assert overview['most_popular']['pattern'] == 'docs'
    assert overview['created_7d'] == 1
    assert overview['created_30d'] == 2

    by_type = {row['type']: row for row in payload['breakdowns']['by_type']}
    assert by_type['static']['shortcuts'] == 2
    assert by_type['static']['hits'] == 15
    assert by_type['dynamic']['shortcuts'] == 1

    buckets = {row['bucket']: row['shortcuts'] for row in payload['breakdowns']['hit_buckets']}
    assert buckets == {'0': 1, '1-10': 2, '11-100': 0, '101+': 0}

    tags = {row['tag']: row['shortcuts'] for row in payload['breakdowns']['top_tags']}
    assert tags['eng'] == 2
    assert tags['docs'] == 1

    assert payload['tables']['expired_count'] == 1
    assert [row['pattern'] for row in payload['tables']['top']] == ['docs', 'old-link', 'jira']
    assert len(payload['created_series']) == 30
    assert sum(d['created'] for d in payload['created_series']) == 2

    assert payload['system']['version']
    assert payload['system']['schema_drift'] in (
        'up-to-date', 'pending', 'ahead', 'unknown', 'fresh', 'unstamped')

    cache = payload['cache']
    assert cache['upstream'] == {'hits': 4, 'misses': 2, 'hit_rate': 66.7}
    # No Redis in tests: unknown, not zero.
    assert cache['redis']['connected'] is False
    assert cache['redis']['hits'] is None
    assert cache['redis']['hit_rate'] is None


def test_live_api_shape(client):
    payload = client.get('/api/metrics/live').get_json()
    assert payload['ok'] is True
    for key in ('generated_at', 'uptime_seconds', 'memory_mb', 'cpu_percent',
                'db_up', 'db_latency_ms', 'total_shortcuts', 'total_hits',
                'top', 'recent', 'schema_drift', 'version'):
        assert key in payload, key
    assert payload['db_up'] is True
    assert payload['total_shortcuts'] == 3
    assert payload['total_hits'] == 15
    assert payload['top'][0]['pattern'] == 'docs'
    assert payload['upstream_cache_stats'] == {'hits': 4, 'misses': 2, 'hit_rate': 66.7}
    assert payload['redis_cache']['connected'] is False


def test_parse_dt_units():
    from app.routes.metrics_routes import _parse_dt

    assert _parse_dt(None) is None
    assert _parse_dt('') is None
    assert _parse_dt('not-a-date') is None
    parsed = _parse_dt('2026-09-01 10:00:00')
    assert parsed is not None and parsed.tzinfo is not None
    assert _parse_dt('2026-09-01T10:00:00+00:00') == parsed
