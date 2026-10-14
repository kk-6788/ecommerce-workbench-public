"""指标域：同口径、窗口、渠道；重复修订不叠加；0 分母 null。

- 相同 source_key+content_hash 幂等（不生成新行）
- 同 source_key 新 hash 成为修订，读取仅用最新修订，旧修订仍留证据
- CTR=点击/曝光、CVR=支付订单/点击 仅在同范围同口径时计算；零分母返回 null
- 无法跨平台归因时明确"未归因"；自然流量与付费流量分开
"""
from __future__ import annotations

import json
import uuid
from decimal import Decimal as D

from app.db import connect
from app.domain.evidence import content_hash, utcnow_iso


def _ratio(num, den):
    """比率：分母无效/为 0 返回 None。"""
    if num is None or den is None or den == 0:
        return None
    return (D(num) / D(den)).quantize(D("0.0001"))


def _parse_time_iso(v):
    """解析 ISO8601 时间；要求带时区（R19）。返回 datetime 或 None。"""
    from datetime import datetime
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None  # 无时区拒绝
    return dt


def validate_metrics(payload: dict) -> list[str]:
    """字段校验，返回错误列表。非负整数/有限金额/带时区半开窗口。"""
    errs = []
    for f in ("platform", "account_id", "channel", "source_timezone", "granularity",
              "measure_mode", "period_start", "period_end"):
        if not payload.get(f):
            errs.append(f"{f} 缺失")
    if payload.get("measure_mode") not in ("INTERVAL", "CUMULATIVE"):
        errs.append("measure_mode 无效")
    # R19：计数必须是非负整数（小数/字符串/负值拒绝）
    for f in ("impressions", "clicks", "orders", "add_to_cart", "units_sold"):
        v = payload.get(f)
        if v is None:
            continue
        if type(v) is not int:
            errs.append(f"{f} 必须是整数（收到 {v!r}）")
            continue
        try:
            n = int(v)
        except (TypeError, ValueError):
            errs.append(f"{f} 必须是非负整数（收到 {v!r}）")
            continue
        if n < 0:
            errs.append(f"{f} 不能为负")
    # R19：金额必须有限
    for f in ("revenue", "ad_spend", "contribution_margin"):
        v = payload.get(f)
        if v is None:
            continue
        try:
            d = D(str(v))
            if f in ("revenue","ad_spend") and d.is_finite() and d < 0:
                errs.append(f"{f} 不能为负")
            if not d.is_finite():
                errs.append(f"{f} 必须是有限金额")
        except Exception:
            errs.append(f"{f} 金额格式错误")
    # R19：带时区半开窗口 + 顺序（INTERVAL 下 start < end）
    ps = _parse_time_iso(payload.get("period_start"))
    pe = _parse_time_iso(payload.get("period_end"))
    if payload.get("period_start") and ps is None:
        errs.append("period_start 必须是带时区的 ISO8601 时间")
    if payload.get("period_end") and pe is None:
        errs.append("period_end 必须是带时区的 ISO8601 时间")
    if ps is not None and pe is not None:
        if pe <= ps:
            errs.append("period_end 必须晚于 period_start（半开窗口）")
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try: ZoneInfo(payload.get("source_timezone", ""))
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        # UTC is universally available even without an optional timezone database.
        if payload.get("source_timezone") not in ("UTC", "Asia/Shanghai"): errs.append("source_timezone 必须为有效 IANA 时区")
    if payload.get("granularity") not in ("HOUR", "DAY", "WEEK", "MONTH", "hour", "day", "week", "month", "daily", "TOTAL"):
        errs.append("granularity 无效")
    if payload.get("channel") not in ("organic", "paid", "total", "unknown", "ORGANIC", "PAID", "TOTAL", "UNKNOWN"):
        errs.append("channel 无效")
    return errs


def save_metric_snapshot(db_path, *, product_id: str, listing_id: str | None,
                         source_key: str, metrics: dict, evidence_id: str | None = None,
                         is_synthetic: bool = False) -> dict:
    """保存指标快照。幂等 + 修订。"""
    errs = validate_metrics(metrics)
    if errs:
        raise ValueError("；".join(errs))
    if not source_key.strip(): raise ValueError("source_key 不能为空")
    original_source_key = source_key
    source_key = content_hash({"product": product_id, "listing": listing_id, "source": source_key,
        "platform": metrics.get("platform"), "account": metrics.get("account_id"), "channel": metrics.get("channel"),
        "start": metrics.get("period_start"), "end": metrics.get("period_end"), "mode": metrics.get("measure_mode")})
    metrics = {**metrics, "original_source_key": original_source_key}
    content_hash_val = content_hash({"metrics": metrics, "synthetic": is_synthetic, "evidence": evidence_id})
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        prod = conn.execute("SELECT is_synthetic FROM products WHERE id=?", (product_id,)).fetchone()
        if not prod: raise ValueError("商品不存在")
        if prod["is_synthetic"] and not is_synthetic: raise ValueError("合成商品不能登记真实指标")
        for table, ref in (("evidence", evidence_id), ("listings", listing_id)):
            if ref:
                owner = conn.execute(f"SELECT * FROM {table} WHERE id=?", (ref,)).fetchone()
                if not owner or owner["product_id"] != product_id: raise ValueError(f"{table} 引用不属于商品")
                if table == "listings" and (owner["platform"] != metrics["platform"] or owner["account_id"] != metrics["account_id"]):
                    raise ValueError("上架记录平台/账号不匹配")
                if table == "evidence" and owner["is_synthetic"] and not is_synthetic:
                    raise ValueError("合成证据不能登记真实指标")
        # 幂等：同 source_key + content_hash 不重复
        dup = conn.execute(
            "SELECT id FROM metric_snapshots WHERE source_key=? AND content_hash=?",
            (source_key, content_hash_val)).fetchone()
        if dup:
            return get_snapshot(db_path, dup["id"])
        # 最新修订号
        latest = conn.execute(
            "SELECT COALESCE(MAX(revision),0) r FROM metric_snapshots WHERE source_key=?",
            (source_key,)).fetchone()
        new_rev = latest["r"] + 1
        sid = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO metric_snapshots(id, product_id, listing_id, platform,"
            " account_id, external_content_id, channel, period_start, period_end,"
            " source_timezone, granularity, measure_mode, source_key, content_hash,"
            " revision, metrics_json, evidence_id, is_synthetic, collected_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, product_id, listing_id, metrics.get("platform"),
             metrics.get("account_id"), metrics.get("external_content_id"),
             metrics.get("channel"), metrics.get("period_start"),
             metrics.get("period_end"), metrics.get("source_timezone"),
             metrics.get("granularity"), metrics.get("measure_mode"),
             source_key, content_hash_val, new_rev,
             json.dumps(metrics, ensure_ascii=False), evidence_id,
             1 if is_synthetic else 0, utcnow_iso()),
        )
        conn.commit()
        return get_snapshot(db_path, sid)
    finally:
        conn.close()


def get_snapshot(db_path, snapshot_id: str) -> dict:
    conn = connect(db_path)
    try:
        row = conn.execute("SELECT * FROM metric_snapshots WHERE id=?",
                           (snapshot_id,)).fetchone()
        if not row:
            raise KeyError("快照不存在")
        out = dict(row)
        out["metrics"] = json.loads(out.pop("metrics_json"))
        return out
    finally:
        conn.close()


def derive_rates(metrics: dict) -> dict:
    """派生比率：CTR/CVR。零分母返回 null（不是 0）。"""
    out = dict(metrics)
    ctr = _ratio(metrics.get("clicks"), metrics.get("impressions"))
    cvr = _ratio(metrics.get("orders"), metrics.get("clicks"))
    out["ctr"] = str(ctr) if ctr is not None else None
    out["conversion_rate"] = str(cvr) if cvr is not None else None
    return out


def latest_snapshot(db_path, source_key: str, product_id: str | None = None) -> dict | None:
    """读取最新修订。"""
    conn = connect(db_path)
    try:
        candidates=conn.execute("SELECT DISTINCT source_key FROM metric_snapshots WHERE (source_key=? OR json_extract(metrics_json,'$.original_source_key')=?) AND (? IS NULL OR product_id=?)",(source_key,source_key,product_id,product_id)).fetchall()
        if len(candidates)>1: raise ValueError("来源匹配多个主体/口径，请使用快照 source_key")
        row = conn.execute("SELECT * FROM metric_snapshots WHERE source_key=? ORDER BY revision DESC LIMIT 1", (candidates[0][0] if candidates else source_key,)).fetchone()
        if not row:
            return None
        out = dict(row)
        out["metrics"] = json.loads(out.pop("metrics_json"))
        return out
    finally:
        conn.close()
