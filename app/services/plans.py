"""执行方案（operation_plan）：canonical hash、CONTENT/COST 独立、逐笔金额。

- 一份 plan 对应一次逻辑外部动作/收费请求
- owner 批准的是 plan_hash；金额或素材变化新建 plan，旧批准不迁移
- 素材/账号/金额/输入变动需新 plan
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.domain.evidence import canonical_json, content_hash, utcnow_iso


@dataclass
class CostSpec:
    currency: str = "CNY"
    estimated_amount: str | None = None
    maximum_amount: str | None = None
    credit_usage: str | None = None
    quote_source: str | None = None
    billing_event: str | None = None
    failure_billing: str = "unknown"
    retry_needs_new_approval: bool = True


@dataclass
class PlanSpec:
    product_id: str
    action: str
    provider: str | None = None
    account_id: str | None = None
    requires_content: bool = False
    requires_cost: bool = False
    payload: dict = field(default_factory=dict)
    cost: CostSpec | None = None
    expires_hours: int = 24 * 7


def plan_hash(spec: PlanSpec) -> str:
    """canonical hash：商品、账号、动作、素材hash、报价和费用上限等被批准内容。"""
    obj = {
        "product_id": spec.product_id,
        "action": spec.action,
        "provider": spec.provider,
        "account_id": spec.account_id,
        "requires_content": spec.requires_content,
        "requires_cost": spec.requires_cost,
        "payload": spec.payload,
    }
    if spec.cost:
        obj["cost"] = {
            "currency": spec.cost.currency,
            "estimated_amount": spec.cost.estimated_amount,
            "maximum_amount": spec.cost.maximum_amount,
            "credit_usage": spec.cost.credit_usage,
            "quote_source": spec.cost.quote_source,
            "billing_event": spec.cost.billing_event,
            "failure_billing": spec.cost.failure_billing,
            "retry_needs_new_approval": spec.cost.retry_needs_new_approval,
        }
    return content_hash(obj)


def create_plan(db_path, *, spec: PlanSpec) -> dict:
    """创建 DRAFT plan，返回含 plan_hash 的记录。

    R02：provider 与 account_id 持久化到 operation_plans 列（之前只参与
    hash 未落库，执行时无法从批准方案还原提供方与账号）。
    """
    from app.db import connect
    from app.services.plan_dependencies import dependency_snapshot
    check = connect(db_path)
    try:
        spec.payload = {k:v for k,v in spec.payload.items() if k not in ("_cost", "_dependencies")}
        spec.payload["_dependencies"] = dependency_snapshot(check,spec.product_id,spec.payload)
    finally: check.close()
    ph = plan_hash(spec)
    plan_id = str(uuid.uuid4())
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=spec.expires_hours)).isoformat()
    payload = dict(spec.payload)
    cost = spec.cost
    cost_cap_minor = None
    credit_cap = None
    if cost:
        # R03：费用报价快照嵌入 payload，执行层据此校验失败收费说明并报价
        payload["_cost"] = {
            "currency": cost.currency,
            "estimated_amount": cost.estimated_amount,
            "maximum_amount": cost.maximum_amount,
            "credit_usage": cost.credit_usage,
            "quote_source": cost.quote_source,
            "billing_event": cost.billing_event,
            "failure_billing": cost.failure_billing,
            "retry_needs_new_approval": cost.retry_needs_new_approval,
        }
        if cost.maximum_amount:
            from decimal import Decimal
            amount = Decimal(cost.maximum_amount)
            if not amount.is_finite() or amount < 0 or amount * 100 != (amount * 100).to_integral_value():
                raise ValueError("费用上限必须为非负有限金额且最多两位小数")
            cost_cap_minor = int(amount * 100)
        credit_cap = cost.credit_usage
    payload_json = canonical_json(payload)
    conn = connect(db_path)
    try:
        conn.execute(
            "INSERT INTO operation_plans(id, product_id, action, plan_hash,"
            " payload_json, requires_content, requires_cost, cost_cap_minor,"
            " currency, credit_cap, state, expires_at, created_at, provider, account_id)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,'DRAFT',?,?,?,?)",
            (plan_id, spec.product_id, spec.action, ph, payload_json,
             int(spec.requires_content), int(spec.requires_cost),
             cost_cap_minor, cost.currency if cost else "CNY",
             credit_cap, expires_at, utcnow_iso(),
             spec.provider, spec.account_id),
        )
        conn.commit()
        return get_plan(db_path, plan_id)
    finally:
        conn.close()


def get_plan(db_path, plan_id: str) -> dict:
    from app.db import connect
    conn = connect(db_path)
    try:
        row = conn.execute("SELECT * FROM operation_plans WHERE id=?",
                           (plan_id,)).fetchone()
        if not row:
            raise KeyError("方案不存在")
        out = dict(row)
        out["payload"] = json.loads(out.pop("payload_json"))
        # 关联审批记录
        out["approvals"] = [dict(r) for r in conn.execute(
            "SELECT * FROM approvals WHERE plan_id=? ORDER BY created_at", (plan_id,)
        ).fetchall()]
        out["attempts"] = [dict(r) for r in conn.execute(
            "SELECT * FROM external_attempts WHERE plan_id=?", (plan_id,)).fetchall()]
        return out
    finally:
        conn.close()


def plan_hash_matches(plan: dict, spec: PlanSpec) -> bool:
    return plan["plan_hash"] == plan_hash(spec)
