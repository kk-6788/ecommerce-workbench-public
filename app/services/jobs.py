"""SQLite 持久任务队列 + 租约/心跳/取消。

- BEGIN IMMEDIATE 领取（两个 worker 不重复领取同一任务）
- 租约过期：纯计算任务可恢复；外部请求先 reconcile
- 取消：排队可取消；执行中只标取消请求并核对外部状态
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

from app.services.maintenance import maintenance_active
from app.db import connect
from app.domain.evidence import utcnow_iso

LEASE_SECONDS = 600  # 默认租约 10 分钟


def enqueue_job(db_path, *, kind: str, product_id: str | None = None,
                plan_id: str | None = None, input_data: dict | None = None) -> dict:
    """创建 QUEUED job。纯计算任务可无 plan；外部请求带 plan。"""
    input_data = input_data or {}
    conn = connect(db_path)
    try:
        jid = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO jobs(id, product_id, plan_id, kind, state, input_json,"
            " input_hash, progress_json, attempts, created_at, updated_at)"
            " VALUES(?,?,?,?,'QUEUED',?,?, '{}', 0, ?, ?)",
            (jid, product_id, plan_id, kind, json.dumps(input_data, ensure_ascii=False),
             json.dumps(input_data, ensure_ascii=False, sort_keys=True),
             utcnow_iso(), utcnow_iso()),
        )
        conn.commit()
        return get_job(db_path, jid)
    finally:
        conn.close()


def get_job(db_path, job_id: str) -> dict:
    conn = connect(db_path)
    try:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise KeyError("任务不存在")
        out = dict(row)
        out["input"] = json.loads(out.pop("input_json"))
        out["progress"] = json.loads(out.pop("progress_json"))
        out["result"] = json.loads(out["result_json"]) if out.get("result_json") else None
        return out
    finally:
        conn.close()


def claim_job(db_path, *, lease_seconds: int = LEASE_SECONDS, job_id: str | None = None) -> dict | None:
    """BEGIN IMMEDIATE 领取一个 QUEUED job。返回 job 或 None。

    R08：为领取生成唯一 lease token（存入 lease_owner），后续心跳/完成
    必须条件匹配该 token，防止旧 worker 覆盖重新领取后的结果。
    """
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        if maintenance_active(conn) or conn.execute("SELECT 1 FROM runtime_state WHERE key='restore' AND value!='READY'").fetchone():
            return None
        row = conn.execute(
            "SELECT * FROM jobs WHERE state='QUEUED' AND (? IS NULL OR id=?) ORDER BY created_at LIMIT 1", (job_id,job_id)
        ).fetchone()
        if row is None:
            conn.rollback()
            return None
        lease_token = f"w-{uuid.uuid4()}"
        lease_until = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat()
        conn.execute(
            "UPDATE jobs SET state='RUNNING', lease_owner=?, lease_until=?,"
            " heartbeat_at=?, attempts=attempts+1, updated_at=? WHERE id=?",
            (lease_token, lease_until, utcnow_iso(), utcnow_iso(), row["id"]),
        )
        conn.commit()
        job = get_job(db_path, row["id"])
        job["lease_token"] = lease_token
        return job
    finally:
        conn.close()


def heartbeat(db_path, job_id: str, *, lease_token: str | None = None) -> None:
    """心跳续租（R08）：同时延长 lease_until，条件匹配当前租约。

    旧执行者（token 不匹配）不能为已被重新领取的任务续租。
    """
    if not lease_token: raise ValueError("必须提供当前租约令牌")
    conn = connect(db_path)
    try:
        if lease_token:
            conn.execute(
                "UPDATE jobs SET heartbeat_at=?, lease_until=?, updated_at=?"
                " WHERE id=? AND state='RUNNING' AND lease_owner=?",
                (utcnow_iso(),
                 (datetime.now(timezone.utc) + timedelta(seconds=LEASE_SECONDS)).isoformat(),
                 utcnow_iso(), job_id, lease_token),
            )
        else:
            conn.execute(
                "UPDATE jobs SET heartbeat_at=?, updated_at=? WHERE id=? AND state='RUNNING'",
                (utcnow_iso(), utcnow_iso(), job_id),
            )
        conn.commit()
    finally:
        conn.close()


def finish_job(db_path, job_id: str, *, state: str, result: dict | None = None,
               error_code: str | None = None, lease_token: str | None = None) -> dict:
    """结束任务：SUCCEEDED / FAILED / CANCELLED / WAITING_INPUT / WAITING_APPROVAL / RECONCILING。

    R08：带 lease_token 时条件更新，旧 worker 不能覆盖新领取任务的结果。
    """
    if not lease_token: raise ValueError("必须提供当前租约令牌")
    conn = connect(db_path)
    try:
        if lease_token:
            cur = conn.execute(
                "UPDATE jobs SET state=?, result_json=?, error_code=?, updated_at=?"
                " WHERE id=? AND lease_owner=? AND state='RUNNING'",
                (state, json.dumps(result, ensure_ascii=False) if result else None,
                 error_code, utcnow_iso(), job_id, lease_token),
            )
        else:
            cur = conn.execute(
                "UPDATE jobs SET state=?, result_json=?, error_code=?, updated_at=?"
                " WHERE id=?",
                (state, json.dumps(result, ensure_ascii=False) if result else None,
                 error_code, utcnow_iso(), job_id),
            )
        conn.commit()
        return get_job(db_path, job_id)
    finally:
        conn.close()


def transition_job(db_path, job_id: str, new_state: str, *, lease_token=None) -> dict:
    return finish_job(db_path, job_id, state=new_state, lease_token=lease_token)



def cancel_job(db_path, job_id: str) -> dict:
    """排队中直接取消；执行中只标取消请求。"""
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            conn.rollback()
            raise KeyError("任务不存在")
        if row["state"] in ("QUEUED", "WAITING_INPUT", "WAITING_APPROVAL"):
            from app.services.execution import abandon_prepared
            abandon_prepared(conn,job_id,'USER_CANCELLED_BEFORE_SUBMIT')
            conn.execute("UPDATE jobs SET state='CANCELLED', updated_at=? WHERE id=?",
                         (utcnow_iso(), job_id))
            conn.commit()
        elif row["state"] == "RUNNING":
            conn.execute("UPDATE jobs SET cancel_requested=1, updated_at=? WHERE id=?",
                         (utcnow_iso(), job_id))
            conn.commit()
        return get_job(db_path, job_id)
    finally:
        conn.close()


def recover_expired(db_path) -> list[dict]:
    """租约过期恢复：纯计算任务转 QUEUED；有外部 attempt 的标 RECONCILING。"""
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        now = utcnow_iso()
        rows = conn.execute(
            "SELECT * FROM jobs WHERE state='RUNNING' AND lease_until < ?", (now,)
        ).fetchall()
        recovered = []
        for r in rows:
            if r["plan_id"]:
                attempt = conn.execute("SELECT * FROM external_attempts WHERE job_id=?",(r["id"],)).fetchone()
                if attempt and attempt["state"] == "PREPARED":
                    conn.execute("UPDATE jobs SET state='QUEUED',lease_owner=NULL,lease_until=NULL WHERE id=?",(r["id"],))
                    recovered.append({"id":r["id"],"to":"QUEUED","reason":"提交前可恢复"})
                    continue
                conn.execute("UPDATE external_attempts SET state='UNKNOWN' WHERE job_id=? AND state='SUBMITTED'",(r["id"],))
                conn.execute("UPDATE operation_plans SET state='UNKNOWN' WHERE id=?",(r["plan_id"],))
                # 有 plan → 可能已有外部请求，先核对
                conn.execute(
                    "UPDATE jobs SET state='RECONCILING', updated_at=? WHERE id=?",
                    (now, r["id"]))
                recovered.append({"id": r["id"], "to": "RECONCILING",
                                  "reason": "有外部 attempt，先核对"})
            else:
                conn.execute(
                    "UPDATE jobs SET state='QUEUED', lease_owner=NULL,"
                    " lease_until=NULL, updated_at=? WHERE id=?", (now, r["id"]))
                recovered.append({"id": r["id"], "to": "QUEUED", "reason": "纯计算任务可恢复"})
        conn.commit()
        return recovered
    finally:
        conn.close()
