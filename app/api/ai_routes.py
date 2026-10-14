from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from app.api.auth_routes import get_current_owner, get_settings, require_csrf
from app.api.pages_routes import _require_page, _common, templates
from app.services import ai_checks, execution, approvals
from app.db import connect

router = APIRouter(tags=['ai-checks'])


def call(fn, *args, **kwargs):
    try:
        return {'data': fn(*args, **kwargs)}
    except ai_checks.Conflict as exc:
        raise HTTPException(409, str(exc)) from None
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


class CheckIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    product_id: str
    use_case: Literal['evidence_support', 'keyword_intent']
    expected_revision: int = Field(gt=0, strict=True)
    claim: str = Field(default='', max_length=2000)
    sku: str = Field(default='', max_length=200)
    evidence_ids: list[str] = Field(default_factory=list, max_length=10)
    keyword: str = Field(default='', max_length=500)
    maximum_amount: str = Field(default='0', max_length=20)
    quote_source: str = Field(default='', max_length=1000)
    failure_billing: str = Field(default='unknown', max_length=1000)


class ReviewIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['HUMAN_PASSED', 'HUMAN_REJECTED', 'REQUEST_EVIDENCE', 'REVOKE']
    reason: str = Field(min_length=1, max_length=2000)
    expected_revision: int = Field(gt=0, strict=True)
    snapshot_hash: str = Field(min_length=64, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=100)


@router.get('/ai-review')
def page(request: Request):
    if _require_page(request) is None:
        return RedirectResponse('/login')
    return templates.TemplateResponse(request, 'ai_review.html', {**_common(request), 'page_title': 'AI 辅助审核'})


@router.get('/api/v1/ai-policy')
def policy(_=Depends(get_current_owner)):
    return {'data': ai_checks.policy_status()}


@router.post('/api/v1/ai-check-plans', status_code=201)
def create(body: CheckIn, request: Request, session=Depends(require_csrf)):
    return call(ai_checks.create, get_settings(request).db_path, session['owner_id'], **body.model_dump())


@router.get('/api/v1/ai-checks')
def list_checks(request: Request, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), session=Depends(get_current_owner)):
    return call(ai_checks.list_runs, get_settings(request).db_path, session['owner_id'], limit, offset)


@router.get('/api/v1/ai-checks/{run_id}')
def check(run_id: str, request: Request, session=Depends(get_current_owner)):
    return call(ai_checks.get, get_settings(request).db_path, session['owner_id'], run_id)


@router.post('/api/v1/ai-checks/{run_id}/review')
def review(run_id: str, body: ReviewIn, request: Request, session=Depends(require_csrf)):
    return call(ai_checks.review, get_settings(request).db_path, session['owner_id'], run_id, **body.model_dump())


@router.post('/api/v1/ai-checks/{run_id}/recheck-plan', status_code=201)
def recheck(run_id: str, body: CheckIn, request: Request, session=Depends(require_csrf)):
    return call(ai_checks.create, get_settings(request).db_path, session['owner_id'], parent=run_id, **body.model_dump())


@router.post('/api/v1/ai-checks/{run_id}/execute')
def execute(run_id: str, request: Request, session=Depends(require_csrf)):
    def submit():
        settings = get_settings(request)
        row = ai_checks.get(settings.db_path, session['owner_id'], run_id)
        return execution.execute_plan(settings.db_path, plan_id=row['plan_id'], registry=request.app.state.registry, deferred=True)
    return call(submit)


class ApprovalIn(BaseModel):
    kind: Literal['CONTENT', 'COST']
    plan_hash: str = Field(min_length=64, max_length=64)
    decision: Literal['APPROVE', 'REJECT', 'REVOKE'] = 'APPROVE'


@router.post('/api/v1/ai-checks/{run_id}/approve')
def approve(run_id: str, body: ApprovalIn, request: Request, session=Depends(require_csrf)):
    def save():
        settings = get_settings(request)
        row = ai_checks.get(settings.db_path, session['owner_id'], run_id)
        if row['state'] != 'PLANNED':
            raise ValueError('仅资料完整且未失效的待执行审核方案可审批')
        return approvals.approve_plan(settings.db_path, plan_id=row['plan_id'], plan_hash=body.plan_hash, owner_id=session['owner_id'], kind=body.kind, decision=body.decision)
    return call(save)


@router.get('/api/v1/ai-notifications')
def notifications(request: Request, session=Depends(get_current_owner)):
    return call(ai_checks.notifications, get_settings(request).db_path, session['owner_id'])


@router.post('/api/v1/ai-notifications/{notification_id}/read')
def read(notification_id: str, request: Request, session=Depends(require_csrf)):
    return call(ai_checks.notifications, get_settings(request).db_path, session['owner_id'], notification_id)


@router.get('/api/v1/ai-sources/{product_id}')
def sources(product_id: str, request: Request, _=Depends(get_current_owner)):
    conn = connect(get_settings(request).db_path)
    try:
        return {'data': [dict(r) for r in conn.execute('SELECT e.id,e.source_key,e.evidence_kind,e.observed_at FROM evidence e WHERE e.product_id=? AND e.id=(SELECT x.id FROM evidence x WHERE x.product_id=e.product_id AND x.source_key=e.source_key ORDER BY x.collected_at DESC,x.id DESC LIMIT 1) ORDER BY e.collected_at DESC', (product_id,))]}
    finally:
        conn.close()
