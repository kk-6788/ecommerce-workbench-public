"""R2 defect regressions. All providers and business data are local synthetic fixtures."""
import csv
import io
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from datetime import datetime,timedelta,timezone
from decimal import Decimal as D
import pytest
from fastapi.testclient import TestClient
from app.db import connect,init_db,MIGRATIONS_DIR
from app.auth import create_owner
from app.main import create_app
from app.services.products import create_product,add_evidence
from app.services import media,jobs,plans,execution,approvals
from app.adapters.fake import FakeAdapter
from app.adapters.registry import Registry
from app.adapters.base import AdapterResult
from app.settings import Settings
from app.services.backup import backup_data,restore_data
from tests.conftest import make_png_bytes

@pytest.fixture
def ctx(tmp_path):
    s=Settings(tmp_path/'data');s.ensure_dirs();init_db(s.db_path)
    owner=create_owner(s.db_path,'review','synthetic-password')
    return s,owner

@pytest.fixture
def client(ctx):
    s,_=ctx
    c=TestClient(create_app(settings=s,test_mode=True))
    result=c.post('/api/v1/auth/login',json={'username':'review','password':'synthetic-password'})
    c.headers['X-CSRF-Token']=result.json()['data']['csrf']
    return c

def product(db,**kw):
    return create_product(db,title='合成样本',category='测试',strategy_mode='PUHUO',**kw)

def plan(ctx,**kw):
    s,owner=ctx
    p=product(s.db_path)
    spec=plans.PlanSpec(product_id=p['id'],action='fake',provider='fake',requires_content=True,**kw)
    pl=plans.create_plan(s.db_path,spec=spec)
    for kind in ['CONTENT']+(['COST'] if spec.requires_cost else []):
        approvals.approve_plan(s.db_path,plan_id=pl['id'],plan_hash=pl['plan_hash'],owner_id=owner,kind=kind)
    return pl

def test_missing_adapter_does_not_leave_attempt(ctx):
    s,_=ctx;pl=plan(ctx)
    with pytest.raises(execution.ApprovalRequiredError): execution.execute_plan(s.db_path,plan_id=pl['id'],registry=Registry())
    with connect(s.db_path) as c: assert c.execute('SELECT COUNT(*) FROM external_attempts').fetchone()[0]==0

@pytest.mark.parametrize('error',[TimeoutError(),ConnectionResetError(),OSError('socket closed')])
def test_transport_exception_is_unknown(ctx,error):
    class Broken(FakeAdapter):
        def execute(self,*args): raise error
    s,_=ctx;pl=plan(ctx)
    result=execution.execute_plan(s.db_path,plan_id=pl['id'],registry=Registry({'fake':Broken()}))
    assert result['state']=='UNKNOWN'
    assert jobs.get_job(s.db_path,result['job_id'])['state']=='RECONCILING'

def test_unknown_paid_capability_zero_calls(ctx):
    class Paid(FakeAdapter):
        def capabilities(self):
            out=super().capabilities()
            for c in out['caps']: c.billing='paid'
            return out
    s,_=ctx;pl=plan(ctx);adapter=Paid()
    with pytest.raises(execution.ApprovalRequiredError): execution.execute_plan(s.db_path,plan_id=pl['id'],registry=Registry({'fake':adapter}))
    assert adapter.submit_count==0

@pytest.mark.parametrize('amount,expected',[(20,'SUCCEEDED'),(250,'UNKNOWN')])
def test_cost_ledger_uses_actual_receipt(ctx,amount,expected):
    class Paid(FakeAdapter):
        def execute(self,*args): return AdapterResult(ok=True,provider_request_id='fixture',is_synthetic=True,data={'actual_amount_minor':amount,'currency':'CNY','billing_receipt_id':'fixture-bill'})
    s,_=ctx;pl=plan(ctx,requires_cost=True,cost=plans.CostSpec(maximum_amount='1.00',quote_source='fixture',failure_billing='free'))
    result=execution.execute_plan(s.db_path,plan_id=pl['id'],registry=Registry({'fake':Paid()}))
    assert result['state']==expected
    with connect(s.db_path) as c:
        settled=c.execute("SELECT amount_minor FROM cost_ledger WHERE entry_kind='SETTLED'").fetchone()
        assert (settled[0] if settled else None)==(20 if amount==20 else None)

def test_cancel_prepared_finishes_all_records(ctx):
    s,_=ctx;pl=plan(ctx,requires_cost=True,cost=plans.CostSpec(maximum_amount='1',quote_source='fixture',failure_billing='free'))
    result=execution.execute_plan(s.db_path,plan_id=pl['id'],registry=Registry({'fake':FakeAdapter()}),deferred=True)
    jobs.cancel_job(s.db_path,result['job_id'])
    with connect(s.db_path) as c:
        assert c.execute('SELECT state FROM external_attempts').fetchone()[0]=='FAILED'
        assert c.execute("SELECT amount_minor FROM cost_ledger WHERE entry_kind='RELEASED'").fetchone()[0]==100

def test_restore_old_prepared_never_resubmits(ctx,tmp_path):
    s,owner=ctx;pl=plan(ctx);adapter=FakeAdapter()
    result=execution.execute_plan(s.db_path,plan_id=pl['id'],registry=Registry({'fake':adapter}),deferred=True)
    backup=backup_data(s,tmp_path/'backup')
    original=jobs.claim_job(s.db_path)
    assert execution.run_attempt(s.db_path,original,Registry({'fake':adapter}))['state']=='SUCCEEDED'
    target=restore_data(backup,tmp_path/'restored')
    assert jobs.claim_job(target/'app.db') is None
    assert execution.execute_plan(target/'app.db',plan_id=pl['id'],registry=Registry({'fake':adapter}))['state']=='UNKNOWN'
    ev=add_evidence(target/'app.db',product_id=pl['product_id'],opportunity_id=None,source_key='restore-proof',content={'confirmed':'already completed'},is_synthetic=True)
    execution.manual_reconcile(target/'app.db',result['id'],owner_id=owner,evidence_id=ev['id'],succeeded=True)
    with connect(target/'app.db') as c: assert c.execute("SELECT value FROM runtime_state WHERE key='restore'").fetchone()[0]=='READY'


def test_restore_unknown_needs_evidenced_manual_reconcile(ctx,tmp_path):
    s,owner=ctx;pl=plan(ctx)
    class Broken(FakeAdapter):
        def execute(self,*args): raise TimeoutError()
    original=execution.execute_plan(s.db_path,plan_id=pl['id'],registry=Registry({'fake':Broken()}))
    target=restore_data(backup_data(s,tmp_path/'backup'),tmp_path/'restored')
    newctx=(Settings(target),owner);newplan=plan(newctx)
    with pytest.raises(execution.ApprovalRequiredError): execution.execute_plan(target/'app.db',plan_id=newplan['id'],registry=Registry({'fake':FakeAdapter()}))
    ev=add_evidence(target/'app.db',product_id=pl['product_id'],opportunity_id=None,source_key='manual-proof',content={'confirmed':'not accepted'},is_synthetic=True)
    execution.manual_reconcile(target/'app.db',original['id'],owner_id=owner,evidence_id=ev['id'],succeeded=False)
    assert execution.execute_plan(target/'app.db',plan_id=newplan['id'],registry=Registry({'fake':FakeAdapter()}))['state']=='SUCCEEDED'

def test_stale_worker_cannot_overwrite(ctx):
    s,_=ctx;j=jobs.enqueue_job(s.db_path,kind='echo');old=jobs.claim_job(s.db_path,lease_seconds=0)
    jobs.recover_expired(s.db_path);new=jobs.claim_job(s.db_path)
    jobs.finish_job(s.db_path,j['id'],state='SUCCEEDED',result={'new':True},lease_token=new['lease_token'])
    jobs.finish_job(s.db_path,j['id'],state='FAILED',lease_token=old['lease_token'])
    assert jobs.get_job(s.db_path,j['id'])['state']=='SUCCEEDED'

def test_worker_heartbeats_during_long_work(ctx,monkeypatch):
    from app import worker
    import time
    s,_=ctx;j=jobs.enqueue_job(s.db_path,kind='echo')
    def handler(job):
        time.sleep(.3)
        assert jobs.recover_expired(s.db_path)==[]
        return {'ok':True}
    monkeypatch.setattr(worker,'_handler',handler)
    worker.run_worker(data_dir=s.data_dir,once=True,lease_seconds=.12)
    assert jobs.get_job(s.db_path,j['id'])['state']=='SUCCEEDED'

def test_revoke_ready_refreshes_state(ctx):
    s,owner=ctx;pl=plan(ctx)
    approvals.revoke_approval(s.db_path,plan_id=pl['id'],plan_hash=pl['plan_hash'],owner_id=owner,kind='CONTENT')
    assert plans.get_plan(s.db_path,pl['id'])['state']=='WAITING_APPROVAL'

@pytest.mark.parametrize('column',['provider','account_id'])
def test_plan_new_columns_immutable(ctx,column):
    import sqlite3
    s,_=ctx;pl=plan(ctx)
    with connect(s.db_path) as c,pytest.raises(sqlite3.IntegrityError): c.execute(f'UPDATE operation_plans SET {column}=? WHERE id=?',('changed',pl['id']))

def test_same_bytes_review_is_per_usage(ctx):
    s,_=ctx;a,b=product(s.db_path),product(s.db_path)
    first=media.store_asset(s.db_path,s.assets_dir,product_id=a['id'],kind='IMAGE',role='MAIN',data=make_png_bytes(),filename='a.png')
    media.mark_review(s.db_path,first['id'],'APPROVED',usage_id=first['usage_id'])
    second=media.store_asset(s.db_path,s.assets_dir,product_id=b['id'],kind='IMAGE',role='MAIN',data=make_png_bytes(),filename='a.png',is_synthetic=True)
    assert second['id']==first['id'] and second['review_status']=='PENDING' and second['is_synthetic']==1
    media.mark_review(s.db_path,second['id'],'REJECTED',usage_id=second['usage_id'])
    with connect(s.db_path) as c: assert c.execute('SELECT review_status FROM asset_usages WHERE id=?',(first['usage_id'],)).fetchone()[0]=='APPROVED'

def test_backfill_legacy_asset_usage(tmp_path):
    from app.db import _split_sql
    db=tmp_path/'legacy.db';c=connect(db)
    c.execute('CREATE TABLE schema_migrations(name TEXT PRIMARY KEY,applied_at TEXT)')
    for path in sorted(MIGRATIONS_DIR.glob('*.sql'))[:3]:
        for sql in _split_sql(path.read_text(encoding='utf-8')): c.execute(sql)
        c.execute("INSERT INTO schema_migrations VALUES(?,datetime('now'))",(path.name,))
    c.execute("INSERT INTO products VALUES('p',NULL,NULL,'p','x','PUHUO','DISCOVERY','{}',1,0,'t','t')")
    c.execute("INSERT INTO assets VALUES('a','p','IMAGE','MAIN',NULL,'a.png','h','image/png','{}',NULL,NULL,'APPROVED','t',0)")
    c.commit();c.close();init_db(db);init_db(db)
    with connect(db) as c:
        rows=c.execute('SELECT * FROM asset_usages').fetchall()
        assert len(rows)==1 and rows[0]['review_status']=='APPROVED'

def test_backup_does_not_change_queue_and_blocks_claim(ctx,tmp_path,monkeypatch):
    import app.services.backup as backup
    s,_=ctx;j=jobs.enqueue_job(s.db_path,kind='echo')
    original=backup._inventory
    def paused(path):
        assert jobs.claim_job(s.db_path) is None
        assert jobs.get_job(s.db_path,j['id'])['state']=='QUEUED'
        return original(path)
    monkeypatch.setattr(backup,'_inventory',paused)
    backup.backup_data(s,tmp_path/'backup')
    assert jobs.claim_job(s.db_path)['id']==j['id']

@pytest.mark.parametrize('damage',['manifest','database','asset'])
def test_restore_rejects_incomplete_without_target(ctx,tmp_path,damage):
    s,_=ctx;p=product(s.db_path)
    a=media.store_asset(s.db_path,s.assets_dir,product_id=p['id'],kind='IMAGE',role='MAIN',data=make_png_bytes(),filename='a.png')
    folder=backup_data(s,tmp_path/'backup')
    file=folder/('backup-manifest.json' if damage=='manifest' else 'app.db' if damage=='database' else 'assets/'+a['relative_path'])
    file.unlink()
    with pytest.raises(ValueError): restore_data(folder,tmp_path/'restore')
    assert not (tmp_path/'restore').exists()

def test_private_evidence_cannot_enter_backup(ctx,tmp_path):
    import hashlib
    s,_=ctx;p=product(s.db_path);(s.private_dir/'secret').write_bytes(b'synthetic-not-a-real-secret')
    ev=add_evidence(s.db_path,product_id=p['id'],opportunity_id=None,source_key='legacy')
    with connect(s.db_path) as c:
        c.execute('UPDATE evidence SET source_path=?,content_json=? WHERE id=?',('../private/secret',json.dumps({'_file':{'sha256':hashlib.sha256(b'synthetic-not-a-real-secret').hexdigest()}}),ev['id']))
    with pytest.raises(ValueError): backup_data(s,tmp_path/'backup')
    assert not (tmp_path/'backup').exists()

def test_five_product_http_flow_and_no_cross_review(client,ctx):
    batch=client.post('/api/v1/batches',json={'name':'练习批次','week_start':'2026-09-19'}).json()['data']
    ids=[]
    for i in range(5):
        p=client.post('/api/v1/products',json={'title':f'测试{i}','category':'宠物','is_synthetic':True,'facts':{'median_price':'34','conservative_procurement':'10','lowest_procurement':'10','taobao_samples':5,'top_payments':3000,'supply_samples':3,'procurement_source':'FACT'}}).json()['data'];pid=p['id'];ids.append(pid)
        assert client.post(f'/api/v1/products/{pid}/evidence',json={'source_key':'same-source','content':{'note':'fixture'},'is_synthetic':True}).status_code==200
        assert client.post(f'/api/v1/products/{pid}/offers',json={'supplier':'fixture','sku_key':'S','specification':{},'procurement_minor':1000,'verification_status':'VERIFIED'}).status_code==200
        prediction=client.post(f'/api/v1/products/{pid}/predictions',json={}).json()['data'];assert prediction['decision']=='TEST'
        for role in ('MAIN','SKU','DETAIL'):
            upload=client.post(f'/api/v1/products/{pid}/assets',data={'kind':'IMAGE','role':role,'sku_key':'S','is_synthetic':'1'},files={'file':('a.png',make_png_bytes(),'image/png')})
            assert upload.status_code==200,upload.text
            asset=upload.json()['data']
            assert client.post('/api/v1/assets/'+asset['id']+'/review',json={'status':'APPROVED','usage_id':asset['usage_id']}).status_code==200
    client.post('/api/v1/batches/'+batch['id']+'/products',json={'product_ids':ids})
    progress=client.get('/api/v1/batches').json()['data'][0]
    assert len(progress['products'])==5 and progress['ready_count']==0
    assert all(p['stage']=='READY' for p in progress['products'])
    for page in ('/','/selection','/media','/launch','/review','/product?id='+ids[0]):
        r=client.get(page);assert r.status_code==200,r.text
    p=client.get('/api/v1/products/'+ids[0]).json()['data'];a=p['assets'][0]
    client.post('/api/v1/assets/'+a['id']+'/review',json={'status':'REJECTED','usage_id':a['usage_id']})
    progress=client.get('/api/v1/batches').json()['data'][0]
    assert next(p for p in progress['products'] if p['product_id']==ids[0])['stage']=='REVIEW'
    assert all(p['stage']=='READY' for p in progress['products'] if p['product_id']!=ids[0])

@pytest.mark.parametrize('field,value',[('taobao_samples',-1),('median_price','NaN'),('supply_samples',True),('needs_supply_verify','false')])
def test_invalid_facts_rejected_at_http(client,field,value):
    r=client.post('/api/v1/products',json={'title':'坏输入','category':'X','facts':{field:value}})
    assert r.status_code==422

def test_effective_prediction_hash_and_gates(client):
    p=client.post('/api/v1/products',json={'title':'p','category':'x','facts':{'median_price':'34','conservative_procurement':'10','lowest_procurement':'10','top_payments':3000,'taobao_samples':3,'supply_samples':3,'procurement_source':'PROXY'}}).json()['data']
    route=f"/api/v1/products/{p['id']}/predictions"
    a=client.post(route,json={}).json()['data'];b=client.post(route,json={'median_price':'50'}).json()['data']
    assert a['decision']==b['decision']=='WAIT' and a['input_hash']!=b['input_hash']
    assert b['snapshot']['input_snapshot']['effective_input']['median_price']=='50'

def test_pm_cli_nonempty_independent_processes(ctx,tmp_path):
    s,_=ctx
    product(s.db_path,facts={'market':'CN','platform':'douyin','target_user':'fixture','currency':'CNY','field_evidence':{}})
    env={**os.environ,'PYTHONUTF8':'1'}
    for command,extra in [('score',[]),('backtest',[]),('compare',[]),('export-candidates',['--output',str(tmp_path/'out.csv')])]:
        r=subprocess.run([sys.executable,'-m','app.cli',command,'--data-dir',str(s.data_dir),*extra],cwd=Path(__file__).resolve().parents[2],env=env,capture_output=True,text=True,encoding='utf-8',timeout=30)
        assert r.returncode==0,r.stderr
    with (tmp_path/'out.csv').open(encoding='utf-8-sig') as stream:
        row=next(csv.DictReader(stream));assert len(row)==47 and row['platform']=='douyin'

def test_calibration_accept_concurrent_one_version(ctx):
    from app.api.business_routes import _ensure_model_versions
    from app.domain.calibration import save_proposal,accept_proposal
    s,owner=ctx;versions=_ensure_model_versions(s.db_path)
    prop=save_proposal(s.db_path,model_version_id=versions['STRATEGY'],proposed={'samples':3,'config':{'engine':'legacy-v4.2.2','minimum_margin':'8'}},comparison={'new_accuracy':.5})
    results=[]
    def accept(version):
        try: results.append(accept_proposal(s.db_path,proposal_id=prop['id'],owner_id=owner,new_version=version))
        except ValueError as e: results.append(e)
    threads=[threading.Thread(target=accept,args=(f'v{i}',)) for i in (2,3)]
    for t in threads:t.start()
    for t in threads:t.join()
    assert sum(isinstance(r,dict) for r in results)==1
    with connect(s.db_path) as c:assert c.execute("SELECT COUNT(*) FROM model_versions WHERE kind='STRATEGY'").fetchone()[0]==2

def test_model_selection_changes_prediction_and_can_return_v1(ctx,client):
    from app.api.business_routes import _ensure_model_versions
    from app.domain.calibration import save_proposal,accept_proposal
    s,owner=ctx;v=_ensure_model_versions(s.db_path)
    p=client.post('/api/v1/products',json={'title':'正常牵引绳','category':'x','facts':{'median_price':'34','conservative_procurement':'10','lowest_procurement':'10','top_payments':3000,'taobao_samples':3,'supply_samples':3,'procurement_source':'FACT'}}).json()['data']
    route=f"/api/v1/products/{p['id']}/predictions"
    old=client.post(route,json={}).json()['data'];assert old['decision']=='TEST'
    prop=save_proposal(s.db_path,model_version_id=v['STRATEGY'],proposed={'samples':1,'config':{'engine':'legacy-v4.2.2','minimum_margin':'20'}},comparison={'new_accuracy':1})
    newer=accept_proposal(s.db_path,proposal_id=prop['id'],owner_id=owner,new_version='v2')
    assert client.post('/api/v1/models/select',json={'version_id':newer['id']}).status_code==200
    assert client.post(route,json={}).json()['data']['decision']=='WAIT'
    assert client.post('/api/v1/models/select',json={'version_id':v['STRATEGY']}).status_code==200
    assert client.post(route,json={}).json()['data']['decision']=='TEST'
    with connect(s.db_path) as c: assert c.execute('SELECT decision FROM predictions WHERE id=?',(old['id'],)).fetchone()[0]=='TEST'


def test_outcome_csv_typed_import_references_and_idempotency(ctx,tmp_path):
    from app.api.business_routes import _ensure_model_versions
    from app.cli import main
    from app.domain.pm_validation import pm_fields,csv_safe
    s,_=ctx;p=product(s.db_path);_ensure_model_versions(s.db_path)
    ev=add_evidence(s.db_path,product_id=p['id'],opportunity_id=None,source_key='manual-observation',content={'fixture':'isolated database'})
    columns=list(pm_fields()['outcome'])
    row={k:None for k in columns}
    row.update(result_status='INSUFFICIENT_DATA',final_outcome='WAIT',currency='CNY',metric_definitions={},candidate_id=p['id'],model_version='v1',test_start_date='2026-09-01T00:00:00+00:00',test_end_date='2026-09-05T00:00:00+00:00',platform='taobao',account_id='test-account',channel='organic',source_timezone='UTC',granularity='day',measure_mode='INTERVAL',orders=0,impressions=10,contribution_margin='-2.50',evidence_ids=[ev['id']],is_synthetic=False)
    path=tmp_path/'outcomes.csv'
    def write():
        with path.open('w',encoding='utf-8-sig',newline='') as f:
            w=csv.writer(f);w.writerow(columns);w.writerow([csv_safe(row[k]) for k in columns])
    write();args=['import-outcomes','--data-dir',str(s.data_dir),'--input',str(path)]
    assert main(args)==0 and main(args)==0
    with connect(s.db_path) as c:
        rows=c.execute('SELECT * FROM metric_snapshots').fetchall();assert len(rows)==1
        saved=json.loads(rows[0]['metrics_json']);assert saved['orders']==0 and saved['contribution_margin']=='-2.50' and saved['clicks'] is None
    row['model_version']='missing';write();assert main(args)==1
    row['model_version']='v1';row['evidence_ids']=['foreign'];write();assert main(args)==1


def test_pm_dictionary_all_types_implemented():
    from app.domain.pm_validation import pm_fields,_TYPE_CHECKS,validate_section
    for section in ('candidate','outcome','prediction_snapshot'):
        for spec in pm_fields().get(section,{}).values(): assert spec['type'] in _TYPE_CHECKS
    assert validate_section('candidate',{'timestamp':'not-a-date'})

def test_crashed_backup_marker_does_not_lock_queue(ctx):
    s,_=ctx;j=jobs.enqueue_job(s.db_path,kind='echo')
    from app.services.maintenance import backup_lock
    with backup_lock(s.data_dir) as acquired:
        assert acquired
        with connect(s.db_path) as c:c.execute("INSERT INTO runtime_state VALUES('maintenance','crashed-process')");c.commit()
        assert jobs.claim_job(s.db_path) is None
    assert jobs.claim_job(s.db_path)['id']==j['id']
