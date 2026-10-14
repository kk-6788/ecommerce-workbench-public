"""冻结方案、事务占用、持久任务执行与未知结果核对。"""
from __future__ import annotations
import json
import uuid
from dataclasses import asdict
from app.services.maintenance import maintenance_active
from app.db import connect
from app.domain.evidence import canonical_json, utcnow_iso
from app.services.audit import log_event
from app.services.approvals import _valid_approvals
from app.services.plans import get_plan, PlanSpec, CostSpec, plan_hash
from app.adapters.base import AdapterResult

class ApprovalRequiredError(ValueError): pass

def _attempt_dict(row):
    out = dict(row)
    if out.get('receipt_json'): out['receipt'] = json.loads(out['receipt_json'])
    return out

def _validate(conn, plan, registry):
    payload = json.loads(plan['payload_json'])
    cost = payload.get('_cost')
    spec = PlanSpec(product_id=plan['product_id'],action=plan['action'],provider=plan['provider'],account_id=plan['account_id'],
        requires_content=bool(plan['requires_content']),requires_cost=bool(plan['requires_cost']),
        payload={k:v for k,v in payload.items() if k!='_cost'},cost=CostSpec(**cost) if cost else None)
    if plan_hash(spec) != plan['plan_hash']: raise ApprovalRequiredError('方案 hash 与冻结内容不一致，请重建方案')
    if plan['expires_at'] < utcnow_iso() or plan['state'] in ('EXPIRED','REVOKED','FAILED','SUCCEEDED'):
        raise ApprovalRequiredError('方案已过期或不允许执行')
    try: adapter = registry.get(plan['provider'] or plan['action'])
    except KeyError as exc: raise ApprovalRequiredError('CAPABILITY_UNAVAILABLE：提供方未接入，未创建执行记录') from exc
    kind = payload.get('kind','WRITE')
    kind = kind if kind in ('IMAGE','VIDEO','READ') else 'WRITE'
    caps = adapter.capabilities().get('caps',[])
    caps = [asdict(c) if not isinstance(c,dict) else c for c in caps]
    cap = next((c for c in caps if c.get('kind')==kind), None)
    if not cap or cap.get('status') not in ('VERIFIED_READ','VERIFIED_WRITE'):
        raise ApprovalRequiredError('提供方动作能力未经核验')
    charged = cap.get('billing') != 'free' or plan['requires_cost'] or bool(cost and cost.get('maximum_amount'))
    if charged:
        if not plan['requires_cost'] or not cost or plan['cost_cap_minor'] is None:
            raise ApprovalRequiredError('收费或未知能力必须有 COST 审批和可信上限')
        if not cost.get('quote_source') or cost.get('failure_billing') in (None,'','unknown') or not cap.get('hard_cap_supported'):
            raise ApprovalRequiredError('缺可核验报价、失败计费依据或提供方硬上限能力')
        if not cap.get('verified_at') or not cap.get('evidence_id'):
            raise ApprovalRequiredError('收费能力缺实测时间和证据')
    kinds = (['CONTENT'] if plan['requires_content'] else []) + (['COST'] if charged else [])
    if not _valid_approvals(conn,plan['id'],plan['plan_hash'],kinds): raise ApprovalRequiredError('缺少有效审批')
    if plan['action'] == 'AI_CHECK':
        from app.services.ai_checks import validate_plan
        validate_plan(conn, plan)
    from app.services.plan_dependencies import validate_dependencies
    validate_dependencies(conn, plan['product_id'], payload)
    return adapter

def execute_plan(db_path, *, plan_id, registry, request=None, simulate_timeout=False, lease_token=None, deferred=False):
    conn = connect(db_path)
    try:
        conn.execute('BEGIN IMMEDIATE')
        existing=conn.execute('SELECT * FROM external_attempts WHERE plan_id=?',(plan_id,)).fetchone()
        if existing: return _attempt_dict(existing)
        if maintenance_active(conn) or conn.execute("SELECT 1 FROM runtime_state WHERE key='restore' AND value!='READY'").fetchone():
            raise ApprovalRequiredError('维护或恢复核对中，禁止新的外部执行')
        plan=conn.execute('SELECT * FROM operation_plans WHERE id=?',(plan_id,)).fetchone()
        if not plan: raise KeyError('方案不存在')
        _validate(conn,plan,registry)
        aid,jid=str(uuid.uuid4()),str(uuid.uuid4())
        now=utcnow_iso()
        conn.execute("INSERT INTO jobs(id,product_id,plan_id,kind,state,input_json,input_hash,created_at,updated_at) VALUES(?,?,?,'external','QUEUED',?,?,?,?)",
            (jid,plan['product_id'],plan_id,canonical_json({'plan_id':plan_id}),plan['plan_hash'],now,now))
        conn.execute("INSERT INTO external_attempts(id,plan_id,job_id,state,idempotency_key,request_hash,provider,prepared_at) VALUES(?,?,?,'PREPARED',?,?,?,?)",
            (aid,plan_id,jid,plan_id+':'+plan['plan_hash'],plan['plan_hash'],plan['provider'] or plan['action'],now))
        if plan['requires_cost']:
            approval=conn.execute("SELECT id FROM approvals WHERE plan_id=? AND kind='COST' AND decision='APPROVE' ORDER BY created_at DESC LIMIT 1",(plan_id,)).fetchone()[0]
            conn.execute("INSERT INTO cost_ledger VALUES(?,?,?,'RESERVED',?,?,?,?,?,?)",(str(uuid.uuid4()),aid,approval,plan['cost_cap_minor'],plan['currency'],plan['credit_cap'],'reserve-'+aid,canonical_json({'plan_hash':plan['plan_hash']}),now))
        conn.execute("UPDATE operation_plans SET state='RESERVED' WHERE id=?",(plan_id,))
        log_event(conn,actor_type='SYSTEM',actor_id='execution',event_type='EXECUTE_RESERVED',entity_type='PLAN',entity_id=plan_id,detail={'attempt':aid,'job':jid,'plan_hash':plan['plan_hash']})
        conn.commit()
        result=_attempt_dict(conn.execute('SELECT * FROM external_attempts WHERE id=?',(aid,)).fetchone())
    finally: conn.close()
    if deferred: return result
    from app.services.jobs import claim_job
    job=claim_job(db_path, job_id=jid)
    return run_attempt(db_path,job,registry,simulate_timeout=simulate_timeout) if job else result

def run_attempt(db_path,job,registry,simulate_timeout=False):
    conn=connect(db_path)
    try:
        conn.execute('BEGIN IMMEDIATE')
        active=conn.execute("SELECT * FROM jobs WHERE id=? AND state='RUNNING' AND lease_owner=?",(job['id'],job['lease_token'])).fetchone()
        if not active: raise ValueError('执行租约已失效')
        attempt=conn.execute('SELECT * FROM external_attempts WHERE job_id=?',(job['id'],)).fetchone()
        if not attempt or attempt['state']!='PREPARED': raise ValueError('请求已提交，必须核对')
        if active['cancel_requested']:
            abandon_prepared(conn,job['id'],'USER_CANCELLED_BEFORE_SUBMIT')
            conn.commit()
            return _attempt_dict(conn.execute('SELECT * FROM external_attempts WHERE id=?',(attempt['id'],)).fetchone())
        plan=conn.execute('SELECT * FROM operation_plans WHERE id=?',(job['plan_id'],)).fetchone()
        try: adapter=_validate(conn,plan,registry)
        except ValueError as exc:
            abandon_prepared(conn,job['id'],'DEPENDENCY_CHANGED')
            conn.commit()
            # EC-01: 重新查询最终状态——abandon_prepared 修改了 attempt/plan/job，
            # 返回撤销前的 PREPARED 行快照会让调用方误判为仍可提交。
            final=conn.execute('SELECT * FROM external_attempts WHERE job_id=?',(job['id'],)).fetchone()
            return {**_attempt_dict(final),'blocked_reason':str(exc)}
        conn.execute("UPDATE external_attempts SET state='SUBMITTED',submitted_at=? WHERE id=?",(utcnow_iso(),attempt['id']))
        conn.commit()
        payload=json.loads(plan['payload_json'])
        request={'plan_id':plan['id'],'plan_hash':plan['plan_hash'],'action':plan['action'],'provider':plan['provider'],'account_id':plan['account_id'],
            'payload':{k:v for k,v in payload.items() if k!='_cost'},'budget':payload.get('_cost'),'idempotency_key':attempt['idempotency_key']}
    finally: conn.close()
    try:
        if simulate_timeout: raise TimeoutError()
        result=adapter.execute(request,attempt['idempotency_key'])
    except Exception:
        # Once the submission boundary is crossed, transport/SDK exceptions cannot prove failure.
        result=AdapterResult(ok=False,code='UNKNOWN_RESULT',error_message='提交后连接异常，需核对，不自动重发')
    return _record_result(db_path,plan['id'],attempt['id'],job['id'],result,job['lease_token'])

def _ledger(conn,attempt_id,result):
    reservation=conn.execute("SELECT * FROM cost_ledger WHERE attempt_id=? AND entry_kind='RESERVED'",(attempt_id,)).fetchone()
    if not reservation: return result
    data=result.data or {}
    amount=data.get('actual_amount_minor')
    receipt=data.get('billing_receipt_id')
    known=type(amount) is int and amount>=0 and receipt and data.get('currency')==reservation['currency']
    if result.code=='UNKNOWN_RESULT' or not known or amount>reservation['amount_minor']:
        kind='PENDING_RECONCILE'
        result=AdapterResult(ok=False,code='UNKNOWN_RESULT',data={**data,'billing_note':'费用待核对'},provider_request_id=result.provider_request_id,is_synthetic=result.is_synthetic)
        events=[(kind,reservation['amount_minor'])]
    else: events=[('SETTLED',amount),('RELEASED',reservation['amount_minor']-amount)]
    for kind,value in events:
        conn.execute('INSERT OR IGNORE INTO cost_ledger VALUES(?,?,?,?,?,?,?,?,?,?)',(str(uuid.uuid4()),attempt_id,reservation['approval_id'],kind,value,reservation['currency'],None,attempt_id+':'+kind,canonical_json(data),utcnow_iso()))
    return result

def _record_result(db_path,plan_id,attempt_id,job_id,result,lease_token=None):
    conn=connect(db_path)
    try:
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT * FROM external_attempts WHERE id=?',(attempt_id,)).fetchone()
        if row['state'] in ('SUCCEEDED','FAILED'): return _attempt_dict(row)
        if lease_token:
            active=conn.execute("SELECT 1 FROM jobs WHERE id=? AND lease_owner=? AND state='RUNNING'",(job_id,lease_token)).fetchone()
            if not active: raise ValueError('旧执行者租约失效，拒绝保存结果')
        from app.services.ai_checks import record_result
        record_result(conn, plan_id, result)
        result=_ledger(conn,attempt_id,result)
        state='SUCCEEDED' if result.ok else 'UNKNOWN' if result.code=='UNKNOWN_RESULT' else 'FAILED'
        receipt={**(result.data or {}),'is_synthetic':result.is_synthetic,'error_code':result.code}
        now=utcnow_iso()
        conn.execute('UPDATE external_attempts SET state=?,provider_request_id=COALESCE(?,provider_request_id),receipt_json=?,reconciled_at=? WHERE id=?',(state,result.provider_request_id,canonical_json(receipt),now,attempt_id))
        conn.execute('UPDATE operation_plans SET state=? WHERE id=?',(state,plan_id))
        conn.execute('UPDATE jobs SET state=?,result_json=?,updated_at=?,lease_owner=NULL,lease_until=NULL WHERE id=?',('RECONCILING' if state=='UNKNOWN' else state,canonical_json(receipt),now,job_id))
        plan=conn.execute('SELECT plan_hash FROM operation_plans WHERE id=?',(plan_id,)).fetchone()
        log_event(conn,actor_type='SYSTEM',actor_id='execution',event_type='EXECUTE_'+state,entity_type='PLAN',entity_id=plan_id,detail={'attempt':attempt_id,'plan_hash':plan['plan_hash']})
        if not conn.execute("SELECT 1 FROM external_attempts WHERE state IN ('SUBMITTED','UNKNOWN','RECONCILING')").fetchone():
            conn.execute("UPDATE runtime_state SET value='READY' WHERE key='restore'")
        conn.commit()
        return _attempt_dict(conn.execute('SELECT * FROM external_attempts WHERE id=?',(attempt_id,)).fetchone())
    finally: conn.close()

def reconcile_attempt(db_path,attempt_id,registry):
    conn=connect(db_path)
    try: row=conn.execute('SELECT * FROM external_attempts WHERE id=?',(attempt_id,)).fetchone()
    finally: conn.close()
    if not row: raise KeyError('attempt 不存在')
    if row['state'] not in ('UNKNOWN','RECONCILING'): return _attempt_dict(row)
    if not row['provider_request_id']: return {**_attempt_dict(row),'provider_status':'NO_PROVIDER_ID','note':'缺提供方标识，请核对提供方后台并登记人工核对证据'}
    try: result=registry.get(row['provider']).reconcile(row['provider_request_id'])
    except Exception: result=AdapterResult(ok=False,code='UNKNOWN_RESULT')
    if not result.ok and result.code!='CONFIRMED_FAILED': result.code='UNKNOWN_RESULT'
    return _record_result(db_path,row['plan_id'],attempt_id,row['job_id'],result)

get_plan_quiet = get_plan

def abandon_prepared(conn,job_id,reason):
    """Only the PREPARED state proves the supplier was not called."""
    attempt=conn.execute("SELECT * FROM external_attempts WHERE job_id=? AND state='PREPARED'",(job_id,)).fetchone()
    if not attempt: return False
    conn.execute("UPDATE external_attempts SET state='FAILED',receipt_json=? WHERE id=?",(canonical_json({'code':reason,'submitted':False}),attempt['id']))
    conn.execute("UPDATE operation_plans SET state='REVOKED' WHERE id=?",(attempt['plan_id'],))
    conn.execute("UPDATE jobs SET state='CANCELLED',error_code=?,lease_owner=NULL,lease_until=NULL WHERE id=?",(reason,job_id))
    reserved=conn.execute("SELECT * FROM cost_ledger WHERE attempt_id=? AND entry_kind='RESERVED'",(attempt['id'],)).fetchone()
    if reserved:
        conn.execute("INSERT OR IGNORE INTO cost_ledger VALUES(?,?,?,'RELEASED',?,?,?,?,?,?)",(str(uuid.uuid4()),attempt['id'],reserved['approval_id'],reserved['amount_minor'],reserved['currency'],reserved['credit_amount'],attempt['id']+':RELEASED',canonical_json({'reason':reason,'submitted':False}),utcnow_iso()))
    log_event(conn,actor_type='SYSTEM',actor_id='execution',event_type='PREPARED_CANCELLED',entity_type='PLAN',entity_id=attempt['plan_id'],detail={'reason':reason,'plan_hash':attempt['request_hash']})
    return True

def manual_reconcile(db_path,attempt_id,*,owner_id,evidence_id,succeeded,actual_amount_minor=None,currency='CNY',billing_receipt_id=None):
    from app.services.ai_checks import ensure_attempt_owner
    ensure_attempt_owner(db_path, attempt_id, owner_id)
    conn=connect(db_path)
    try:
        row=conn.execute('SELECT a.*,p.product_id FROM external_attempts a JOIN operation_plans p ON p.id=a.plan_id WHERE a.id=?',(attempt_id,)).fetchone()
        if not row: raise ValueError('请求不存在')
        if row['state'] not in ('UNKNOWN','RECONCILING'): raise ValueError('只有未知结果允许人工核对')
        ev=conn.execute('SELECT * FROM evidence WHERE id=? AND product_id=?',(evidence_id,row['product_id'])).fetchone()
        if not ev or ev['evidence_kind']!='FACT': raise ValueError('人工核对须引用本商品的事实证据')
        if not conn.execute('SELECT 1 FROM owners WHERE id=?',(owner_id,)).fetchone(): raise ValueError('owner 不存在')
    finally: conn.close()
    result=AdapterResult(ok=succeeded,code='OK' if succeeded else 'CONFIRMED_FAILED',is_synthetic=bool(ev['is_synthetic']),data={'manual_owner':owner_id,'evidence_id':evidence_id,'actual_amount_minor':actual_amount_minor,'currency':currency,'billing_receipt_id':billing_receipt_id})
    return _record_result(db_path,row['plan_id'],attempt_id,row['job_id'],result)
