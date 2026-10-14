import json
import time
from urllib.parse import urlparse, parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.auth import create_owner
from app.settings import Settings
from app.services.libtv_connection import Connection, protect


@pytest.fixture
def setup(tmp_path, monkeypatch):
    import app.services.libtv_connection as module
    # Tests isolate storage encryption from protocol validation. No real credentials.
    monkeypatch.setattr(module, 'protect', lambda data, decrypt=False: data)
    settings = Settings(tmp_path)
    settings.ensure_dirs()
    return settings


def test_pkce_state_single_use_session_binding_and_no_secret_status(setup):
    exchanges = []
    def serve(request):
        if request.url.path == '/register':
            return httpx.Response(201, json={'client_id': 'client', 'client_secret': 'SECRET'})
        exchanges.append(parse_qs(request.content.decode()))
        return httpx.Response(200, json={'access_token': 'TOKEN', 'token_type': 'Bearer', 'expires_in': 3600})
    connection = Connection(setup, httpx.MockTransport(serve))
    session = {'session_id': 's1', 'owner_id': 'o1'}
    url = connection.start(session)['authorization_url']
    params = parse_qs(urlparse(url).query)
    assert params['code_challenge_method'] == ['S256']
    assert params['scope'] == ['libtv:account:read libtv:project:read']
    assert 'SECRET' not in url
    with pytest.raises(ValueError):
        connection.callback({'session_id': 's2', 'owner_id': 'o1'}, params['state'][0], 'code')
    with pytest.raises(ValueError):
        connection.callback(session, 'bad-state', 'code')
    connection.callback(session, params['state'][0], 'code')
    assert exchanges[0]['code_verifier']
    assert connection.status('o1')['connected']
    assert 'TOKEN' not in json.dumps(connection.status('o1'))
    assert not connection.status('o2')['connected']
    with pytest.raises(ValueError):
        connection.callback(session, params['state'][0], 'code')
    assert len(exchanges) == 1


def test_unknown_token_exchange_never_replays(setup):
    def serve(request):
        if request.url.path == '/register':
            return httpx.Response(201, json={'client_id': 'c', 'client_secret': 's'})
        raise httpx.ReadTimeout('private response or token')
    connection = Connection(setup, httpx.MockTransport(serve))
    session = {'session_id': 's', 'owner_id': 'o'}
    state = parse_qs(urlparse(connection.start(session)['authorization_url']).query)['state'][0]
    with pytest.raises(ValueError, match='未自动重试'):
        connection.callback(session, state, 'code')
    with pytest.raises(ValueError, match='过期'):
        connection.callback(session, state, 'code')


def test_mcp_session_headers_readonly_and_sse(setup):
    calls = []
    def serve(request):
        body = json.loads(request.content)
        calls.append(body)
        assert request.headers['Authorization'] == 'Bearer TOKEN'
        if body['method'] == 'initialize':
            return httpx.Response(200, json={'id': 1, 'result': {'protocolVersion': '2025-03-26'}}, headers={'Mcp-Session-Id': 'abc'})
        assert request.headers['Mcp-Session-Id'] == 'abc'
        if body['method'] == 'notifications/initialized':
            return httpx.Response(202)
        return httpx.Response(200, headers={'content-type': 'text/event-stream'},
            text='event: message\ndata: {"jsonrpc":"2.0","id":2,"result":{"structuredContent":{"status":"ready"}}}\n\n')
    connection = Connection(setup, httpx.MockTransport(serve))
    connection.save('token:o', {'access_token': 'TOKEN', 'expires': time.time() + 3600})
    assert connection.call('o', 'doctor', {})['structuredContent']['status'] == 'ready'
    with pytest.raises(ValueError, match='不能制作'):
        connection.call('o', 'generation_submit', {})
    assert len(calls) == 3


def test_no_redirect_and_disconnect_removes_local_token(setup):
    connection = Connection(setup, httpx.MockTransport(lambda r: httpx.Response(302, headers={'Location': 'https://elsewhere.invalid'})))
    connection.save('token:o', {'access_token': 'TOKEN', 'expires': time.time() + 3600})
    connection.save('client', {'client_id': 'c', 'client_secret': 's'})
    result = connection.disconnect('o')
    assert result == {'disconnected': True, 'remote_revoked': False}
    assert not connection.status('o')['connected']


def test_page_auth_and_csrf(setup):
    app = create_app(settings=setup)
    with TestClient(app, base_url='http://127.0.0.1:8510') as client:
        assert client.get('/api/v1/integrations/libtv').status_code == 401
        assert client.get('/integrations', follow_redirects=False).status_code == 307
        create_owner(setup.db_path, 'owner', 'test-password')
        response = client.post('/api/v1/auth/login', json={'username': 'owner', 'password': 'test-password'})
        csrf = response.json()['data']['csrf']
        assert client.get('/integrations').status_code == 200
        assert client.post('/api/v1/integrations/libtv/connect').status_code == 403
        assert client.post('/api/v1/integrations/libtv/disconnect', headers={'X-CSRF-Token': csrf, 'Origin': 'https://evil.invalid'}).status_code == 403
        assert client.get('/api/v1/integrations/libtv').json()['data']['status'] == 'NEEDS_LOGIN'


def test_windows_dpapi_roundtrip():
    import os
    if os.name != 'nt':
        pytest.skip('Windows DPAPI only')
    encrypted = protect(b'non-secret-test-string')
    assert b'non-secret-test-string' not in encrypted
    assert protect(encrypted, True) == b'non-secret-test-string'


def test_disconnect_invalidates_pending_and_inflight_authorization(setup):
    connection = None
    inflight = False
    def serve(request):
        if request.url.path == '/register':
            return httpx.Response(201, json={'client_id': 'c', 'client_secret': 's'})
        if inflight and request.url.path == '/token':
            connection.disconnect('o')
        return httpx.Response(200, json={'access_token': 't', 'token_type': 'bearer', 'expires_in': 3600})
    connection = Connection(setup, httpx.MockTransport(serve))
    session = {'owner_id': 'o', 'session_id': 's'}
    state = parse_qs(urlparse(connection.start(session)['authorization_url']).query)['state'][0]
    connection.disconnect('o')
    with pytest.raises(ValueError, match='断开'):
        connection.callback(session, state, 'code')
    assert not connection.status('o')['connected']
    state = parse_qs(urlparse(connection.start(session)['authorization_url']).query)['state'][0]
    inflight = True
    with pytest.raises(ValueError, match='断开'):
        connection.callback(session, state, 'code')
    assert not connection.status('o')['connected']


def test_sse_returns_before_stream_timeout(setup):
    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield b'data: {"id":2,"result":{"ok":true}}\n\n'
            raise httpx.ReadTimeout('stream remains open')
    connection = Connection(setup, httpx.MockTransport(lambda r: httpx.Response(200,
        headers={'content-type': 'text/event-stream'}, stream=Stream())))
    result, _ = connection.request('/mcp', body={'id': 2})
    assert result['result']['ok']


def test_byok_owner_isolation_deletion_and_no_response_echo(setup):
    from app.services.provider_credentials import Credentials
    credentials = Credentials(setup)
    credentials.save('alice', 'typesafe', 'alice-secret', 'jev-latest')
    credentials.save('bob', 'typesafe', 'bob-secret', 'jev-latest')
    assert credentials.resolve('alice', 'typesafe')['api_key'] == 'alice-secret'
    assert credentials.resolve('bob', 'typesafe')['api_key'] == 'bob-secret'
    assert 'secret' not in json.dumps(credentials.list('alice'))
    credentials.delete('alice', 'typesafe')
    with pytest.raises(ValueError, match='不会使用其他人'):
        credentials.resolve('alice', 'typesafe')
    assert credentials.resolve('bob', 'typesafe')['api_key'] == 'bob-secret'


def test_byok_api_never_echoes_key_even_validation_failure(setup):
    app = create_app(settings=setup)
    create_owner(setup.db_path, 'owner', 'test-password')
    with TestClient(app) as client:
        csrf = client.post('/api/v1/auth/login', json={'username': 'owner', 'password': 'test-password'}).json()['data']['csrf']
        headers = {'X-CSRF-Token': csrf}
        for body in ({'api_key': 'MY-SECRET', 'model': 'jev-latest'},
                     {'api_key': {'private': 'MY-SECRET'}, 'model': 'jev-latest'},
                     {'api_key': 'MY-SECRET'}):
            response = client.put('/api/v1/integrations/providers/typesafe', json=body, headers=headers)
            assert 'MY-SECRET' not in response.text
        assert client.get('/api/v1/integrations/providers').json()['data'][0]['configured']


def test_oauth_query_removed_from_access_log():
    import logging
    from app.services.sensitive_logs import OAuthAccessFilter
    record = logging.LogRecord('uvicorn.access', 20, '', 1, '%s - "%s %s HTTP/%s" %d',
        ('127.0.0.1', 'GET', '/integrations/libtv/callback?code=SECRET&state=STATE', '1.1', 307), None)
    OAuthAccessFilter().filter(record)
    assert 'SECRET' not in record.getMessage()
    assert 'STATE' not in record.getMessage()
