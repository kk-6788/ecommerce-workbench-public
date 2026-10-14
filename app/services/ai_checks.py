"""Immutable source snapshots, approval plans, shadow results and human review."""
import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone

from app.db import connect
from app.domain.evidence import canonical_json, content_hash, utcnow_iso
from app.domain import ai_checks as policy
from app.services.provider_credentials import Credentials
from app.services.plans import PlanSpec, CostSpec, plan_hash
from app.services.plan_dependencies import dependency_snapshot
from app.settings import Settings


class Conflict(ValueError):
    pass


def configuration(db_path, owner):
    from pathlib import Path
    settings = Settings(data_dir=Path(db_path).parent)
    config = Credentials(settings).resolve(owner, 'typesafe')
    return {'model': config['model'], 'credential_hash': hashlib.sha256(config['api_key'].encode()).hexdigest()}


def policy_status():
    return {'version': policy.POLICY_VERSION, 'mode': 'SHADOW', 'auto_release_enabled': False,
            'uses': {name: 'SHADOW' for name in policy.USES},
            'review_min': .80, 'candidate_min': .95,
            'billing_enabled': False,
            'execution_blocker': 'TypeSafe 单次费用硬上限及失败计费尚未核验，真实收费执行保持关闭。'}


def _clean_text(text):
    if not isinstance(text, str) or not text.strip():
        return ''
    # Refuse obvious secrets/identifiers rather than silently rewrite evidence.
    if re.search(r'(?i)(bearer\s+\S+|(?:api[_ -]?key|password|密码|密钥)\s*[:=]\s*\S+|sk-[a-zA-Z0-9_-]{12,}|\b1[3-9]\d{9}\b|[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,})', text):
        raise ValueError('资料包含疑似密钥或个人联系方式，请选择去敏后的资料再建立方案')
    return text.strip()


def snapshot(conn, *, product_id, use_case, claim, sku, evidence_ids, keyword, config, parent=None):
    product = conn.execute('SELECT * FROM products WHERE id=?', (product_id,)).fetchone()
    if not product:
        raise ValueError('商品不存在')
    prediction = conn.execute('SELECT id,decision,input_json FROM predictions WHERE product_id=? ORDER BY created_at DESC LIMIT 1', (product_id,)).fetchone()
    hard_ok = not (prediction and prediction['decision'] == 'KILL' and json.loads(prediction['input_json']).get('revision') == product['revision'])
    sources, fingerprints = [], []
    for eid in sorted(set(evidence_ids)):
        row = conn.execute('SELECT * FROM evidence WHERE id=? AND product_id=?', (eid, product_id)).fetchone()
        if not row:
            raise ValueError('来源不存在或不属于当前商品')
        latest = conn.execute('SELECT id FROM evidence WHERE product_id=? AND source_key=? ORDER BY collected_at DESC,id DESC LIMIT 1', (product_id, row['source_key'])).fetchone()
        if latest[0] != eid:
            raise ValueError('来源已有新版本，请选择最新的证据记录')
        raw = json.loads(row['content_json'])
        text = next((raw[k] for k in ('text', 'excerpt', 'note', 'description') if isinstance(raw.get(k), str) and raw[k].strip()), '')
        text = _clean_text(text)
        if len(text) > 12000:
            raise ValueError('单份来源超过 12000 字，请先登记有上下文的证据片段，不能静默截断')
        sources.append({'id': eid, 'text': text, 'observed_at': row['observed_at'],
                        'evidence_kind': row['evidence_kind'], 'is_synthetic': bool(row['is_synthetic'])})
        fingerprints.append({'id': eid, 'hash': content_hash(dict(row))})
    clusters = {}
    if use_case == 'keyword_intent':
        categories = [r[0] for r in conn.execute('SELECT DISTINCT category_group FROM opportunities ORDER BY category_group LIMIT 101')]
        if len(categories) > 100:
            raise ValueError('已有分类过多，请先整理分类，不能截断候选集')
        clusters = {'C' + str(i): _clean_text(cat) for i, cat in enumerate(categories)}
    state = {'subject': {'id': product_id, 'title': _clean_text(product['title']),
                         'category': _clean_text(product['category']), 'revision': product['revision'], 'sku': _clean_text(sku)},
             'claim': _clean_text(claim), 'sources': sources, 'keyword': _clean_text(keyword), 'candidate_clusters': clusters}
    complete = bool(state['claim'] and state['subject']['sku'] and sources and all(s['text'] for s in sources)) if use_case == 'evidence_support' else bool(state['keyword'])
    qs = policy.questions(use_case, clusters)
    snap = {'product_id': product_id, 'use_case': use_case, 'state': state,
            'evidence': fingerprints, 'questions': qs, 'config': config, 'policy_version': policy.POLICY_VERSION,
            'parent': parent, 'complete': complete, 'hard_ok': hard_ok}
    if len(canonical_json(state).encode()) > 64000:
        raise ValueError('本次资料超过 64KB，请缩小审核范围')
    return snap


def _current(conn, row):
    snap = json.loads(row['snapshot_json'])
    state = snap['state']
    try:
        db_path = conn.execute('PRAGMA database_list').fetchone()[2]
        now = snapshot(conn, product_id=row['product_id'], use_case=row['use_case'],
            claim=state['claim'], sku=state['subject']['sku'], evidence_ids=[e['id'] for e in snap['evidence']],
            keyword=state['keyword'], config=configuration(db_path, row['owner_id']), parent=snap['parent'])
        now['budget'] = snap['budget']
        return row['expires_at'] > utcnow_iso() and content_hash(now) == row['snapshot_hash']
    except ValueError:
        return False


def _notify(conn, row, state):
    conn.execute('INSERT INTO ai_notifications VALUES(?,?,?,?,NULL,NULL,?) ON CONFLICT(owner_id,run_id,kind) DO UPDATE SET read_at=NULL,resolved_at=NULL',
        (str(uuid.uuid4()), row['owner_id'], row['id'], state, utcnow_iso()))


def _refresh(conn, row):
    if row['state'] != 'STALE' and not _current(conn, row):
        conn.execute("UPDATE ai_check_runs SET state='STALE',reason=?,revision=revision+1 WHERE id=?", (policy.REASONS['STALE'], row['id']))
        _notify(conn, row, 'STALE')
        return conn.execute('SELECT * FROM ai_check_runs WHERE id=?', (row['id'],)).fetchone()
    return row


def _owned(conn, run_id, owner):
    row = conn.execute('SELECT * FROM ai_check_runs WHERE id=? AND owner_id=?', (run_id, owner)).fetchone()
    if not row:
        raise KeyError('审核记录不存在')
    return row


def create(db_path, owner, *, product_id, use_case, expected_revision, claim='', sku='', evidence_ids=None,
           keyword='', maximum_amount='0', quote_source='', failure_billing='unknown', parent=None):
    if use_case not in policy.USES:
        raise ValueError('本期只支持证据审核和关键词判断')
    from decimal import Decimal, InvalidOperation
    try:
        amount = Decimal(maximum_amount)
        if not amount.is_finite() or amount < 0 or amount * 100 != (amount * 100).to_integral_value():
            raise ValueError('费用上限必须为非负金额且最多两位小数')
        cap = int(amount * 100)
        if cap > 2**63 - 1:
            raise ValueError('费用上限超出支持范围')
    except InvalidOperation:
        raise ValueError('费用上限格式错误') from None
    config = configuration(db_path, owner)
    conn = connect(db_path)
    try:
        conn.execute('BEGIN IMMEDIATE')
        snap = snapshot(conn, product_id=product_id, use_case=use_case, claim=claim, sku=sku,
                        evidence_ids=evidence_ids or [], keyword=keyword, config=config, parent=parent)
        if snap['state']['subject']['revision'] != expected_revision:
            raise Conflict('商品版本已变化，请刷新后重新建立方案')
        if parent:
            prior = _owned(conn, parent, owner)
            if prior['product_id'] != product_id or prior['use_case'] != use_case:
                raise ValueError('复查必须针对同一商品和场景')
            pending = conn.execute("SELECT 1 FROM external_attempts WHERE plan_id=? AND state NOT IN ('SUCCEEDED','FAILED')", (prior['plan_id'],)).fetchone()
            if pending:
                raise ValueError('原调用结果或费用未确定，请先核对，不能重复提交')
        snap['budget'] = {'maximum_amount': str(amount), 'quote_source': quote_source, 'failure_billing': failure_billing}
        digest = content_hash(snap)
        old = conn.execute('SELECT id FROM ai_check_runs WHERE owner_id=? AND snapshot_hash=?', (owner, digest)).fetchone()
        if old:
            conn.commit()
            return get(db_path, owner, old[0])
        rid, pid = str(uuid.uuid4()), str(uuid.uuid4())
        request = {'state': snap['state'], 'questions': snap['questions'], 'model': config['model']}
        payload = {'kind': 'READ', 'ai_run_id': rid, 'owner_id': owner, 'snapshot_hash': digest}
        payload['_dependencies'] = dependency_snapshot(conn, product_id, payload)
        cost = CostSpec(maximum_amount=str(amount), quote_source=quote_source, failure_billing=failure_billing)
        spec = PlanSpec(product_id=product_id, action='AI_CHECK', provider='typesafe', account_id=owner,
                        requires_content=True, requires_cost=True, payload=payload, cost=cost)
        ph = plan_hash(spec)
        payload['_cost'] = vars(cost)
        expires = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
        conn.execute('INSERT INTO operation_plans(id,product_id,action,plan_hash,payload_json,requires_content,requires_cost,cost_cap_minor,currency,state,expires_at,created_at,provider,account_id) VALUES(?,?,?,?,?,1,1,?,\'CNY\',\'DRAFT\',?,?,\'typesafe\',?)',
            (pid, product_id, 'AI_CHECK', ph, canonical_json(payload), cap, expires, utcnow_iso(), owner))
        state = 'BLOCKED' if not snap['hard_ok'] else 'PLANNED' if snap['complete'] else 'WAIT_EVIDENCE'
        reason = '审核方案已建立，尚未调用模型' if state == 'PLANNED' else policy.REASONS[state]
        conn.execute('INSERT INTO ai_check_runs(id,owner_id,product_id,use_case,state,snapshot_json,snapshot_hash,request_json,policy_version,model,plan_id,reason,expires_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (rid, owner, product_id, use_case, state, canonical_json(snap), digest, canonical_json(request), policy.POLICY_VERSION, config['model'], pid, reason, expires, utcnow_iso()))
        if state in ('WAIT_EVIDENCE', 'BLOCKED'):
            _notify(conn, {'id': rid, 'owner_id': owner}, state)
        conn.commit()
    finally:
        conn.close()
    return get(db_path, owner, rid)


def get(db_path, owner, run_id):
    conn = connect(db_path)
    try:
        conn.execute('BEGIN IMMEDIATE')
        row = _refresh(conn, _owned(conn, run_id, owner))
        result = dict(row)
        for key in ('snapshot', 'request', 'response'):
            result[key] = json.loads(result.pop(key + '_json')) if row[key + '_json'] else None
        result['snapshot']['config'].pop('credential_hash', None)
        result['answers'] = [{**dict(r), 'answer': json.loads(r['answer_json'])} for r in conn.execute('SELECT * FROM ai_check_answers WHERE run_id=? ORDER BY question_id', (run_id,))]
        result['reviews'] = [dict(r) for r in conn.execute('SELECT * FROM ai_review_events WHERE run_id=? ORDER BY created_at', (run_id,))]
        result['plan'] = dict(conn.execute('SELECT id,plan_hash,state,cost_cap_minor FROM operation_plans WHERE id=?', (row['plan_id'],)).fetchone())
        attempt = conn.execute('SELECT state,provider_request_id,id FROM external_attempts WHERE plan_id=?', (row['plan_id'],)).fetchone()
        result['attempt'] = dict(attempt) if attempt else None
        conn.commit()
        return result
    finally:
        conn.close()


def list_runs(db_path, owner, limit=50, offset=0):
    conn = connect(db_path)
    try:
        ids = [r[0] for r in conn.execute('SELECT id FROM ai_check_runs WHERE owner_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?', (owner, limit, offset))]
    finally:
        conn.close()
    return [get(db_path, owner, rid) for rid in ids]


def validate_plan(conn, plan):
    row = conn.execute('SELECT * FROM ai_check_runs WHERE plan_id=?', (plan['id'],)).fetchone()
    if not row or not _current(conn, row):
        raise ValueError('AI 审核资料或配置已改变，请创建新方案')
    if row['state'] != 'PLANNED' or not json.loads(row['snapshot_json'])['complete'] or not json.loads(row['snapshot_json'])['hard_ok']:
        raise ValueError('AI 审核缺资料或已有结果，不能提交')
    payload = json.loads(plan['payload_json'])
    if payload.get('owner_id') != row['owner_id'] or payload.get('snapshot_hash') != row['snapshot_hash']:
        raise ValueError('AI 方案与审核归属不匹配')
    for kind in ('CONTENT', 'COST'):
        approval = conn.execute('SELECT owner_id FROM approvals WHERE plan_id=? AND kind=? ORDER BY created_at DESC LIMIT 1', (plan['id'], kind)).fetchone()
        if not approval or approval[0] != row['owner_id']:
            raise ValueError('AI 调用必须由密钥所属用户本人审批')


def ensure_plan_owner(db_path, plan_id, owner):
    conn = connect(db_path)
    try:
        row = conn.execute('SELECT action,account_id FROM operation_plans WHERE id=?', (plan_id,)).fetchone()
        if row and row['action'] == 'AI_CHECK' and row['account_id'] != owner:
            raise KeyError('方案不存在')
    finally:
        conn.close()


def ensure_attempt_owner(db_path, attempt_id, owner):
    conn = connect(db_path)
    try:
        row = conn.execute('SELECT plan_id FROM external_attempts WHERE id=?', (attempt_id,)).fetchone()
    finally:
        conn.close()
    if row:
        ensure_plan_owner(db_path, row[0], owner)


def record_result(conn, plan_id, result):
    row = conn.execute('SELECT * FROM ai_check_runs WHERE plan_id=?', (plan_id,)).fetchone()
    if not row or row['response_json']:
        return
    raw = result.data.get('ai_response')
    states = []
    try:
        answers = policy.validate_response(raw, json.loads(row['request_json'])['questions'])
        for qid, answer in answers.items():
            status = policy.route(answer)
            states.append(status)
            conn.execute('INSERT INTO ai_check_answers VALUES(?,?,?,?)', (row['id'], qid, canonical_json(answer), status))
        state = policy.aggregate(states)
    except (ValueError, TypeError, KeyError):
        state = 'ERROR'
        raw = None
    if not _current(conn, row):
        state = 'STALE'
    if not json.loads(row['snapshot_json'])['hard_ok']:
        state = 'BLOCKED'
    conn.execute('UPDATE ai_check_runs SET state=?,reason=?,response_json=?,returned_model=?,revision=revision+1 WHERE id=?',
        (state, policy.REASONS[state], canonical_json(raw) if raw else None, raw.get('model') if raw else None, row['id']))
    _notify(conn, row, state)


def review(db_path, owner, run_id, *, action, reason, expected_revision, snapshot_hash, idempotency_key):
    allowed = ('HUMAN_PASSED', 'HUMAN_REJECTED', 'REQUEST_EVIDENCE', 'REVOKE')
    if action not in allowed or not reason.strip() or not idempotency_key.strip():
        raise ValueError('请选择有效操作并填写人工判断理由')
    conn = connect(db_path)
    try:
        conn.execute('BEGIN IMMEDIATE')
        row = _refresh(conn, _owned(conn, run_id, owner))
        previous = conn.execute('SELECT * FROM ai_review_events WHERE owner_id=? AND idempotency_key=?', (owner, idempotency_key)).fetchone()
        if previous:
            if any(previous[k] != value for k, value in {'run_id': run_id, 'action': action, 'reason': reason, 'snapshot_hash': snapshot_hash, 'expected_revision': expected_revision}.items()):
                raise Conflict('重复提交标识对应不同的审核动作')
            conn.commit()
            return dict(previous)
        if row['revision'] != expected_revision or row['snapshot_hash'] != snapshot_hash:
            conn.commit()
            raise Conflict('审核记录已变化，请刷新后重试')
        if conn.execute("SELECT 1 FROM external_attempts WHERE plan_id=? AND state IN ('PREPARED','SUBMITTED')", (row['plan_id'],)).fetchone():
            raise Conflict('审核请求正在排队或执行，请先等待结果或取消未提交任务')
        if action == 'HUMAN_PASSED' and row['state'] not in ('SHADOW_CANDIDATE', 'REVIEW_REQUIRED', 'HUMAN_REQUIRED'):
            raise ValueError('缺证据、过期、服务错误或尚无模型结果，不能标记通过')
        if action == 'HUMAN_PASSED' and (not row['response_json'] or not json.loads(row['snapshot_json'])['hard_ok'] or not json.loads(row['snapshot_json'])['complete'] or conn.execute("SELECT 1 FROM ai_check_answers WHERE run_id=? AND state='WAIT_EVIDENCE'", (run_id,)).fetchone()):
            raise ValueError('原始审核缺少可复核结果或资料，不能绕过缺口标记通过')
        event = str(uuid.uuid4())
        conn.execute('INSERT INTO ai_review_events VALUES(?,?,?,?,?,?,?,?,?)', (event, run_id, owner, action, reason, snapshot_hash, expected_revision, idempotency_key, utcnow_iso()))
        status = {'REQUEST_EVIDENCE': 'WAIT_EVIDENCE', 'REVOKE': 'HUMAN_REQUIRED'}.get(action, action)
        conn.execute('UPDATE ai_check_runs SET state=?,reason=?,revision=revision+1 WHERE id=?', (status, reason, run_id))
        conn.execute('UPDATE ai_notifications SET resolved_at=? WHERE run_id=? AND owner_id=?', (utcnow_iso(), run_id, owner))
        if action in ('REQUEST_EVIDENCE', 'REVOKE'):
            _notify(conn, row, status)
        conn.commit()
        return dict(conn.execute('SELECT * FROM ai_review_events WHERE id=?', (event,)).fetchone())
    finally:
        conn.close()


def notifications(db_path, owner, read_id=None):
    conn = connect(db_path)
    try:
        if read_id:
            count = conn.execute('UPDATE ai_notifications SET read_at=? WHERE id=? AND owner_id=?', (utcnow_iso(), read_id, owner)).rowcount
            if not count:
                raise KeyError('通知不存在')
            conn.commit()
        return [dict(r) for r in conn.execute('SELECT * FROM ai_notifications WHERE owner_id=? ORDER BY created_at DESC LIMIT 100', (owner,))]
    finally:
        conn.close()
