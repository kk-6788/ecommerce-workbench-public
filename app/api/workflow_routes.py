"""网页作业入口；只写本地记录，外部执行仍需逐项审批。"""
import json
from fastapi import APIRouter, Depends, Request, HTTPException
from pydantic import BaseModel, Field, ConfigDict
from app.api.auth_routes import require_csrf, get_current_owner, get_settings
from app.db import connect
from app.services import batch_flow, plans, execution

router=APIRouter(prefix='/api/v1',tags=['workflow'])

class BatchIn(BaseModel):
    name:str=Field(min_length=1)
    week_start:str
    target_min:int=Field(default=3,ge=0,strict=True)
    target_max:int=Field(default=5,ge=0,strict=True)

@router.post('/batches')
def batch_create(body:BatchIn,request:Request,_=Depends(require_csrf)):
    from datetime import date
    try:
        date.fromisoformat(body.week_start)
        if not body.name.strip(): raise ValueError('批次名称不能为空')
        return {'data':batch_flow.create_batch(get_settings(request).db_path,**body.model_dump())}
    except ValueError as exc: raise HTTPException(422,str(exc))

@router.get('/batches')
def batch_list(request:Request,_=Depends(get_current_owner)):
    conn=connect(get_settings(request).db_path)
    try: ids=[r[0] for r in conn.execute('SELECT id FROM batches ORDER BY created_at DESC')]
    finally: conn.close()
    return {'data':[batch_flow.batch_progress(get_settings(request).db_path,bid) for bid in ids]}

class AssignIn(BaseModel):
    product_ids:list[str]

@router.post('/batches/{batch_id}/products')
def batch_assign(batch_id:str,body:AssignIn,request:Request,_=Depends(require_csrf)):
    try: return {'data':batch_flow.assign_to_batch(get_settings(request).db_path,batch_id,body.product_ids)}
    except KeyError as exc: raise HTTPException(404,str(exc))

@router.get('/products')
def product_list(request:Request,_=Depends(get_current_owner)):
    conn=connect(get_settings(request).db_path)
    try: rows=[dict(r) for r in conn.execute('SELECT id,title,revision,is_synthetic FROM products ORDER BY updated_at DESC')]
    finally: conn.close()
    return {'data':rows}

class PlanIn(BaseModel):
    model_config=ConfigDict(extra='forbid')
    product_id:str
    action:str
    provider:str
    account_id:str|None=None
    payload:dict
    cost:dict|None=None

@router.post('/plans')
def create_plan(body:PlanIn,request:Request,_=Depends(require_csrf)):
    if body.action not in ('IMAGE','VIDEO','LISTING_UPLOAD','VIDEO_PUBLISH','PRICE_CHANGE'):
        raise HTTPException(422,'不支持的动作')
    kinds={'LISTING_UPLOAD':'LISTING','VIDEO_PUBLISH':'VIDEO_PUBLISH','PRICE_CHANGE':'PRICE_CHANGE','IMAGE':'IMAGE','VIDEO':'VIDEO'}
    payload={**body.payload,'kind':kinds[body.action]}
    try:
        cost=plans.CostSpec(**body.cost) if body.cost else None
        if body.action=='LISTING_UPLOAD':
            for key in ('price_minor','stock'):
                if type(payload.get(key)) is not int or payload[key]<0: raise ValueError(key+' 必须为非负整数')
            if not body.account_id or not payload.get('title') or not payload.get('sku_map'): raise ValueError('缺账号、标题或SKU')
        spec=plans.PlanSpec(product_id=body.product_id,action=body.action,provider=body.provider,account_id=body.account_id,requires_content=True,requires_cost=cost is not None,payload=payload,cost=cost)
        return {'data':plans.create_plan(get_settings(request).db_path,spec=spec)}
    except (ValueError,TypeError) as exc: raise HTTPException(422,str(exc))

@router.post('/attempts/{attempt_id}/reconcile')
def reconcile(attempt_id:str,request:Request,session=Depends(require_csrf)):
    try:
        from app.services.ai_checks import ensure_attempt_owner
        ensure_attempt_owner(get_settings(request).db_path,attempt_id,session['owner_id'])
        return {'data':execution.reconcile_attempt(get_settings(request).db_path,attempt_id,request.app.state.registry)}
    except KeyError as exc: raise HTTPException(404,str(exc))

@router.get('/recovery')
def recovery(request:Request,session=Depends(get_current_owner)):
    conn=connect(get_settings(request).db_path)
    try:
        attempts=[dict(r) for r in conn.execute("SELECT a.* FROM external_attempts a JOIN operation_plans p ON p.id=a.plan_id WHERE a.state IN ('PREPARED','SUBMITTED','UNKNOWN','RECONCILING') AND (p.action!='AI_CHECK' OR p.account_id=?)", (session['owner_id'],))]
        state=conn.execute("SELECT value FROM runtime_state WHERE key='restore'").fetchone()
    finally: conn.close()
    return {'data':{'state':state[0] if state else 'READY','attempts':attempts}}

class SelectionIn(BaseModel):
    version_id:str

class ManualReconcileIn(BaseModel):
    evidence_id:str
    succeeded:bool
    actual_amount_minor:int|None=Field(default=None,ge=0,strict=True)
    currency:str='CNY'
    billing_receipt_id:str|None=None

@router.post('/attempts/{attempt_id}/manual-reconcile')
def manual_reconcile(attempt_id:str,body:ManualReconcileIn,request:Request,session=Depends(require_csrf)):
    try: return {'data':execution.manual_reconcile(get_settings(request).db_path,attempt_id,owner_id=session['owner_id'],**body.model_dump())}
    except ValueError as exc: raise HTTPException(422,str(exc))
    except KeyError as exc: raise HTTPException(404,str(exc))

@router.post('/models/select')
def select_model(body:SelectionIn,request:Request,session=Depends(require_csrf)):
    from app.services.model_config import select_strategy
    try: select_strategy(get_settings(request).db_path,body.version_id,session['owner_id'])
    except ValueError as exc: raise HTTPException(422,str(exc))
    return {'data':{'selected':body.version_id}}
