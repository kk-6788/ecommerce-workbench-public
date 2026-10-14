"""审批：CONTENT/COST 独立，绑定 plan_hash，只有 owner 可操作。

- owner 批准的是 plan_hash；金额或素材变化新建 plan，旧批准不迁移
- 素材/账号/金额/输入变动需新 plan（hash 不同 → 旧批准无效）
- worker/agent 不能自己批准费用
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.db import connect
from app.domain.evidence import utcnow_iso

APPROVAL_TTL_HOURS = 24 * 7


def approve_plan(db_path, *, plan_id: str, plan_hash: str, owner_id: str,
                 kind: str, decision: str = "APPROVE") -> dict:
    """owner 审批。kind ∈ CONTENT/COST。只有 APPROVE/REJECT 可新增；REVOKE 追加。"""
    if kind not in ("CONTENT", "COST"):
        raise ValueError("kind 必须是 CONTENT 或 COST")
    if decision not in ("APPROVE", "REJECT", "REVOKE"):
        raise ValueError("decision 必须是 APPROVE/REJECT/REVOKE")
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        plan = conn.execute(
            "SELECT * FROM operation_plans WHERE id=? AND plan_hash=?",
            (plan_id, plan_hash)).fetchone()
        if not plan:
            raise KeyError("方案或 hash 不匹配")
        if plan['action'] == 'AI_CHECK' and plan['account_id'] != owner_id:
            raise ValueError('只能审批使用自己密钥的 AI 方案')
        if conn.execute("SELECT 1 FROM external_attempts WHERE plan_id=?", (plan_id,)).fetchone():
            raise ValueError("动作已占用或提交，审批不能撤回外部动作，请先核对")
        if not conn.execute("SELECT 1 FROM owners WHERE id=?", (owner_id,)).fetchone():
            raise ValueError("owner 不存在")
        # 拒绝模型自批：只有 owner 会话可操作（调用方保证 owner_id 来自认证会话）
        if not owner_id:
            raise ValueError("缺少 owner 身份")
        aid = str(uuid.uuid4())
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=APPROVAL_TTL_HOURS)).isoformat()
        conn.execute(
            "INSERT INTO approvals(id, plan_id, plan_hash, owner_id, kind,"
            " decision, created_at, expires_at) VALUES(?,?,?,?,?,?,?,?)",
            (aid, plan_id, plan_hash, owner_id, kind, decision, utcnow_iso(), expires_at),
        )
        # DRAFT/WAITING_APPROVAL → READY 当所需审批齐全
        _refresh_plan_state(conn, plan_id)
        from app.services.audit import log_event
        log_event(conn, actor_type="OWNER", actor_id=owner_id, event_type="APPROVAL_"+decision,
                  entity_type="PLAN", entity_id=plan_id, detail={"kind":kind,"plan_hash":plan_hash})
        conn.commit()
        row = conn.execute("SELECT * FROM approvals WHERE id=?", (aid,)).fetchone()
        return dict(row)
    finally:
        conn.close()


def _refresh_plan_state(conn, plan_id: str) -> None:
    plan = conn.execute("SELECT * FROM operation_plans WHERE id=?", (plan_id,)).fetchone()
    if not plan or plan["state"] not in ("DRAFT", "WAITING_APPROVAL", "READY"):
        return
    if plan["expires_at"] < utcnow_iso():
        conn.execute("UPDATE operation_plans SET state='EXPIRED' WHERE id=?", (plan_id,))
        return
    required = []
    if plan["requires_content"]:
        required.append("CONTENT")
    if plan["requires_cost"]:
        required.append("COST")
    if not required:
        conn.execute("UPDATE operation_plans SET state='READY' WHERE id=?", (plan_id,))
        return
    valid = _valid_approvals(conn, plan_id, plan["plan_hash"], required)
    if valid:
        conn.execute("UPDATE operation_plans SET state='READY' WHERE id=?", (plan_id,))
    else:
        conn.execute("UPDATE operation_plans SET state='WAITING_APPROVAL' WHERE id=?", (plan_id,))


def _valid_approvals(conn, plan_id: str, plan_hash: str, kinds: list[str]) -> bool:
    """检查每个必需 kind 是否存在未过期 APPROVE。REJECT/REVOKE 覆盖。"""
    for kind in kinds:
        rows = conn.execute(
            "SELECT decision, expires_at FROM approvals"
            " WHERE plan_id=? AND plan_hash=? AND kind=? ORDER BY created_at DESC",
            (plan_id, plan_hash, kind)).fetchall()
        if not rows:
            return False
        latest = rows[0]
        if latest["decision"] == "REJECT":
            return False
        if latest["decision"] == "REVOKE":
            return False
        if latest["expires_at"] < utcnow_iso():
            return False
    return True


def valid_approvals(db_path, plan_id: str) -> list[str]:
    """返回当前有效审批 kinds 列表。"""
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        plan = conn.execute("SELECT * FROM operation_plans WHERE id=?", (plan_id,)).fetchone()
        if not plan:
            return []
        required = []
        if plan["requires_content"]:
            required.append("CONTENT")
        if plan["requires_cost"]:
            required.append("COST")
        return [k for k in required
                if _valid_approvals(conn, plan_id, plan["plan_hash"], [k])]
    finally:
        conn.close()


def revoke_approval(db_path, *, plan_id: str, plan_hash: str, owner_id: str,
                    kind: str) -> dict:
    """撤销尚未提交动作的审批。"""
    return approve_plan(db_path, plan_id=plan_id, plan_hash=plan_hash,
                        owner_id=owner_id, kind=kind, decision="REVOKE")
