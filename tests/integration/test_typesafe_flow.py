import copy
import json
import sqlite3
import uuid
import httpx
import pytest
from fastapi.testclient import TestClient
from app.auth import create_owner
from app.db import connect, init_db
from app.main import create_app
from app.settings import Settings
from app.adapters.typesafe import TypeSafeAdapter
from app.adapters.registry import Registry
from app.adapters.base import Capability
from app.domain import ai_checks as policy
from app.services import ai_checks, approvals, execution, jobs
from app.services.products import create_product, add_evidence
from app.services.provider_credentials import Credentials


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    import app.services.libtv_connection as vault
    monkeypatch.setattr(vault, 'protect', lambda value, decrypt=False: value)
    settings = Settings(tmp_path / 'data'); settings.ensure_dirs(); init_db(settings.db_path)
    owner = create_owner(settings.db_path, 'owner', 'password')
    Credentials(settings).save(owner, 'typesafe', 'synthetic-key-not-real', 'jev-latest')
    product = create_product(settings.db_path, title='测试杯', category='杯具', strategy_mode='PUHUO', is_synthetic=True)
    source = add_evidence(settings.db_path, product_id=product['id'], opportunity_id=None,
        source_key='test', content={'note': '本款 500ml 玻璃杯；SKU A。'}, is_synthetic=True)
    return settings, owner, product, source


def prepare(ctx, **kwargs):
    settings, owner, product, source = ctx
    return ai_checks.create(settings.db_path, owner, **{
        'product_id': product['id'], 'expected_revision': 1, 'use_case': 'evidence_support',
        'claim': '容量500ml', 'sku': 'A', 'evidence_ids': [source['id']], 'maximum_amount': '1',
        'quote_source': 'synthetic-only', 'failure_billing': 'synthetic-only', **kwargs})


def approve(ctx, run):
    settings, owner, _, _ = ctx
    for kind in ('CONTENT', 'COST'):
        approvals.approve_plan(settings.db_path, plan_id=run['plan_id'], plan_hash=run['plan']['plan_hash'], owner_id=owner, kind=kind)


def answer(body, choice='PASS', confidence=.97):
    answers = {}
    for qid, question in body['questions'].items():
        chosen = choice if choice in question['criteria'] else next(iter(question['criteria']))
        others = len(question['criteria']) - 1
        answers[qid] = {'type': 'choice', 'choice': chosen, 'confidence': confidence,
            'probabilities': {key: .98 if key == chosen else .02 / others for key in question['criteria']}}
    return {'model': 'jev-test', 'answers': answers, 'usage': {'input_tokens': 100, 'output_tokens': 10}}


class SimulatedVerified(TypeSafeAdapter):
    def capabilities(self):
        return {'caps': [Capability('READ', 'VERIFIED_READ', verified_at='synthetic-only', billing='paid',
            evidence_id='synthetic-only', hard_cap_supported=True)]}


def test_complete_worker_chain_and_separate_billing_unknown(ctx):
    settings, owner, product, source = ctx
    calls = []
    def serve(request):
        calls.append(request)
        assert request.url == 'https://api.typesafe.ai/v1/systemone'
        assert request.headers['Authorization'] == 'Bearer synthetic-key-not-real'
        return httpx.Response(200, json=answer(json.loads(request.content)), headers={'x-request-id': 'real-header-in-mock'})
    run = prepare(ctx); approve(ctx, run)
    registry = Registry({'typesafe': SimulatedVerified(settings, httpx.MockTransport(serve))})
    queued = execution.execute_plan(settings.db_path, plan_id=run['plan_id'], registry=registry, deferred=True)
    assert not calls
    from app.worker import run_worker
    run_worker(data_dir=settings.data_dir, once=True, registry=registry)
    result = ai_checks.get(settings.db_path, owner, run['id'])
    assert result['state'] == 'SHADOW_CANDIDATE'
    assert result['returned_model'] == 'jev-test'
    assert result['attempt']['state'] == 'UNKNOWN'  # usage != money receipt
    assert len(result['answers']) == 2
    again = execution.execute_plan(settings.db_path, plan_id=run['plan_id'], registry=registry)
    assert again['id'] == queued['id'] and len(calls) == 1
    with pytest.raises(ValueError, match='先核对'):
        prepare(ctx, parent=run['id'])
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM cost_ledger WHERE entry_kind='PENDING_RECONCILE'").fetchone()[0] == 1
        assert conn.execute('SELECT revision FROM products WHERE id=?', (product['id'],)).fetchone()[0] == 1
        assert conn.execute('SELECT evidence_kind FROM evidence WHERE id=?', (source['id'],)).fetchone()[0] == 'FACT'
    event = dict(action='HUMAN_PASSED', reason='核对本款原文后接受，仅作参考', expected_revision=result['revision'], snapshot_hash=result['snapshot_hash'], idempotency_key='same-click')
    first = ai_checks.review(settings.db_path, owner, run['id'], **event)
    assert ai_checks.review(settings.db_path, owner, run['id'], **event)['id'] == first['id']
    with pytest.raises(ai_checks.Conflict):
        ai_checks.review(settings.db_path, owner, run['id'], **{**event, 'reason': 'different'})
    with connect(settings.db_path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM approvals').fetchone()[0] == 2
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE ai_review_events SET reason='changed'")


def test_live_unverified_capability_prevents_any_request(ctx):
    settings, owner, _, _ = ctx
    run = prepare(ctx); approve(ctx, run)
    def forbidden(request):
        pytest.fail('real execution gate must block before transport')
    registry = Registry({'typesafe': TypeSafeAdapter(settings, httpx.MockTransport(forbidden))})
    with pytest.raises(ValueError, match='未经核验'):
        execution.execute_plan(settings.db_path, plan_id=run['plan_id'], registry=registry)
    with connect(settings.db_path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM external_attempts').fetchone()[0] == 0


def test_response_after_source_change_is_preserved_but_stale(ctx):
    settings, owner, product, _ = ctx
    def serve(request):
        # A write succeeds during HTTP: the network call holds no database lock.
        with connect(settings.db_path) as conn:
            conn.execute('UPDATE products SET revision=revision+1 WHERE id=?', (product['id'],))
        return httpx.Response(200, json=answer(json.loads(request.content)))
    run = prepare(ctx); approve(ctx, run)
    execution.execute_plan(settings.db_path, plan_id=run['plan_id'], registry=Registry({'typesafe': SimulatedVerified(settings, httpx.MockTransport(serve))}))
    result = ai_checks.get(settings.db_path, owner, run['id'])
    assert result['state'] == 'STALE' and result['response']
    with pytest.raises(ValueError):
        ai_checks.review(settings.db_path, owner, run['id'], action='HUMAN_PASSED', reason='cannot override stale', expected_revision=result['revision'], snapshot_hash=result['snapshot_hash'], idempotency_key='x')


def test_missing_evidence_cannot_revoke_then_pass(ctx):
    settings, owner, _, _ = ctx
    run = prepare(ctx, evidence_ids=[])
    assert run['state'] == 'WAIT_EVIDENCE'
    ai_checks.review(settings.db_path, owner, run['id'], action='REVOKE', reason='still missing', expected_revision=run['revision'], snapshot_hash=run['snapshot_hash'], idempotency_key='revoke')
    result = ai_checks.get(settings.db_path, owner, run['id'])
    with pytest.raises(ValueError, match='缺少'):
        ai_checks.review(settings.db_path, owner, run['id'], action='HUMAN_PASSED', reason='cannot pass', expected_revision=result['revision'], snapshot_hash=result['snapshot_hash'], idempotency_key='pass')


def test_duplicate_snapshot_reused_budget_change_new_plan_and_key_change_stale(ctx):
    settings, owner, _, _ = ctx
    run = prepare(ctx)
    assert prepare(ctx)['id'] == run['id']
    assert prepare(ctx, maximum_amount='2')['id'] != run['id']
    Credentials(settings).save(owner, 'typesafe', 'replacement-test-key', 'jev-latest')
    assert ai_checks.get(settings.db_path, owner, run['id'])['state'] == 'STALE'


def test_cross_product_evidence_rejected(ctx):
    settings, _, _, source = ctx
    other = create_product(settings.db_path, title='其他', category='其他', strategy_mode='PUHUO')
    with pytest.raises(ValueError, match='不属于'):
        prepare(ctx, product_id=other['id'])


def test_keyword_none_is_valid_classification_not_failure(ctx):
    settings, owner, _, _ = ctx
    run = prepare(ctx, use_case='keyword_intent', keyword='玻璃杯怎么清洗', evidence_ids=[])
    def serve(request):
        data = answer(json.loads(request.content), 'NONE')
        return httpx.Response(200, json=data)
    approve(ctx, run)
    execution.execute_plan(settings.db_path, plan_id=run['plan_id'], registry=Registry({'typesafe': SimulatedVerified(settings, httpx.MockTransport(serve))}))
    assert ai_checks.get(settings.db_path, owner, run['id'])['state'] == 'SHADOW_CANDIDATE'


@pytest.mark.parametrize('confidence,status', [(.799999, 'HUMAN_REQUIRED'), (.80, 'REVIEW_REQUIRED'), (.949999, 'REVIEW_REQUIRED'), (.95, 'SHADOW_CANDIDATE')])
def test_raw_confidence_boundaries(confidence, status):
    assert policy.route({'choice': 'PASS', 'confidence': confidence}) == status
    assert policy.route({'choice': 'INSUFFICIENT', 'confidence': .999}) == 'WAIT_EVIDENCE'
    assert policy.route({'choice': 'FAIL', 'confidence': .999}) == 'REVIEW_REQUIRED'


@pytest.mark.parametrize('mutation', ['bool', 'nan', 'percent', 'missing', 'extra', 'wrong_type', 'wrong_choice', 'sum'])
def test_malformed_response_rejected(mutation):
    qs = policy.questions('evidence_support', {})
    response = answer({'questions': qs})
    target = response['answers']['claim_support']
    if mutation == 'bool': target['confidence'] = True
    if mutation == 'nan': target['confidence'] = float('nan')
    if mutation == 'percent': target['confidence'] = '99%'
    if mutation == 'missing': del response['answers']['sku_scope']
    if mutation == 'extra': response['answers']['extra'] = target
    if mutation == 'wrong_type': target['type'] = 'noul'
    if mutation == 'wrong_choice': target['choice'] = 'FAIL'
    if mutation == 'sum': target['probabilities']['PASS'] = .5
    with pytest.raises(ValueError): policy.validate_response(response, qs)


def test_transport_timeout_never_retries_and_preserves_unknown(ctx):
    settings, owner, _, _ = ctx
    calls = []
    def serve(request):
        calls.append(request)
        raise httpx.ReadTimeout('secret must not escape')
    run = prepare(ctx); approve(ctx, run)
    result = execution.execute_plan(settings.db_path, plan_id=run['plan_id'], registry=Registry({'typesafe': SimulatedVerified(settings, httpx.MockTransport(serve))}))
    assert result['state'] == 'UNKNOWN' and len(calls) == 1
    assert 'secret must not escape' not in json.dumps(result)
    assert ai_checks.get(settings.db_path, owner, run['id'])['state'] == 'ERROR'


def test_owner_read_review_and_approval_isolation(ctx):
    settings, owner, _, _ = ctx
    run = prepare(ctx)
    bob = create_owner(settings.db_path, 'bob', 'password')
    with pytest.raises(KeyError): ai_checks.get(settings.db_path, bob, run['id'])
    with pytest.raises(ValueError): approvals.approve_plan(settings.db_path, plan_id=run['plan_id'], plan_hash=run['plan']['plan_hash'], owner_id=bob, kind='CONTENT')
    client = TestClient(create_app(settings=settings))
    assert client.get('/ai-review', follow_redirects=False).status_code == 307
    csrf = client.post('/api/v1/auth/login', json={'username': 'bob', 'password': 'password'}).json()['data']['csrf']
    assert client.get('/api/v1/ai-checks/' + run['id']).status_code == 404
    assert client.post('/api/v1/ai-checks/' + run['id'] + '/execute', headers={'X-CSRF-Token': csrf}).status_code == 404
    assert client.get('/ai-review').status_code == 200


def test_notification_read_does_not_resolve_and_backup_excludes_key(ctx, tmp_path):
    settings, owner, _, _ = ctx
    run = prepare(ctx, evidence_ids=[])
    notification = ai_checks.notifications(settings.db_path, owner)[0]
    rows = ai_checks.notifications(settings.db_path, owner, notification['id'])
    assert rows[0]['read_at'] and not rows[0]['resolved_at']
    from app.services.backup import backup_data, restore_data
    backup = backup_data(settings, tmp_path / 'backup')
    assert not (backup / 'private').exists()
    restored = restore_data(backup, tmp_path / 'restore')
    with connect(restored / 'app.db') as conn:
        assert conn.execute('SELECT COUNT(*) FROM ai_check_runs').fetchone()[0] == 1
    assert b'synthetic-key-not-real' not in (backup / 'app.db').read_bytes()


def test_source_revision_invalidates_old_run_and_recheck_can_select_new(ctx):
    settings, owner, product, _ = ctx
    run = prepare(ctx)
    newer = add_evidence(settings.db_path, product_id=product['id'], opportunity_id=None,
        source_key='test', content={'note': '修订为 300ml；SKU A。'}, is_synthetic=True)
    assert ai_checks.get(settings.db_path, owner, run['id'])['state'] == 'STALE'
    with pytest.raises(ValueError, match='新版本'): prepare(ctx, parent=run['id'])
    updated = prepare(ctx, parent=run['id'], evidence_ids=[newer['id']], claim='容量300ml')
    assert updated['state'] == 'PLANNED'
    assert updated['request']['state']['sources'][0]['id'] == newer['id']


def test_repeat_request_evidence_reopens_pending_notification(ctx):
    settings, owner, _, _ = ctx
    run = prepare(ctx, evidence_ids=[])
    ai_checks.review(settings.db_path, owner, run['id'], action='REQUEST_EVIDENCE', reason='尚未补齐', expected_revision=run['revision'], snapshot_hash=run['snapshot_hash'], idempotency_key='need-more')
    notes = ai_checks.notifications(settings.db_path, owner)
    assert len(notes) == 1 and notes[0]['resolved_at'] is None


def test_inflight_human_rejection_is_conflict_not_silently_overwritten(ctx):
    settings, owner, _, _ = ctx
    run = prepare(ctx); approve(ctx, run)
    def serve(request):
        with pytest.raises(ai_checks.Conflict, match='排队或执行'):
            ai_checks.review(settings.db_path, owner, run['id'], action='HUMAN_REJECTED', reason='在途禁止终审', expected_revision=run['revision'], snapshot_hash=run['snapshot_hash'], idempotency_key='during-flight')
        return httpx.Response(200, json=answer(json.loads(request.content)))
    execution.execute_plan(settings.db_path, plan_id=run['plan_id'], registry=Registry({'typesafe': SimulatedVerified(settings, httpx.MockTransport(serve))}))
    assert ai_checks.get(settings.db_path, owner, run['id'])['reviews'] == []


def test_generic_routes_cannot_read_cancel_or_settle_another_owners_ai(ctx):
    settings, owner, _, source = ctx
    run = prepare(ctx); approve(ctx, run)
    registry = Registry({'typesafe': SimulatedVerified(settings, httpx.MockTransport(lambda request: httpx.Response(200, json=answer(json.loads(request.content)))))})
    attempt = execution.execute_plan(settings.db_path, plan_id=run['plan_id'], registry=registry)
    create_owner(settings.db_path, 'bob', 'password')
    with TestClient(create_app(settings=settings, adapters=registry)) as client:
        csrf = client.post('/api/v1/auth/login', json={'username': 'bob', 'password': 'password'}).json()['data']['csrf']
        client.headers['X-CSRF-Token'] = csrf
        assert client.get('/api/v1/plans/' + run['plan_id']).status_code == 404
        assert not client.get('/api/v1/plans').json()['data']
        assert not client.get('/api/v1/jobs').json()['data']
        assert not client.get('/api/v1/recovery').json()['data']['attempts']
        assert 'AI_CHECK' not in client.get('/launch').text
        assert run['plan_id'][:8] not in client.get('/launch').text
        assert client.post('/api/v1/jobs/' + attempt['job_id'] + '/cancel').status_code == 404
        assert client.post('/api/v1/attempts/' + attempt['id'] + '/reconcile').status_code == 404
        assert client.post('/api/v1/attempts/' + attempt['id'] + '/manual-reconcile', json={'evidence_id': source['id'], 'succeeded': True, 'actual_amount_minor': 0, 'billing_receipt_id': 'fake'}).status_code == 404
    with connect(settings.db_path) as conn:
        assert conn.execute('SELECT state FROM external_attempts WHERE id=?', (attempt['id'],)).fetchone()[0] == 'UNKNOWN'


def test_key_changed_between_validation_and_use_is_not_sent(ctx, monkeypatch):
    settings, owner, _, _ = ctx
    run = prepare(ctx); approve(ctx, run)
    old_validate = ai_checks.validate_plan
    n = 0
    def validating(conn, plan):
        nonlocal n
        old_validate(conn, plan)
        n += 1
        # _validate at reservation, again at worker, then execute adapter.
        if n == 3:
            Credentials(settings).save(owner, 'typesafe', 'replacement-key', 'jev-latest')
    monkeypatch.setattr(ai_checks, 'validate_plan', validating)
    def forbidden(request): pytest.fail('unapproved new Key must never be transmitted')
    execution.execute_plan(settings.db_path, plan_id=run['plan_id'], registry=Registry({'typesafe': SimulatedVerified(settings, httpx.MockTransport(forbidden))}))
    assert ai_checks.get(settings.db_path, owner, run['id'])['state'] == 'STALE'


def test_hard_kill_and_new_migration_are_enforced(ctx):
    settings, owner, product, _ = ctx
    from app.api.business_routes import _ensure_model_versions
    from app.domain.evidence import utcnow_iso
    versions = _ensure_model_versions(settings.db_path)
    with connect(settings.db_path) as conn:
        conn.execute('INSERT INTO predictions(id,product_id,strategy_version_id,engine_version_id,outcome_version_id,input_json,input_hash,result_json,decision,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
            (str(uuid.uuid4()), product['id'], versions['STRATEGY'], versions['ENGINE'], versions['OUTCOME'], '{"revision":1}', 'synthetic-hash', '{}', 'KILL', utcnow_iso()))
    run = prepare(ctx)
    assert run['state'] == 'BLOCKED'
    with pytest.raises(ValueError):
        ai_checks.review(settings.db_path, owner, run['id'], action='HUMAN_PASSED', reason='cannot override', expected_revision=run['revision'], snapshot_hash=run['snapshot_hash'], idempotency_key='blocked')
    init_db(settings.db_path)
    with connect(settings.db_path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM ai_check_runs').fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE ai_check_runs SET model='another'")


def test_oversized_or_secret_source_not_sent(ctx):
    settings, _, product, _ = ctx
    source = add_evidence(settings.db_path, product_id=product['id'], opportunity_id=None,
        source_key='secret-test', content={'note': 'API_KEY=sample-secret-not-real'}, is_synthetic=True)
    with pytest.raises(ValueError, match='去敏'): prepare(ctx, evidence_ids=[source['id']])
    with pytest.raises(ValueError, match='范围'): prepare(ctx, maximum_amount='99999999999999999999')
