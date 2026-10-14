"""页面路由：渲染 Jinja2 模板。页面只读数据，写操作走 API（带 CSRF）。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.api.auth_routes import COOKIE_NAME, get_current_owner, get_settings
from app.auth import validate_session
from app.db import connect

router = APIRouter(tags=["pages"])

from pathlib import Path
TEMPLATES_DIR = str(Path(__file__).resolve().parents[1] / "web" / "templates")
templates = Jinja2Templates(directory=TEMPLATES_DIR)


def _nav(request: Request) -> dict:
    return {
        "today": "/", "selection": "/selection", "product": "/product",
        "media": "/media", "launch": "/launch", "review": "/review",
        "active": request.url.path,
    }


def _require_page(request: Request):
    """页面级认证：未登录重定向到 /login。"""
    token = request.cookies.get(COOKIE_NAME)
    settings = get_settings(request)
    if not validate_session(settings.db_path, token or ""):
        return None
    return True


def _common(request: Request) -> dict:
    settings = get_settings(request)
    owner_id = get_current_owner(request)['owner_id']
    conn = connect(settings.db_path)
    try:
        pending_approvals = conn.execute(
            "SELECT p.* FROM operation_plans p"
            " WHERE p.state IN ('DRAFT','WAITING_APPROVAL') AND (p.action!='AI_CHECK' OR p.account_id=?)"
            " ORDER BY p.created_at DESC LIMIT 10", (owner_id,)).fetchall()
        waiting_jobs = conn.execute(
            "SELECT COUNT(*) c FROM jobs j LEFT JOIN operation_plans p ON p.id=j.plan_id WHERE j.state IN"
            " ('QUEUED','RUNNING','WAITING_INPUT','WAITING_APPROVAL','RECONCILING') AND (p.action IS NULL OR p.action!='AI_CHECK' OR p.account_id=?)",
            (owner_id,)).fetchone()["c"]
        evidence_gaps = conn.execute(
            "SELECT COUNT(*) c FROM products WHERE stage='EVIDENCE'").fetchone()["c"]
    finally:
        conn.close()
    return {
        "request": request,
        "nav": _nav(request),
        "pending_approvals": [dict(r) for r in pending_approvals],
        "waiting_jobs": waiting_jobs,
        "evidence_gaps": evidence_gaps,
        "settings": settings,
        "owner_id": owner_id,
    }


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    conn = connect(get_settings(request).db_path)
    try:
        has_accounts = bool(conn.execute("SELECT 1 FROM owners LIMIT 1").fetchone())
    finally:
        conn.close()
    return templates.TemplateResponse(request, "login.html", {"request": request, "has_accounts": has_accounts})


@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    conn = connect(get_settings(request).db_path)
    try:
        has_accounts = bool(conn.execute("SELECT 1 FROM owners LIMIT 1").fetchone())
    finally:
        conn.close()
    signed_in = bool(_require_page(request))
    if has_accounts and not signed_in:
        return RedirectResponse("/login")
    return templates.TemplateResponse(request, "register.html", {
        "request": request, "additional": signed_in,
    })


@router.get("/", response_class=HTMLResponse)
def dashboard_page(request: Request):
    if _require_page(request) is None:
        return RedirectResponse("/login")
    ctx = _common(request)
    settings = get_settings(request)
    from app.services.batch_flow import advance_products
    advance_products(settings.db_path)
    conn = connect(settings.db_path)
    try:
        products = [dict(r) for r in conn.execute(
            "SELECT * FROM products ORDER BY updated_at DESC LIMIT 20").fetchall()]
        batches = [dict(r) for r in conn.execute(
            "SELECT * FROM batches ORDER BY week_start DESC LIMIT 5").fetchall()]
    finally:
        conn.close()
    ctx.update({"products": products, "batches": batches, "page_title": "今日工作"})
    return templates.TemplateResponse(request, "dashboard.html", ctx)


@router.get("/selection", response_class=HTMLResponse)
def selection_page(request: Request):
    if _require_page(request) is None:
        return RedirectResponse("/login")
    ctx = _common(request)
    settings = get_settings(request)
    conn = connect(settings.db_path)
    try:
        opportunities = [dict(r) for r in conn.execute(
            "SELECT * FROM opportunities ORDER BY category_group, keyword LIMIT 100"
        ).fetchall()]
        products = [dict(r) for r in conn.execute(
            "SELECT id, title, category, strategy_mode, stage, revision, updated_at"
            " FROM products ORDER BY updated_at DESC LIMIT 50").fetchall()]
    finally:
        conn.close()
    ctx.update({"opportunities": opportunities, "products": products,
                "page_title": "选品与货源"})
    return templates.TemplateResponse(request, "selection.html", ctx)


@router.get("/product", response_class=HTMLResponse)
def product_page(request: Request):
    if _require_page(request) is None:
        return RedirectResponse("/login")
    ctx = _common(request)
    settings = get_settings(request)
    pid = request.query_params.get("id")
    conn = connect(settings.db_path)
    try:
        if pid:
            row = conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
            if row:
                prod = dict(row)
                prod["facts"] = json.loads(prod["facts_json"])
                prod["offers"] = [dict(r) for r in conn.execute(
                    "SELECT * FROM offers WHERE product_id=?", (pid,)).fetchall()]
                prod["evidence"] = [dict(r) for r in conn.execute(
                    "SELECT * FROM evidence WHERE product_id=?", (pid,)).fetchall()]
                prod["assets"] = [dict(r) for r in conn.execute(
                    "SELECT * FROM assets WHERE product_id=?", (pid,)).fetchall()]
                prod["predictions"] = [dict(r) for r in conn.execute(
                    "SELECT * FROM predictions WHERE product_id=?", (pid,)).fetchall()]
            else:
                prod = None
        else:
            prod = None
        products = [dict(r) for r in conn.execute(
            "SELECT id, title, category, stage FROM products ORDER BY updated_at DESC LIMIT 50"
        ).fetchall()]
    finally:
        conn.close()
    if pid and prod:
        from app.services.products import get_product
        prod = get_product(settings.db_path,pid)
    ctx.update({"products": products, "product": prod, "page_title": "商品档案"})
    return templates.TemplateResponse(request, "product.html", ctx)


@router.get("/media", response_class=HTMLResponse)
def media_page(request: Request):
    if _require_page(request) is None:
        return RedirectResponse("/login")
    ctx = _common(request)
    settings = get_settings(request)
    conn = connect(settings.db_path)
    try:
        assets = [dict(r) for r in conn.execute(
            "SELECT a.*,u.id AS usage_id,u.role AS usage_role,u.review_status AS usage_review_status,p.title AS product_title FROM assets a"
            " JOIN asset_usages u ON u.asset_id=a.id LEFT JOIN products p ON p.id=u.product_id"
            " ORDER BY a.created_at DESC LIMIT 100").fetchall()]
        plans = [dict(r) for r in conn.execute(
            "SELECT p.*, pr.title AS product_title FROM operation_plans p"
            " LEFT JOIN products pr ON pr.id=p.product_id"
            " WHERE (p.payload_json LIKE '%IMAGE%' OR p.payload_json LIKE '%VIDEO%') AND (p.action!='AI_CHECK' OR p.account_id=?)"
            " ORDER BY p.created_at DESC LIMIT 50", (ctx['owner_id'],)).fetchall()]
    finally:
        conn.close()
    for a in assets:
        a.update(role=a["usage_role"],review_status=a["usage_review_status"])
    ctx.update({"assets": assets, "plans": plans, "page_title": "图片与视频"})
    return templates.TemplateResponse(request, "media.html", ctx)


@router.get("/launch", response_class=HTMLResponse)
def launch_page(request: Request):
    if _require_page(request) is None:
        return RedirectResponse("/login")
    ctx = _common(request)
    settings = get_settings(request)
    conn = connect(settings.db_path)
    try:
        listings = [dict(r) for r in conn.execute(
            "SELECT l.*, p.title AS product_title FROM listings l"
            " LEFT JOIN products p ON p.id=l.product_id"
            " ORDER BY l.created_at DESC LIMIT 50").fetchall()]
        launch_plans = [dict(r) for r in conn.execute(
            "SELECT p.*, pr.title AS product_title FROM operation_plans p"
            " LEFT JOIN products pr ON pr.id=p.product_id"
            " WHERE p.action!='AI_CHECK' OR p.account_id=? ORDER BY p.created_at DESC LIMIT 50", (ctx['owner_id'],)).fetchall()]
        attempts = [dict(r) for r in conn.execute(
            "SELECT a.* FROM external_attempts a JOIN operation_plans p ON p.id=a.plan_id WHERE p.action!='AI_CHECK' OR p.account_id=? ORDER BY a.prepared_at DESC LIMIT 50", (ctx['owner_id'],)).fetchall()]
    finally:
        conn.close()
    ctx.update({"listings": listings, "launch_plans": launch_plans,
                "attempts": attempts, "page_title": "上架与启动"})
    return templates.TemplateResponse(request, "launch.html", ctx)


@router.get("/review", response_class=HTMLResponse)
def review_page(request: Request):
    if _require_page(request) is None:
        return RedirectResponse("/login")
    ctx = _common(request)
    settings = get_settings(request)
    conn = connect(settings.db_path)
    try:
        reviews = [dict(r) for r in conn.execute(
            "SELECT r.*, p.title AS product_title FROM reviews r"
            " LEFT JOIN products p ON p.id=r.product_id"
            " ORDER BY r.created_at DESC LIMIT 50").fetchall()]
        for rv in reviews:
            rv["hypotheses"] = json.loads(rv.pop("hypotheses_json") or "[]")
            rv["findings"] = json.loads(rv.pop("findings_json") or "{}")
            rv["metric_ids"] = json.loads(rv.pop("metric_ids_json") or "[]")
        # 只显示每个来源最新修订（R19：避免旧修订混入比较/重复累计）
        snapshots = [dict(r) for r in conn.execute(
            "SELECT * FROM metric_snapshots ms"
            " WHERE revision = (SELECT MAX(revision) FROM metric_snapshots"
            "                    WHERE source_key = ms.source_key)"
            " ORDER BY collected_at DESC LIMIT 50").fetchall()]
        for s in snapshots:
            s["metrics"] = json.loads(s.pop("metrics_json") or "{}")
        proposals = [dict(r) for r in conn.execute(
            "SELECT * FROM calibration_proposals ORDER BY created_at DESC LIMIT 20"
        ).fetchall()]
    finally:
        conn.close()
    ctx.update({"reviews": reviews, "snapshots": snapshots,
                "proposals": proposals, "page_title": "经营复盘"})
    return templates.TemplateResponse(request, "review.html", ctx)
