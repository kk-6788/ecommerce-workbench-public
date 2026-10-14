"""业务 API（G01）：预测、素材上传/审阅、方案查看/审批/撤销/执行、任务、指标、复盘。

所有写操作带 CSRF + 认证；错误统一返回可读的 404/409/422，不落库失败记录。
"""
from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form
from pydantic import BaseModel, Field

from app.api.auth_routes import get_current_owner, get_settings, require_csrf
from app.db import connect
from app.services import launch, media, products as products_svc
from app.services import approvals, execution, jobs, plans
from app.domain import decisions, metrics, reviews
from app.domain.evidence import canonical_json

router = APIRouter(prefix="/api/v1", tags=["business"])

_MAX_ASSET_UPLOAD = 200 * 1024 * 1024


# ============ 预测 ============

def _ensure_model_versions(db_path) -> dict[str, str]:
    """确保存在默认 STRATEGY/ENGINE/OUTCOME 版本，返回 {kind: version_id}。"""
    conn = connect(db_path)
    try:
        out = {}
        for kind in ("STRATEGY", "ENGINE", "OUTCOME"):
            row = conn.execute(
                "SELECT id FROM model_versions WHERE kind=? AND version='v1'",
                (kind,)).fetchone()
            if row:
                out[kind] = row["id"]
            else:
                vid = str(uuid.uuid4())
                content = {"version": "v1", "kind": kind}
                if kind == "STRATEGY": content.update(engine="legacy-v4.2.2",minimum_margin="0",status="PROVISIONAL")
                conn.execute(
                    "INSERT INTO model_versions(id, kind, version, content_json,"
                    " content_hash, created_at) VALUES(?,?,?,?,?,datetime('now'))",
                    (vid, kind, "v1", canonical_json(content),
                     __import__("hashlib").sha256(
                         canonical_json(content).encode("utf-8")).hexdigest()))
                out[kind] = vid
        selected=conn.execute("SELECT value FROM runtime_state WHERE key='selected_strategy'").fetchone()
        if selected: out["STRATEGY"]=selected[0]
        conn.commit()
        return out
    finally:
        conn.close()


class PredictionIn(BaseModel):
    median_price: str | None = None
    conservative_procurement: str | None = None
    top_payments: int | None = None
    taobao_samples: int | None = None
    supply_samples: int | None = None
    category_group: str = "未分组"
    facts: dict = Field(default_factory=dict)
    decision: str = "TEST"


@router.post("/products/{product_id}/predictions")
def create_prediction(product_id: str, body: PredictionIn, request: Request,
                      _=Depends(require_csrf)):
    """基于商品 facts 与分层规则生成预测快照并保存（不可变，不覆盖旧版）。"""
    from decimal import Decimal as D
    from app.domain.legacy_rules import TierInput, grade_tier
    settings = get_settings(request)
    try:
        prod = products_svc.get_product(settings.db_path, product_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="商品不存在")
    from app.domain.inputs import effective_input
    try:
        inp, frozen = effective_input(prod, body.model_dump(exclude_unset=True))
        tier = grade_tier(inp)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    versions = _ensure_model_versions(settings.db_path)
    from app.services.model_config import apply_selected
    tier, strategy = apply_selected(settings.db_path,tier,versions["STRATEGY"])
    frozen["strategy_config_hash"] = strategy["content_hash"]
    snap = decisions.build_prediction_snapshot(
        candidate_id=product_id,
        strategy_version=strategy["version"], engine_version="v1", outcome_definition_version="v1",
        tier=tier, input_snapshot={**frozen, "versions": versions},
        kill_filter_result="KILL" if tier.layer in ("历史排除",) else
        ("WAIT" if tier.block_execution else "PASS"),
    )
    saved = decisions.save_prediction(
        settings.db_path, product_id=product_id,
        strategy_version_id=versions["STRATEGY"],
        engine_version_id=versions["ENGINE"],
        outcome_version_id=versions["OUTCOME"],
        snapshot=snap, decision=snap.recommended_action,
    )
    saved["snapshot"] = json.loads(saved.pop("result_json"))
    return {"data": saved}


@router.get("/products/{product_id}/predictions")
def list_predictions(product_id: str, request: Request,
                     _=Depends(get_current_owner)):
    settings = get_settings(request)
    conn = connect(settings.db_path)
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM predictions WHERE product_id=? ORDER BY created_at DESC",
            (product_id,)).fetchall()]
        for r in rows:
            r["snapshot"] = json.loads(r.pop("result_json"))
            r["input"] = json.loads(r.pop("input_json"))
    finally:
        conn.close()
    return {"data": rows}


# ============ 素材上传 / 审阅 ============

@router.post("/products/{product_id}/assets")
async def upload_asset(product_id: str, request: Request,
                       file: UploadFile = File(...),
                       kind: str = Form("IMAGE"),
                       role: str = Form("RAW"),
                       sku_key: str = Form(None),
                       is_synthetic: int = Form(0),
                       _=Depends(require_csrf)):
    """上传素材文件（multipart）。文件内容经解码校验后入库并关联用途。"""
    settings = get_settings(request)
    try:
        products_svc.get_product(settings.db_path, product_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="商品不存在")
    data = await file.read()
    if len(data) > _MAX_ASSET_UPLOAD:
        raise HTTPException(status_code=422, detail="文件超过 200MB 上限")
    try:
        a = media.store_asset(
            settings.db_path, settings.assets_dir, product_id=product_id,
            kind=kind, role=role, data=data, filename=file.filename or "upload.bin",
            sku_key=sku_key or None, is_synthetic=bool(is_synthetic))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"data": a}


class AssetReviewIn(BaseModel):
    usage_id: str | None = None
    status: str
    note: str | None = None


@router.post("/assets/{asset_id}/review")
def review_asset(asset_id: str, body: AssetReviewIn, request: Request,
                 _=Depends(require_csrf)):
    settings = get_settings(request)
    try:
        a = media.mark_review(settings.db_path, asset_id, body.status, body.note, usage_id=body.usage_id)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"data": a}


# ============ 方案查看 / 审批 / 撤销 / 执行 ============

@router.get("/plans")
def list_plans(request: Request, session=Depends(get_current_owner)):
    settings = get_settings(request)
    conn = connect(settings.db_path)
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM operation_plans WHERE action!='AI_CHECK' OR account_id=? ORDER BY created_at DESC LIMIT 100", (session['owner_id'],)
        ).fetchall()]
        for r in rows:
            r["payload"] = json.loads(r.pop("payload_json"))
    finally:
        conn.close()
    return {"data": rows}


@router.get("/plans/{plan_id}")
def get_plan(plan_id: str, request: Request, session=Depends(get_current_owner)):
    settings = get_settings(request)
    try:
        from app.services.ai_checks import ensure_plan_owner
        ensure_plan_owner(settings.db_path, plan_id, session['owner_id'])
        return {"data": plans.get_plan(settings.db_path, plan_id)}
    except KeyError:
        raise HTTPException(status_code=404, detail="方案不存在")


class ApprovalIn(BaseModel):
    kind: str = "CONTENT"
    decision: str = "APPROVE"


@router.post("/plans/{plan_id}/approve")
def approve_plan(plan_id: str, body: ApprovalIn, request: Request,
                 sess=Depends(require_csrf)):
    """owner 审批：CONTENT/COST 独立；REJECT 也可走此接口。"""
    settings = get_settings(request)
    try:
        plan = plans.get_plan(settings.db_path, plan_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="方案不存在")
    try:
        a = approvals.approve_plan(
            settings.db_path, plan_id=plan_id, plan_hash=plan["plan_hash"],
            owner_id=sess["owner_id"], kind=body.kind, decision=body.decision)
        return {"data": a}
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/plans/{plan_id}/revoke")
def revoke_plan(plan_id: str, body: ApprovalIn, request: Request,
                sess=Depends(require_csrf)):
    settings = get_settings(request)
    try:
        plan = plans.get_plan(settings.db_path, plan_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="方案不存在")
    try:
        a = approvals.revoke_approval(
            settings.db_path, plan_id=plan_id, plan_hash=plan["plan_hash"],
            owner_id=sess["owner_id"], kind=body.kind)
        return {"data": a}
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/plans/{plan_id}/execute")
def execute_plan(plan_id: str, request: Request, session=Depends(require_csrf)):
    """执行已批准方案（幂等；同 plan 只提交一次）。"""
    settings = get_settings(request)
    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="adapter 注册表未挂载")
    try:
        plan = plans.get_plan(settings.db_path, plan_id)
        if plan['action'] == 'AI_CHECK' and plan['account_id'] != session['owner_id']:
            raise HTTPException(403, '只能执行使用自己密钥的 AI 方案')
        attempt = execution.execute_plan(
            settings.db_path, plan_id=plan_id, registry=registry, deferred=True)
        return {"data": attempt}
    except KeyError:
        raise HTTPException(status_code=404, detail="方案不存在")
    except execution.ApprovalRequiredError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


# ============ 任务查询 / 取消 ============

@router.get("/jobs")
def list_jobs(request: Request, session=Depends(get_current_owner)):
    settings = get_settings(request)
    conn = connect(settings.db_path)
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT j.* FROM jobs j LEFT JOIN operation_plans p ON p.id=j.plan_id WHERE p.action IS NULL OR p.action!='AI_CHECK' OR p.account_id=? ORDER BY j.created_at DESC LIMIT 100", (session['owner_id'],)).fetchall()]
        for r in rows:
            r["input"] = json.loads(r.pop("input_json"))
            r["progress"] = json.loads(r.pop("progress_json") or "{}")
            if r.get("result_json"):
                r["result"] = json.loads(r.pop("result_json"))
    finally:
        conn.close()
    return {"data": rows}


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, request: Request, session=Depends(require_csrf)):
    settings = get_settings(request)
    try:
        from app.services.ai_checks import ensure_plan_owner
        job = jobs.get_job(settings.db_path, job_id)
        if job.get('plan_id'):
            ensure_plan_owner(settings.db_path, job['plan_id'], session['owner_id'])
        j = jobs.cancel_job(settings.db_path, job_id)
        return {"data": j}
    except KeyError:
        raise HTTPException(status_code=404, detail="任务不存在")
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


# ============ 指标 ============

class MetricIn(BaseModel):
    listing_id: str | None = None
    source_key: str
    metrics: dict
    evidence_id: str | None = None
    is_synthetic: bool = False


@router.post("/products/{product_id}/metrics")
def save_metrics(product_id: str, body: MetricIn, request: Request,
                 _=Depends(require_csrf)):
    settings = get_settings(request)
    try:
        s = metrics.save_metric_snapshot(
            settings.db_path, product_id=product_id, listing_id=body.listing_id,
            source_key=body.source_key, metrics=body.metrics,
            evidence_id=body.evidence_id, is_synthetic=body.is_synthetic)
        return {"data": s}
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


# ============ 复盘 ============

class ReviewIn(BaseModel):
    prediction_id: str
    outcome: str
    hypotheses: list[dict]
    findings: dict = Field(default_factory=dict)
    verification: dict = Field(default_factory=dict)
    metric_ids: list[str] = Field(default_factory=list)


@router.post("/products/{product_id}/reviews")
def create_review(product_id: str, body: ReviewIn, request: Request,
                  _=Depends(require_csrf)):
    settings = get_settings(request)
    try:
        r = reviews.create_review(
            settings.db_path, product_id=product_id,
            prediction_id=body.prediction_id, outcome=body.outcome,
            hypotheses=body.hypotheses, findings=body.findings,
            verification=body.verification, metric_ids=body.metric_ids)
        return {"data": r}
    except ValueError as e:
        # 归属错误 409，格式错误 422
        if "不存在" in str(e) or "不属于" in str(e) or "跨商品" in str(e):
            raise HTTPException(status_code=409, detail=str(e))
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/products/{product_id}/reviews")
def list_reviews(product_id: str, request: Request, _=Depends(get_current_owner)):
    settings = get_settings(request)
    conn = connect(settings.db_path)
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM reviews WHERE product_id=? ORDER BY created_at DESC",
            (product_id,)).fetchall()]
        for r in rows:
            r["metric_ids"] = json.loads(r.pop("metric_ids_json") or "[]")
            r["hypotheses"] = json.loads(r.pop("hypotheses_json") or "[]")
            r["findings"] = json.loads(r.pop("findings_json") or "{}")
    finally:
        conn.close()
    return {"data": rows}
