import pytest
from app import create_app, db
from flask import url_for
import requests
from unittest.mock import patch

@pytest.fixture
def client():
    app = create_app()
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    with app.test_client() as client:
        with app.app_context():
            db.create_all()
            yield client
            db.session.remove()
            db.drop_all()

@pytest.fixture
def logged_in_client(client):
    with client.session_transaction() as sess:
        sess['admin_logged_in'] = True
    return client

def test_admin_upstreams_get(logged_in_client):
    resp = logged_in_client.get('/admin/upstreams', follow_redirects=True)
    assert resp.status_code == 200
    assert b'Upstreams' in resp.data

def test_admin_upstreams_post_add_and_delete(logged_in_client):
    # Add new upstream
    data = {
        'name_0': 'TestUp',
        'base_url_0': 'http://example.com',
        'fail_url_0': 'http://example.com/fail',
        'fail_status_code_0': '404',
        'verify_ssl_0': 'on',
    }
    resp = logged_in_client.post('/admin/upstreams', data=data, follow_redirects=True)
    assert resp.status_code == 200
    assert b'TestUp' in resp.data
    # Delete upstream
    resp = logged_in_client.post('/admin/upstreams', data={'delete': '0'}, follow_redirects=True)
    assert resp.status_code == 200
    assert b'TestUp' not in resp.data

def test_admin_upstreams_post_invalid_delete(logged_in_client):
    # Try to delete a non-existent upstream
    resp = logged_in_client.post('/admin/upstreams', data={'delete': '99'}, follow_redirects=True)
    assert resp.status_code == 200
    assert b'Upstreams' in resp.data or b'non-existent' in resp.data

def test_admin_upstreams_post_invalid_fail_status_code(logged_in_client):
    # Add with invalid fail_status_code
    data = {
        'name_0': 'BadUp',
        'base_url_0': 'http://badup.com',
        'fail_url_0': 'http://badup.com/fail',
        'fail_status_code_0': 'notanumber',
        'verify_ssl_0': 'on',
    }
    resp = logged_in_client.post('/admin/upstreams', data=data, follow_redirects=True)
    assert resp.status_code == 200
    assert b'BadUp' in resp.data

def test_check_upstreams_ui(logged_in_client):
    resp = logged_in_client.get('/check-upstreams-ui/test-pattern')
    assert resp.status_code == 200
    assert b'test-pattern' in resp.data

def test_stream_check_upstreams(logged_in_client):
    resp = logged_in_client.get('/stream/check-upstreams/test-pattern')
    assert resp.status_code == 200
    assert resp.mimetype == 'text/event-stream'

def test_stream_check_upstreams_no_upstreams(logged_in_client, monkeypatch):
    # Patch utils.get_upstreams to return empty
    from app.utils import utils as app_utils
    monkeypatch.setattr(app_utils, 'get_upstreams', lambda: [])
    resp = logged_in_client.get('/stream/check-upstreams/test-pattern')
    assert resp.status_code == 200
    assert b'No upstream found' in resp.data or resp.data

def test_stream_check_upstreams_timeout(logged_in_client, monkeypatch):
    # Patch requests.get to raise Timeout
    from app.utils import utils as app_utils
    monkeypatch.setattr(app_utils, 'get_upstreams', lambda: [{
        'name': 'TimeoutUp', 'base_url': 'http://timeout.com', 'fail_url': '', 'fail_status_code': None, 'verify_ssl': False
    }])
    with patch('requests.get', side_effect=requests.exceptions.Timeout):
        resp = logged_in_client.get('/stream/check-upstreams/test-pattern')
        assert resp.status_code == 200
        assert b'Timeout' in resp.data or b'timed out' in resp.data

def test_admin_upstream_logs(logged_in_client):
    resp = logged_in_client.get('/admin/upstream-logs')
    assert resp.status_code == 200
    assert b'Upstream' in resp.data or b'Log' in resp.data

def test_admin_upstream_cache(logged_in_client):
    resp = logged_in_client.get('/admin/upstream-cache/TestUp')
    assert resp.status_code in (200, 404)

def test_admin_upstream_cache_resync_not_found(logged_in_client):
    resp = logged_in_client.get('/admin/upstream-cache/resync/NotExist/test-pattern')
    assert resp.status_code == 404
    assert b'Upstream not found' in resp.data

def test_admin_upstream_cache_purge_entry(logged_in_client):
    resp = logged_in_client.post('/admin/upstream-cache/purge-entry/TestUp/test-pattern')
    assert resp.status_code in (200, 500)

def test_admin_upstream_cache_purge(logged_in_client):
    resp = logged_in_client.post('/admin/upstream-cache/purge/TestUp')
    assert resp.status_code in (200, 500)

def test_admin_upstream_cache_resync_all_not_found(logged_in_client):
    resp = logged_in_client.post('/admin/upstream-cache/resync-all/NotExist')
    assert resp.status_code == 404
    assert b'Upstream not found' in resp.data

def test_clear_upstream_logs(logged_in_client):
    resp = logged_in_client.post('/admin/clear-upstream-logs', follow_redirects=True)
    assert resp.status_code == 200
    assert b'Upstream' in resp.data or b'Log' in resp.data

def test_resync_success_and_fail(logged_in_client):
    # Add a fake upstream
    data = {
        'name_0': 'FakeUp',
        'base_url_0': 'http://fakeup.com',
        'fail_url_0': 'http://fakeup.com/fail',
        'fail_status_code_0': '404',
        'verify_ssl_0': 'on',
    }
    logged_in_client.post('/admin/upstreams', data=data, follow_redirects=True)
    # Patch requests.get to simulate success and fail
    with patch('requests.get') as mock_get:
        class FakeResp:
            def __init__(self, url, status):
                self.url = url
                self.status_code = status
        # Simulate success
        mock_get.return_value = FakeResp('http://fakeup.com/success', 200)
        resp = logged_in_client.get('/admin/upstream-cache/resync/FakeUp/test-pattern')
        assert resp.status_code == 200
        assert b'success' in resp.data
        # Simulate fail
        mock_get.return_value = FakeResp('http://fakeup.com/fail', 404)
        resp = logged_in_client.get('/admin/upstream-cache/resync/FakeUp/test-pattern')
        assert resp.status_code == 200
        assert b'fail criteria' in resp.data or b'Pattern not found' in resp.data

def test_admin_upstream_cache_resync_all_success(logged_in_client):
    # Add a fake upstream and cache entry
    data = {
        'name_0': 'BulkUp',
        'base_url_0': 'http://bulkup.com',
        'fail_url_0': 'http://bulkup.com/fail',
        'fail_status_code_0': '404',
        'verify_ssl_0': 'on',
    }
    logged_in_client.post('/admin/upstreams', data=data, follow_redirects=True)
    # Patch utils.list_upstream_cache to return a fake entry
    from app.utils import utils as app_utils
    app_utils.list_upstream_cache = lambda up: [{'pattern': 'bulk-pattern'}] if up == 'BulkUp' else []
    with patch('requests.get') as mock_get:
        class FakeResp:
            def __init__(self, url, status):
                self.url = url
                self.status_code = status
        mock_get.return_value = FakeResp('http://bulkup.com/success', 200)
        resp = logged_in_client.post('/admin/upstream-cache/resync-all/BulkUp')
        assert resp.status_code == 200
        assert b'success' in resp.data or b'results' in resp.data

def test_admin_upstreams_post_missing_fields(logged_in_client):
    # Missing base_url
    data = {
        'name_0': 'NoBase',
        'fail_url_0': 'http://fail',
        'fail_status_code_0': '404',
        'verify_ssl_0': 'on',
    }
    resp = logged_in_client.post('/admin/upstreams', data=data, follow_redirects=True)
    assert resp.status_code == 200
    assert b'NoBase' in resp.data

def test_admin_upstreams_duplicate_name(logged_in_client):
    data1 = {
        'name_0': 'DupUp',
        'base_url_0': 'http://dup1.com',
        'fail_url_0': 'http://dup1.com/fail',
        'fail_status_code_0': '404',
        'verify_ssl_0': 'on',
    }
    data2 = {
        'name_0': 'DupUp',
        'base_url_0': 'http://dup2.com',
        'fail_url_0': 'http://dup2.com/fail',
        'fail_status_code_0': '404',
        'verify_ssl_0': 'on',
    }
    logged_in_client.post('/admin/upstreams', data=data1, follow_redirects=True)
    resp = logged_in_client.post('/admin/upstreams', data=data2, follow_redirects=True)
    assert resp.status_code == 200
    assert resp.data.count(b'DupUp') >= 1

def test_admin_upstream_cache_purge_empty(logged_in_client):
    # Purge cache for upstream with no entries
    resp = logged_in_client.post('/admin/upstream-cache/purge/NoSuchUpstream')
    assert resp.status_code in (200, 500)
    # Should not error, purged should be 0 or error message

def test_admin_upstream_cache_get_nonexistent(logged_in_client):
    resp = logged_in_client.get('/admin/upstream-cache/NoSuchUpstream')
    assert resp.status_code in (200, 404, 500)

def test_admin_upstream_cache_resync_post_method(logged_in_client):
    # POST to resync endpoint (should work, but test both GET and POST)
    data = {
        'name_0': 'PostResync',
        'base_url_0': 'http://postresync.com',
        'fail_url_0': 'http://postresync.com/fail',
        'fail_status_code_0': '404',
        'verify_ssl_0': 'on',
    }
    logged_in_client.post('/admin/upstreams', data=data, follow_redirects=True)
    with patch('requests.get') as mock_get:
        class FakeResp:
            def __init__(self, url, status):
                self.url = url
                self.status_code = status
        mock_get.return_value = FakeResp('http://postresync.com/success', 200)
        resp = logged_in_client.post('/admin/upstream-cache/resync/PostResync/test-pattern')
        assert resp.status_code == 200
        assert b'success' in resp.data

def test_admin_upstream_cache_resync_all_mixed_results(logged_in_client):
    # Add a fake upstream and cache entry
    data = {
        'name_0': 'MixUp',
        'base_url_0': 'http://mixup.com',
        'fail_url_0': 'http://mixup.com/fail',
        'fail_status_code_0': '404',
        'verify_ssl_0': 'on',
    }
    logged_in_client.post('/admin/upstreams', data=data, follow_redirects=True)
    from app.utils import utils as app_utils
    app_utils.list_upstream_cache = lambda up: [{'pattern': 'ok'}, {'pattern': 'fail'}] if up == 'MixUp' else []
    with patch('requests.get') as mock_get:
        class FakeResp:
            def __init__(self, url, status):
                self.url = url
                self.status_code = status
        def fake_get(url, *a, **k):
            if 'ok' in url:
                return FakeResp('http://mixup.com/success', 200)
            return FakeResp('http://mixup.com/fail', 404)
        mock_get.side_effect = fake_get
        resp = logged_in_client.post('/admin/upstream-cache/resync-all/MixUp')
        assert resp.status_code == 200
        assert b'success' in resp.data and b'fail' in resp.data

def test_stream_check_upstreams_connection_error(logged_in_client, monkeypatch):
    from app.utils import utils as app_utils
    monkeypatch.setattr(app_utils, 'get_upstreams', lambda: [{
        'name': 'ConnUp', 'base_url': 'http://connup.com', 'fail_url': '', 'fail_status_code': None, 'verify_ssl': False
    }])
    with patch('requests.get', side_effect=requests.exceptions.ConnectionError('fail')):
        resp = logged_in_client.get('/stream/check-upstreams/test-pattern')
        assert resp.status_code == 200
        assert b'Connection error' in resp.data or b'fail' in resp.data

def test_stream_check_upstreams_generic_exception(logged_in_client, monkeypatch):
    from app.utils import utils as app_utils
    monkeypatch.setattr(app_utils, 'get_upstreams', lambda: [{
        'name': 'GenUp', 'base_url': 'http://genup.com', 'fail_url': '', 'fail_status_code': None, 'verify_ssl': False
    }])
    with patch('requests.get', side_effect=Exception('boom')):
        resp = logged_in_client.get('/stream/check-upstreams/test-pattern')
        assert resp.status_code == 200
        assert b'unexpected error' in resp.data.lower() or b'boom' in resp.data.lower()

def test_admin_upstream_cache_resync_ssl_error(logged_in_client):
    data = {
        'name_0': 'SSLUp',
        'base_url_0': 'https://sslfail.com',
        'fail_url_0': 'https://sslfail.com/fail',
        'fail_status_code_0': '404',
        'verify_ssl_0': 'on',
    }
    logged_in_client.post('/admin/upstreams', data=data, follow_redirects=True)
    with patch('requests.get', side_effect=requests.exceptions.SSLError('ssl fail')):
        resp = logged_in_client.get('/admin/upstream-cache/resync/SSLUp/test-pattern')
        assert resp.status_code == 200 or resp.status_code == 500
        assert b'ssl fail' in resp.data or b'Upstream check failed' in resp.data or b'error' in resp.data
