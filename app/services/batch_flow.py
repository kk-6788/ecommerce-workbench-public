"""批次流转（G02）：批次创建/分配、商品阶段推进、进度汇总。

阶段机：
  DISCOVERY → EVIDENCE → PREDICTED → ASSETS → REVIEW → READY → LAUNCHED

推进规则（后端硬门禁，不因配额放宽）：
- DISCOVERY→EVIDENCE：商品有标题/类目
- EVIDENCE→PREDICTED：至少 1 条该商品证据 + 1 条已核验货源报价
- PREDICTED→ASSETS：存在预测快照
- ASSETS→REVIEW：MAIN/SKU/DETAIL 三角色素材已上传（不必已审）
- REVIEW→READY：三角色素材全部 APPROVED 且启动检查 ready
- READY→LAUNCHED：上架动作已提交（有 listing 或 attempt）

等待原因 / 下一步 / 待审批金额 / 证据更新时间逐商品计算，供今日工作展示。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from app.db import connect
from app.domain.evidence import utcnow_iso

STAGES = ("DISCOVERY", "EVIDENCE", "PREDICTED", "ASSETS", "REVIEW", "READY", "LAUNCHED")
REQUIRED_ASSET_ROLES = ("MAIN", "SKU", "DETAIL")


def create_batch(db_path, *, name: str, week_start: str,
                 target_min: int = 3, target_max: int = 5) -> dict:
    """创建批次。周目标不因实际质量放宽（3–5 件硬约束）。"""
    if target_min < 0 or target_max < target_min:
        raise ValueError("target_min>=0 且 target_max>=target_min")
    conn = connect(db_path)
    try:
        bid = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO batches(id, name, week_start, target_min, target_max, created_at)"
            " VALUES(?,?,?,?,?,?)",
            (bid, name, week_start, int(target_min), int(target_max), utcnow_iso()))
        conn.commit()
        row = conn.execute("SELECT * FROM batches WHERE id=?", (bid,)).fetchone()
        return dict(row)
    finally:
        conn.close()


def assign_to_batch(db_path, batch_id: str, product_ids: list[str]) -> dict:
    """分配商品到批次。返回 {assigned, missing}。"""
    conn = connect(db_path)
    try:
        b = conn.execute("SELECT id FROM batches WHERE id=?", (batch_id,)).fetchone()
        if not b:
            raise KeyError("批次不存在")
        assigned, missing = [], []
        for pid in product_ids:
            row = conn.execute("SELECT id FROM products WHERE id=?", (pid,)).fetchone()
            if not row:
                missing.append(pid)
                continue
            conn.execute("UPDATE products SET batch_id=?, updated_at=? WHERE id=?",
                         (batch_id, utcnow_iso(), pid))
            assigned.append(pid)
        conn.commit()
        return {"assigned": assigned, "missing": missing}
    finally:
        conn.close()


def product_stage(db_path, product_id: str) -> dict:
    """计算单商品当前阶段与阻塞原因/下一步（G02 推进服务核心）。"""
    conn = connect(db_path)
    try:
        prod = conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
        if not prod:
            raise KeyError("商品不存在")
        prod = dict(prod)
        facts = json.loads(prod["facts_json"] or "{}")

        evidence_n = conn.execute(
            "SELECT COUNT(*) c FROM evidence WHERE product_id=?", (product_id,)).fetchone()["c"]
        verified_offers = conn.execute(
            "SELECT COUNT(*) c FROM offers WHERE product_id=? AND verification_status='VERIFIED'"
            " AND procurement_minor IS NOT NULL", (product_id,)).fetchone()["c"]
        pred_n = conn.execute(
            "SELECT COUNT(*) c FROM predictions WHERE product_id=?", (product_id,)).fetchone()["c"]
        asset_rows = conn.execute(
            "SELECT a.id, u.review_status, u.role FROM assets a"
            " JOIN asset_usages u ON u.asset_id=a.id WHERE u.product_id=? AND a.kind='IMAGE'",
            (product_id,)).fetchall()
        roles = {r["role"] for r in asset_rows}
        approved_roles = {r["role"] for r in asset_rows if r["review_status"] == "APPROVED"}
        listing_n = conn.execute(
            "SELECT COUNT(*) c FROM listings WHERE product_id=?", (product_id,)).fetchone()["c"]
        attempt_n = conn.execute(
            "SELECT COUNT(*) c FROM external_attempts a JOIN operation_plans p ON p.id=a.plan_id"
            " WHERE p.product_id=? AND p.action='LISTING_UPLOAD' AND a.state='SUCCEEDED' AND json_extract(a.receipt_json,'$.is_synthetic')=0",
            (product_id,)).fetchone()["c"]
        pending_approval = conn.execute(
            "SELECT COALESCE(SUM(COALESCE(cost_cap_minor,0)),0) c FROM operation_plans"
            " WHERE product_id=? AND state IN ('DRAFT','WAITING_APPROVAL')",
            (product_id,)).fetchone()["c"]
        # 证据更新时间：最近一条证据的 collected_at
        ev_row = conn.execute(
            "SELECT MAX(collected_at) m FROM evidence WHERE product_id=?",
            (product_id,)).fetchone()
        evidence_updated_at = ev_row["m"] if ev_row else None

        prediction = conn.execute("SELECT * FROM predictions WHERE product_id=? ORDER BY created_at DESC LIMIT 1",(product_id,)).fetchone()
        pred_valid = bool(prediction and prediction["decision"]=="TEST" and json.loads(prediction["input_json"]).get("revision")==prod["revision"])
        stage = "DISCOVERY"
        reason = None
        next_step = None

        def _block(msg, step):
            nonlocal reason, next_step
            reason = reason or msg
            next_step = next_step or step

        if stage == "DISCOVERY":
            if not (prod["title"] and prod["category"]):
                _block("缺标题/类目", "补全商品档案")
            else:
                stage = "EVIDENCE"
        if stage == "EVIDENCE":
            if evidence_n < 1:
                _block("缺证据", "添加商品证据")
            elif verified_offers < 1:
                _block("缺已核验货源报价", "添加已核验货源")
            else:
                stage = "PREDICTED"
        if stage == "PREDICTED":
            if not pred_valid:
                _block("缺当前版本可执行预测", "补证据并重新预测")
            else:
                stage = "ASSETS"
        if stage == "ASSETS":
            missing = [r for r in REQUIRED_ASSET_ROLES if r not in roles]
            if missing:
                _block(f"缺素材 {','.join(missing)}", "上传 MAIN/SKU/DETAIL 素材")
            else:
                stage = "REVIEW"
        if stage == "REVIEW":
            missing_appr = [r for r in REQUIRED_ASSET_ROLES if r not in approved_roles]
            if missing_appr:
                _block(f"素材未审完 {','.join(missing_appr)}", "审阅素材")
            else:
                from app.services.launch import launch_readiness
                readiness=launch_readiness(db_path,product_id)
                if readiness['ready']: stage = "READY"
                else: _block('启动条件不足：'+','.join(readiness['gaps']), '补齐启动检查')
        if stage == "READY":
            if attempt_n < 1:
                _block("未启动", "创建上架方案并执行")
            else:
                stage = "LAUNCHED"

        return {
            "product_id": product_id, "title": prod["title"], "stage": stage, "is_synthetic": bool(prod["is_synthetic"]),
            "blocked": reason is not None, "reason": reason, "next_step": next_step,
            "counts": {"evidence": evidence_n, "verified_offers": verified_offers,
                       "predictions": pred_n, "assets": len(asset_rows),
                       "listings": listing_n, "attempts": attempt_n},
            "pending_approval_minor": pending_approval,
            "evidence_updated_at": evidence_updated_at,
            "updated_at": prod["updated_at"],
        }
    finally:
        conn.close()


def advance_products(db_path, batch_id: str | None = None) -> list[dict]:
    """重算批次（或全部）商品阶段；按规则推进并落库。返回进度列表。"""
    conn = connect(db_path)
    try:
        if batch_id:
            rows = conn.execute(
                "SELECT id FROM products WHERE batch_id=?", (batch_id,)).fetchall()
        else:
            rows = conn.execute("SELECT id FROM products").fetchall()
        conn.close()
    finally:
        if 'conn' in locals():
            conn.close()
    results = []
    for r in rows:
        st = product_stage(db_path, r["id"])
        # 落库推进后的阶段
        c = connect(db_path)
        try:
            c.execute("UPDATE products SET stage=?, updated_at=? WHERE id=?",
                      (st["stage"], utcnow_iso(), r["id"]))
            c.commit()
        finally:
            c.close()
        results.append(st)
    return results


def batch_progress(db_path, batch_id: str) -> dict:
    """批次汇总：商品数、各阶段数、达标情况、总待审批金额。"""
    conn = connect(db_path)
    try:
        b = conn.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
        if not b:
            raise KeyError("批次不存在")
        b = dict(b)
    finally:
        conn.close()
    stages = advance_products(db_path, batch_id)
    stage_counts = {}
    for s in stages:
        stage_counts[s["stage"]] = stage_counts.get(s["stage"], 0) + 1
    ready_n = sum(1 for s in stages if s["stage"] in ("READY", "LAUNCHED") and not s["is_synthetic"])
    total_pending = sum(s["pending_approval_minor"] for s in stages)
    return {
        "batch": b,
        "products": stages,
        "stage_counts": stage_counts,
        "ready_count": ready_n,
        "target_min": b["target_min"], "target_max": b["target_max"],
        "target_reached": ready_n >= b["target_min"],
        "pending_approval_minor_total": total_pending,
    }
