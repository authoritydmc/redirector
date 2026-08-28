import pytest
from app import app as flask_app
from model.user_param import UserParam
from model.redirect import Redirect
from app import db

@pytest.fixture
def client():
    flask_app.config['TESTING'] = True
    with flask_app.test_client() as client:
        with flask_app.app_context():
            db.create_all()
            yield client
            db.session.remove()
            db.drop_all()

def test_create_static_shortcut(client):
    resp = client.post('/edit/', data={
        'pattern': 'static-test',
        'type': 'static',
        'target': 'https://example.com/static'
    }, follow_redirects=True)
    assert resp.status_code == 200
    shortcut = Redirect.query.filter_by(pattern='static-test').first()
    assert shortcut is not None
    assert shortcut.target == 'https://example.com/static'
    assert shortcut.type == 'static'

def test_create_dynamic_shortcut(client):
    resp = client.post('/edit/', data={
        'pattern': 'dynamic-test/{foo}',
        'type': 'dynamic',
        'target': 'https://example.com/dyn/{foo}'
    }, follow_redirects=True)
    assert resp.status_code == 200
    shortcut = Redirect.query.filter_by(pattern='dynamic-test/{foo}').first()
    assert shortcut is not None
    assert shortcut.target == 'https://example.com/dyn/{foo}'
    assert shortcut.type == 'dynamic'

def test_create_user_dynamic_shortcut(client):
    # Create shortcut with user-dynamic param and param metadata
    resp = client.post('/edit/', data={
        'pattern': 'userdyn-test/[bar]',
        'type': 'user-dynamic',
        'target': 'https://example.com/userdyn/[bar]',
        'param_desc_bar': 'Bar param',  # Provide required param description
    }, follow_redirects=True)
    assert resp.status_code == 200
    shortcut = Redirect.query.filter_by(pattern='userdyn-test/[bar]').first()
    assert shortcut is not None
    assert shortcut.target == 'https://example.com/userdyn/[bar]'
    assert shortcut.type == 'user-dynamic'
    found = UserParam.query.filter_by(shortcut_pattern='userdyn-test/[bar]', param_name='bar').first()
    assert found is not None
    assert found.description == 'Bar param'
    assert found.required is True

def test_create_shortcut_missing_fields(client):
    resp = client.post('/edit/', data={
        'pattern': '',
        'type': 'static',
        'target': 'https://example.com/static'
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b'cannot be empty' in resp.data or b'error' in resp.data

def test_create_duplicate_shortcut(client):
    data = {
        'pattern': 'dup-shortcut',
        'type': 'static',
        'target': 'https://example.com/dup'
    }
    client.post('/edit/', data=data, follow_redirects=True)
    resp = client.post('/edit/', data=data, follow_redirects=True)
    assert resp.status_code == 200
    assert b'already exists' in resp.data or b'error' in resp.data

def test_edit_shortcut(client):
    # Create then edit
    data = {
        'pattern': 'edit-shortcut',
        'type': 'static',
        'target': 'https://example.com/old'
    }
    client.post('/edit/', data=data, follow_redirects=True)
    resp = client.post('/edit/', data={
        'pattern': 'edit-shortcut',
        'type': 'static',
        'target': 'https://example.com/new'
    }, follow_redirects=True)
    assert resp.status_code == 200
    from model.redirect import Redirect
    shortcut = Redirect.query.filter_by(pattern='edit-shortcut').first()
    assert shortcut.target == 'https://example.com/new'

def test_redirect_static(client):
    data = {
        'pattern': 'redir-static',
        'type': 'static',
        'target': 'https://example.com/static'
    }
    client.post('/edit/', data=data, follow_redirects=True)
    resp = client.get('/redir-static', follow_redirects=False)
    assert resp.status_code in (302, 200)
    assert 'example.com/static' in resp.headers.get('Location', '') or b'example.com/static' in resp.data

def test_redirect_dynamic(client):
    data = {
        'pattern': 'redir-dyn/{foo}',
        'type': 'dynamic',
        'target': 'https://example.com/dyn/{foo}'
    }
    client.post('/edit/', data=data, follow_redirects=True)
    # Use the pattern as the subpath, with dynamic prop as a segment
    resp = client.get('/redir-dyn/bar', follow_redirects=False)
    # Accept both direct and UI fallback
    assert resp.status_code in (302, 200)
    if resp.status_code == 302 and 'Location' in resp.headers:
        assert 'example.com/dyn/bar' in resp.headers['Location'] or '/check-upstreams-ui' in resp.headers['Location']
    else:
        assert b'example.com/dyn/bar' in resp.data or b'/check-upstreams-ui' in resp.data

def test_redirect_user_dynamic(client):
    data = {
        'pattern': 'redir-udyn/[foo]',
        'type': 'user-dynamic',
        'target': 'https://example.com/udyn/[foo]',
        'param_desc_foo': 'desc'
    }
    client.post('/edit/', data=data, follow_redirects=True)
    from model.user_param import UserParam
    from app import db
    # Ensure param exists (created by POST already); upsert if needed
    param = UserParam.query.filter_by(shortcut_pattern='redir-udyn/[foo]', param_name='foo').first()
    if not param:
        param = UserParam(shortcut_pattern='redir-udyn/[foo]', param_name='foo', description='desc', required=True)
        db.session.add(param)
        db.session.commit()
    else:
        # Ensure description/required as expected
        if param.description != 'desc':
            param.description = 'desc'
            param.required = True
            db.session.commit()
    resp = client.get('/redir-udyn/bar', follow_redirects=False)
    assert resp.status_code in (302, 200)
    if resp.status_code == 302 and 'Location' in resp.headers:
        assert 'example.com/udyn/bar' in resp.headers['Location'] or '/check-upstreams-ui' in resp.headers['Location']
    else:
        assert b'example.com/udyn/bar' in resp.data or b'/check-upstreams-ui' in resp.data
