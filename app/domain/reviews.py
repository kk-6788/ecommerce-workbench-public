"""复盘域：每条复盘必须包含可能因素、支持/反对证据、未知、验证动作与结果。

- 区分经营、执行、数据问题（相关性不等于原因）
- 无证据不宣称因果；同一时间改多个因素时承认无法单独归因
- 没测试的商品不能当作失败样本
- R18：预测/指标引用必须真实且归属当前商品；窗口未满或样本不足时
  只能 INSUFFICIENT_DATA，不得标 VALIDATED/FAILED
"""
from __future__ import annotations

import json
import uuid

from app.db import connect
from app.domain.evidence import utcnow_iso

# R18：证据状态门槛常量
MIN_REAL_METRICS = 1           # 至少 1 条真实（非合成）指标快照
MIN_OBSERVATION_WINDOW_DAYS = 3  # 观察窗下限（日），默认值可被 outcome 配置覆盖


def validate_review(payload: dict) -> list[str]:
    """必填结构校验。"""
    errs = []
    if not payload.get("product_id"):
        errs.append("product_id 缺失")
    if not payload.get("prediction_id"):
        errs.append("prediction_id 缺失")
    if payload.get("outcome") not in ("INSUFFICIENT_DATA", "VALIDATED", "FAILED", "MIXED"):
        errs.append("outcome 无效")
    hypotheses = payload.get("hypotheses") or []
    if not hypotheses:
        errs.append("至少需要一个可能因素假设（不能单一确定结论）")
    else:
        for h in hypotheses:
            if not h.get("statement"):
                errs.append("假设缺少描述")
            if not (h.get("supporting_evidence") or h.get("opposing_evidence")):
                errs.append(f"假设 '{h.get('statement')}' 缺少支持或反对证据")
            if "unknowns" not in h:
                errs.append(f"假设 '{h.get('statement')}' 缺少未知项")
    if not isinstance(payload.get("verification"),dict) or not isinstance(payload["verification"].get("action"),str) or not payload["verification"]["action"].strip():
        errs.append("缺少最小验证动作")
    return errs


def _check_prediction_ownership(conn, product_id: str, prediction_id: str) -> list[str]:
    """R18：预测必须存在且归属当前商品。返回错误列表。"""
    row = conn.execute(
        "SELECT product_id FROM predictions WHERE id=?", (prediction_id,)).fetchone()
    if row is None:
        return [f"prediction_id 不存在：{prediction_id}"]
    if row["product_id"] != product_id:
        return [f"prediction 不属于该商品（跨商品引用被拒绝）"]
    return []


def _check_metric_ownership(conn, product_id: str, metric_ids: list[str]) -> list[str]:
    """R18：指标引用必须存在且归属当前商品。返回错误列表。"""
    errs = []
    for mid in metric_ids:
        row = conn.execute(
            "SELECT product_id, is_synthetic FROM metric_snapshots WHERE id=?",
            (mid,)).fetchone()
        if row is None:
            errs.append(f"metric_id 不存在：{mid}")
        elif row["product_id"] != product_id:
            errs.append(f"metric {mid} 不属于该商品（跨商品引用被拒绝）")
    return errs


def _compute_evidence_status(conn, product_id: str, prediction_id: str,
                             metric_ids: list[str], outcome: str,
                             min_real_metrics: int = MIN_REAL_METRICS,
                             min_window_days: int = MIN_OBSERVATION_WINDOW_DAYS) -> dict:
    """R18：按真实指标数量与观察窗计算证据状态。

    返回 {"status": "INSUFFICIENT_DATA"|"SUFFICIENT", "reasons": [...]}。
    观察窗从 prediction.created_at 到指标 period_end 的最大值计算。
    """
    from datetime import datetime, timezone
    reasons = []
    pred = conn.execute("SELECT * FROM predictions WHERE id=?", (prediction_id,)).fetchone()
    config_row = conn.execute("SELECT content_json FROM model_versions WHERE id=?", (pred["outcome_version_id"],)).fetchone()
    config = json.loads(config_row[0])
    window = max(MIN_OBSERVATION_WINDOW_DAYS, config.get("min_window_days", MIN_OBSERVATION_WINDOW_DAYS))
    minimum = config.get("min_orders", 1)
    intervals, scopes, orders = [], set(), 0
    real = 0
    for mid in set(metric_ids):
        row = conn.execute("SELECT * FROM metric_snapshots WHERE id=?", (mid,)).fetchone()
        latest = conn.execute("SELECT MAX(revision) FROM metric_snapshots WHERE source_key=?", (row["source_key"],)).fetchone()[0]
        if row["revision"] != latest or row["is_synthetic"]:
            reasons.append("指标为旧修订或合成来源")
            continue
        values = json.loads(row["metrics_json"])
        start, end = datetime.fromisoformat(row["period_start"]), datetime.fromisoformat(row["period_end"])
        pred_time = datetime.fromisoformat(pred["created_at"])
        if end > datetime.now(timezone.utc) or start < pred_time or end <= start:
            reasons.append("指标窗口未发生或不属于预测后的观察")
            continue
        if values.get("orders") is None or values.get("impressions") is None:
            reasons.append("缺真实业务计数")
            continue
        if values.get("outcome_version_id") not in (None, pred["outcome_version_id"]):
            reasons.append("结果定义版本不兼容")
            continue
        scopes.add((row["platform"], row["account_id"], row["channel"], row["measure_mode"]))
        intervals.append((start, end))
        orders = max(orders, values["orders"]) if row["measure_mode"] == "CUMULATIVE" else orders + values["orders"]
        real += 1
    if len(scopes) > 1: reasons.append("指标账号/渠道/口径不可比")
    seconds = 0
    tail = None
    for start, end in sorted(intervals):
        if tail is not None and start < tail and any(s[3]=="INTERVAL" for s in scopes): reasons.append("区间指标重复或重叠，不能累计样本")
        if tail is not None and start > tail: reasons.append("观察窗口不连续")
        seconds += max(0, (end - max(start, tail or start)).total_seconds())
        tail = max(end, tail or end)
    if real < max(1, min_real_metrics): reasons.append("真实指标不足")
    if seconds < window * 86400: reasons.append("实际观察窗口未满")
    if orders < minimum: reasons.append("真实订单样本不足")
    return {"status": "INSUFFICIENT_DATA" if reasons else "SUFFICIENT", "reasons": reasons}


def create_review(db_path, *, product_id: str, prediction_id: str, outcome: str,
                  hypotheses: list[dict], findings: dict | None = None,
                  verification: dict | None = None,
                  metric_ids: list[str] | None = None,
                  supersedes_id: str | None = None,
                  min_real_metrics: int = MIN_REAL_METRICS,
                  min_window_days: int = MIN_OBSERVATION_WINDOW_DAYS) -> dict:
    """创建复盘。hypotheses 结构：statement/supporting_evidence/opposing_evidence/
    unknowns/confidence/issue_type。verification：action/control/stop_conditions/cost。

    R18：prediction 与 metric 必须存在且归属同一商品（否则 ValueError→422/409）；
    VALIDATED/FAILED 需要足够真实指标与观察窗，否则强制 INSUFFICIENT_DATA。
    """
    payload = {
        "product_id": product_id, "prediction_id": prediction_id, "outcome": outcome,
        "hypotheses": hypotheses, "findings": findings or {},
        "verification": verification or {},
    }
    errs = validate_review(payload)
    if errs:
        raise ValueError("；".join(errs))
    metric_ids = metric_ids or []

    conn = connect(db_path)
    try:
        errs = _check_prediction_ownership(conn, product_id, prediction_id)
        errs += _check_metric_ownership(conn, product_id, metric_ids)
        if errs:
            raise ValueError("；".join(errs))

        ev = _compute_evidence_status(
            conn, product_id, prediction_id, metric_ids, outcome,
            min_real_metrics=min_real_metrics, min_window_days=min_window_days)
        support_ids=[ref for hypothesis in hypotheses for ref in hypothesis.get("supporting_evidence",[])+hypothesis.get("opposing_evidence",[]) if isinstance(ref,str)]
        valid_ids=set(metric_ids)
        valid_ids.update(row[0] for row in conn.execute("SELECT id FROM evidence WHERE product_id=? AND is_synthetic=0",(product_id,)))
        if not support_ids or any(ref not in valid_ids for ref in support_ids):
            ev["status"]="INSUFFICIENT_DATA"
            ev["reasons"].append("假设证据尚未绑定有效的本商品证据/指标标识")
        # 窗口/样本不足时不得标 VALIDATED/FAILED（R18）
        if ev["status"] == "INSUFFICIENT_DATA" and outcome in ("VALIDATED", "FAILED", "MIXED"):
            outcome = "INSUFFICIENT_DATA"

        rid = str(uuid.uuid4())
        findings_full = {
            "verification": verification or {},
            "findings": findings or {},
            "evidence_status": ev["status"],
            "evidence_reasons": ev["reasons"],
        }
        conn.execute(
            "INSERT INTO reviews(id, product_id, prediction_id, metric_ids_json,"
            " outcome, hypotheses_json, findings_json, supersedes_id, created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (rid, product_id, prediction_id,
             json.dumps(metric_ids, ensure_ascii=False),
             outcome,
             json.dumps(hypotheses, ensure_ascii=False),
             json.dumps(findings_full, ensure_ascii=False),
             supersedes_id, utcnow_iso()),
        )
        conn.commit()
        return get_review(db_path, rid)
    finally:
        conn.close()


def get_review(db_path, review_id: str) -> dict:
    conn = connect(db_path)
    try:
        row = conn.execute("SELECT * FROM reviews WHERE id=?", (review_id,)).fetchone()
        if not row:
            raise KeyError("复盘不存在")
        out = dict(row)
        out["metric_ids"] = json.loads(out.pop("metric_ids_json"))
        out["hypotheses"] = json.loads(out.pop("hypotheses_json"))
        out["findings"] = json.loads(out.pop("findings_json"))
        return out
    finally:
        conn.close()


def classify_issue(outcome: str, hypotheses: list[dict]) -> str:
    """区分经营/执行/数据问题：按假设类型标签。"""
    labels = set()
    for h in hypotheses:
        labels.add(h.get("issue_type", "business"))
    if "execution" in labels:
        return "execution"
    if "data" in labels:
        return "data"
    return "business"
