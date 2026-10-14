"""Official TypeSafe HTTP contract, exactly one request and no SDK retries.

The documented endpoint lacks a provider-enforced per-request money cap. Live
execution therefore remains blocked by the shared execution gate until verified.
"""
import json
import re
import time
import httpx
from app.adapters.base import AdapterResult, Capability
from app.db import connect
from app.domain.ai_checks import validate_response
from app.services.provider_credentials import Credentials
from app.services import ai_checks


class TypeSafeAdapter:
    name = 'typesafe'

    def __init__(self, settings, transport=None):
        self.settings = settings
        self.transport = transport

    def capabilities(self):
        return {'name': self.name, 'caps': [Capability(kind='READ', status='UNVERIFIED', billing='paid',
            hard_cap_supported=False, notes='HTTP 接口已实现；单次费用硬上限、失败账单口径和真实联调尚未核验') ]}

    def probe(self, request):
        return AdapterResult(False, code='NEEDS_EVIDENCE', error_message='没有已核验的免费探测接口，保存 Key 不触发收费测试')

    collect = probe
    quote = probe

    def reconcile(self, provider_request_id):
        return AdapterResult(False, code='UNKNOWN_RESULT', provider_request_id=provider_request_id,
                             error_message='官方 API 未提供已核验的请求账单查询，需人工核对')

    def execute(self, request, execution_token):
        conn = connect(self.settings.db_path)
        try:
            attempt = conn.execute("SELECT a.*,p.account_id FROM external_attempts a JOIN operation_plans p ON p.id=a.plan_id WHERE a.plan_id=? AND a.state='SUBMITTED' AND a.idempotency_key=?", (request['plan_id'], execution_token)).fetchone()
            if not attempt:
                raise ValueError('必须通过已审批执行任务调用 TypeSafe')
            plan = conn.execute('SELECT * FROM operation_plans WHERE id=?', (request['plan_id'],)).fetchone()
            ai_checks.validate_plan(conn, plan)
            run = conn.execute('SELECT * FROM ai_check_runs WHERE plan_id=?', (request['plan_id'],)).fetchone()
            body = json.loads(run['request_json'])
            config = Credentials(self.settings).resolve(run['owner_id'], 'typesafe')
            import hashlib
            expected = json.loads(run['snapshot_json'])['config']
            if config['model'] != expected['model'] or hashlib.sha256(config['api_key'].encode()).hexdigest() != expected['credential_hash']:
                raise ValueError('密钥已变更，拒绝使用未经本方案批准的新密钥')
        finally:
            conn.close()
        return self.evaluate(body, config['api_key'])

    def evaluate(self, body, api_key):
        """Transport seam; production calls only from execute after durable approval."""
        request_id = None
        try:
            with httpx.Client(transport=self.transport, timeout=45, trust_env=False, follow_redirects=False) as client:
                with client.stream('POST', 'https://api.typesafe.ai/v1/systemone',
                    headers={'Authorization': 'Bearer ' + api_key}, json=body) as response:
                    request_id = response.headers.get('x-request-id')
                    if request_id and (len(request_id) > 200 or api_key in request_id):
                        request_id = None
                    if response.status_code != 200:
                        return AdapterResult(False, code='UNKNOWN_RESULT', provider_request_id=request_id,
                            data={'ai_error': 'HTTP_' + str(response.status_code)}, error_message='调用未得到可用结果，费用待核对；未重试')
                    raw = bytearray()
                    deadline = time.monotonic() + 60
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 1_000_000 or time.monotonic() > deadline:
                            raise ValueError('响应超过限制')
                    value = json.loads(raw)
            answers = validate_response(value, body['questions'])
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}', value['model']) or api_key in value['model']:
                raise ValueError('返回模型标识错误')
            clean = {'model': value['model'], 'usage': {k: value['usage'][k] for k in ('input_tokens', 'output_tokens')},
                     'answers': {qid: {k: answer[k] for k in ('type', 'choice', 'confidence', 'probabilities')} for qid, answer in answers.items()}}
            # Token usage is not an invoice. Never invent a cash receipt from usage.
            return AdapterResult(True, data={'ai_response': clean}, provider_request_id=request_id)
        except (httpx.HTTPError, ValueError, TypeError, KeyError):
            return AdapterResult(False, code='UNKNOWN_RESULT', provider_request_id=request_id,
                data={'ai_error': 'TRANSPORT_OR_CONTRACT_ERROR'}, error_message='服务或格式异常，费用待核对；未自动重试')
