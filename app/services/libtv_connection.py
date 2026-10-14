"""LibTV OAuth/PKCE and read-only MCP connection. No generation or token copying.

Credentials live in private/, outside normal business backups, encrypted by Windows
DPAPI for the current OS user. Network operations never run inside a DB transaction.
"""
import base64
import ctypes
import hashlib
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from urllib.parse import urlencode

import httpx

ORIGIN = 'https://mcp.liblib.tv'
RESOURCE = ORIGIN + '/mcp'
SCOPES = 'libtv:account:read libtv:project:read'
READ_TOOLS = {'doctor', 'list_projects', 'project_list', 'project_info',
              'search_models', 'get_model', 'task_status', 'browse_project_media'}


def protect(data: bytes, decrypt=False) -> bytes:
    if os.name != 'nt':
        raise ValueError('安全凭据存储目前需要 Windows 用户环境')
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt = ctypes.windll.crypt32
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ValueError('Windows 凭据保护失败，请在原 Windows 用户下重新连接')
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        ctypes.windll.kernel32.LocalFree(target.data)


class Connection:
    def __init__(self, settings, transport=None):
        self.settings = settings
        self.transport = transport

    @contextmanager
    def db(self):
        conn = sqlite3.connect(self.settings.private_dir / 'libtv-connection.db', timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute('CREATE TABLE IF NOT EXISTS secrets (key TEXT PRIMARY KEY, value BLOB NOT NULL)')
        conn.commit()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def load(self, key):
        with self.db() as conn:
            row = conn.execute('SELECT value FROM secrets WHERE key=?', (key,)).fetchone()
        return json.loads(protect(row[0], True)) if row else None

    def save(self, key, value):
        blob = protect(json.dumps(value).encode())
        with self.db() as conn:
            conn.execute('INSERT OR REPLACE INTO secrets VALUES (?,?)', (key, blob))

    def remove(self, key):
        with self.db() as conn:
            conn.execute('DELETE FROM secrets WHERE key=?', (key,))

    def request(self, path, *, data=None, body=None, token=None, headers=None):
        # Fixed origin and paths; never follow redirects or trust proxy environment.
        if path not in ('/register', '/token', '/revoke', '/mcp'):
            raise ValueError('不支持的服务地址')
        hdr = {'Accept': 'application/json, text/event-stream', **(headers or {})}
        if token:
            hdr['Authorization'] = 'Bearer ' + token
        try:
            with httpx.Client(transport=self.transport, trust_env=False, follow_redirects=False,
                              timeout=30) as client:
                with client.stream('POST', ORIGIN + path, data=data, json=body, headers=hdr) as response:
                    if response.status_code >= 300:
                        raise ValueError(f'LibTV 请求失败（{response.status_code}），请重新连接或稍后重试')
                    content = bytearray()
                    event_buffer = b''
                    is_sse = 'text/event-stream' in response.headers.get('content-type', '')
                    deadline = time.monotonic() + 60
                    for chunk in response.iter_bytes():
                        content.extend(chunk)
                        if len(content) > 2_000_000 or time.monotonic() > deadline:
                            raise ValueError('LibTV 响应过大')
                        if is_sse:
                            event_buffer += chunk
                            event_buffer = event_buffer.replace(b'\r\n', b'\n')
                            while b'\n\n' in event_buffer:
                                block, event_buffer = event_buffer.split(b'\n\n', 1)
                                text = '\n'.join(line[5:].lstrip() for line in block.decode().splitlines() if line.startswith('data:'))
                                if text:
                                    value = json.loads(text)
                                    if isinstance(value, dict) and body and value.get('id') == body.get('id'):
                                        return value, dict(response.headers)
                    if not content:
                        return {}, dict(response.headers)
                    if 'text/event-stream' in response.headers.get('content-type', ''):
                        messages = []
                        for block in bytes(content).decode().replace('\r\n', '\n').split('\n\n'):
                            text = '\n'.join(line[5:].lstrip() for line in block.splitlines() if line.startswith('data:'))
                            if text:
                                messages.append(json.loads(text))
                        wanted = body.get('id') if body else None
                        result = next((v for v in messages if v.get('id') == wanted), None)
                        if result is None:
                            raise ValueError('LibTV 没有返回对应请求结果')
                    else:
                        result = json.loads(content)
                    if not isinstance(result, dict):
                        raise ValueError('LibTV 响应格式不正确')
                    return result, dict(response.headers)
        except (httpx.HTTPError, UnicodeError, json.JSONDecodeError):
            raise ValueError('LibTV 网络或响应异常；未自动重试') from None

    def start(self, session):
        redirect = f'http://127.0.0.1:{self.settings.port}/integrations/libtv/callback'
        key = 'client'
        client = self.load(key)
        if not client or client.get('redirect_uri') != redirect:
            client, _ = self.request('/register', body={
                'client_name': '电商作业平台（本机）', 'redirect_uris': [redirect],
                'grant_types': ['authorization_code'], 'response_types': ['code'],
                'token_endpoint_auth_method': 'client_secret_post', 'scope': SCOPES})
            if not client.get('client_id') or not client.get('client_secret'):
                raise ValueError('LibTV 没有返回所需客户端凭据')
            client['redirect_uri'] = redirect
            self.save(key, client)
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        self.save('pending:' + session['session_id'], {
            'state': state, 'verifier': verifier, 'expires': time.time() + 600,
            'owner_id': session['owner_id'], 'client': client,
            'epoch': self.load('epoch:' + session['owner_id']) or 0})
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        return {'authorization_url': ORIGIN + '/authorize?' + urlencode({
            'response_type': 'code', 'client_id': client['client_id'], 'redirect_uri': redirect,
            'scope': SCOPES, 'resource': RESOURCE, 'state': state,
            'code_challenge': challenge, 'code_challenge_method': 'S256'})}

    def callback(self, session, state, code):
        key = 'pending:' + session['session_id']
        # Claim the one-shot state atomically before the token exchange.
        with self.db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT value FROM secrets WHERE key=?', (key,)).fetchone()
            pending = json.loads(protect(row[0], True)) if row else None
            if (not pending or pending['expires'] < time.time()
                    or pending['owner_id'] != session['owner_id']
                    or not secrets.compare_digest(pending['state'], state)):
                raise ValueError('授权已过期或不属于本次登录，请重新连接')
            conn.execute('DELETE FROM secrets WHERE key=?', (key,))
        client = pending['client']
        token, _ = self.request('/token', data={
            'grant_type': 'authorization_code', 'code': code,
            'client_id': client['client_id'], 'client_secret': client['client_secret'],
            'redirect_uri': client['redirect_uri'], 'code_verifier': pending['verifier'], 'resource': RESOURCE})
        if not isinstance(token.get('access_token'), str) or token.get('token_type', '').lower() != 'bearer':
            raise ValueError('LibTV 没有返回有效授权')
        ttl = token.get('expires_in')
        if type(ttl) not in (int, float) or not 0 < ttl <= 366 * 86400:
            raise ValueError('LibTV 授权有效期不明确，请重新连接')
        encrypted = protect(json.dumps({
            'access_token': token['access_token'], 'expires': time.time() + ttl,
            'scope': token.get('scope', SCOPES)}).encode())
        with self.db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT value FROM secrets WHERE key=?', ('epoch:' + session['owner_id'],)).fetchone()
            epoch = json.loads(protect(row[0], True)) if row else 0
            if epoch != pending['epoch']:
                raise ValueError('授权期间已断开连接，请重新授权')
            conn.execute('INSERT OR REPLACE INTO secrets VALUES (?,?)', ('token:' + session['owner_id'], encrypted))

    def status(self, owner):
        token = self.load('token:' + owner)
        connected = bool(token and token['expires'] > time.time() + 30)
        return {'connected': connected, 'status': 'CONNECTED' if connected else 'NEEDS_LOGIN',
                'generation_enabled': False, 'scope': token.get('scope') if token else None,
                'note': '只读连接可检查项目与模型；付费生成尚未开放。'}

    def disconnect(self, owner):
        # Remove local grant even if remote revocation is temporarily unavailable.
        token = self.load('token:' + owner)
        client = self.load('client')
        with self.db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT value FROM secrets WHERE key=?', ('epoch:' + owner,)).fetchone()
            epoch = json.loads(protect(row[0], True)) if row else 0
            conn.execute('INSERT OR REPLACE INTO secrets VALUES (?,?)', ('epoch:' + owner, protect(json.dumps(epoch + 1).encode())))
            conn.execute('DELETE FROM secrets WHERE key=?', ('token:' + owner,))
        revoked = False
        if token and client:
            try:
                self.request('/revoke', data={'token': token['access_token'],
                    'client_id': client['client_id'], 'client_secret': client['client_secret']})
                revoked = True
            except ValueError:
                pass
        return {'disconnected': True, 'remote_revoked': revoked}

    def call(self, owner, tool, arguments):
        if tool not in READ_TOOLS:
            raise ValueError('此入口只允许查询，不能制作、发布或扣费')
        token = self.load('token:' + owner)
        if not token or token['expires'] <= time.time() + 30:
            raise ValueError('请先连接 LibTV，授权过期后需要重新连接')
        access = token['access_token']
        init, headers = self.request('/mcp', token=access, body={
            'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
                'protocolVersion': '2025-03-26', 'capabilities': {},
                'clientInfo': {'name': 'ecommerce-workbench', 'version': '0.2.0'}}})
        version = init.get('result', {}).get('protocolVersion')
        if version not in ('2024-11-05', '2025-03-26', '2025-06-18'):
            raise ValueError('LibTV 协议协商失败')
        hdr = {'MCP-Protocol-Version': version}
        if headers.get('mcp-session-id'):
            hdr['Mcp-Session-Id'] = headers['mcp-session-id']
        self.request('/mcp', token=access, headers=hdr,
                     body={'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        result, _ = self.request('/mcp', token=access, headers=hdr, body={
            'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
            'params': {'name': tool, 'arguments': arguments}})
        if result.get('id') != 2 or result.get('error') or result.get('result', {}).get('isError'):
            raise ValueError('LibTV 查询失败，请检查授权和项目权限')
        return result.get('result', {})
