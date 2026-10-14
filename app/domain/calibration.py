"""校准提案（G03）：回测 → proposal → owner 接受 → 新模型版本。

- 学习只生成 proposal；owner 确认后新建版本，历史预测不改
- 接受提案时创建新 model_versions 行（kind=STRATEGY），不覆盖旧版
- 未测商品不算失败样本；假设不足返回 null
"""
from __future__ import annotations

import json
import uuid

from app.db import connect
from app.domain.evidence import canonical_json, content_hash, utcnow_iso


def save_proposal(db_path, *, model_version_id: str, proposed: dict,
                  comparison: dict, owner_id: str | None = None) -> dict:
    """保存校准提案（PROPOSED）。不自动应用。"""
    conn = connect(db_path)
    try:
        mv = conn.execute(
            "SELECT id FROM model_versions WHERE id=?", (model_version_id,)).fetchone()
        if not mv:
            raise KeyError("model_version_id 不存在")
        pid = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO calibration_proposals(id, model_version_id, proposed_json,"
            " comparison_json, state, accepted_by, created_at)"
            " VALUES(?,?,?,?,'PROPOSED',?,?)",
            (pid, model_version_id, canonical_json(proposed),
             canonical_json(comparison), owner_id, utcnow_iso()))
        conn.commit()
        row = conn.execute(
            "SELECT * FROM calibration_proposals WHERE id=?", (pid,)).fetchone()
        out = dict(row)
        out["proposed"] = json.loads(out.pop("proposed_json"))
        out["comparison"] = json.loads(out.pop("comparison_json"))
        return out
    finally:
        conn.close()


def accept_proposal(db_path, *, proposal_id: str, owner_id: str,
                    new_version: str) -> dict:
    """owner 接受提案：新建 STRATEGY 模型版本（不覆盖旧版），提案标记 ACCEPTED。

    返回新版本记录。
    """
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        if not owner_id:
            owners=conn.execute("SELECT id FROM owners").fetchall()
            if len(owners)!=1: raise ValueError("请创建账号，或用 --owner-id 指定现有 owner")
            owner_id=owners[0]["id"]
        if not conn.execute("SELECT 1 FROM owners WHERE id=?",(owner_id,)).fetchone(): raise ValueError("owner 不存在，请使用真实账号 ID")
        prop = conn.execute(
            "SELECT * FROM calibration_proposals WHERE id=?", (proposal_id,)).fetchone()
        if not prop:
            raise KeyError("提案不存在")
        if prop["state"] != "PROPOSED":
            raise ValueError(f"提案状态不允许接受：{prop['state']}")
        proposed = json.loads(prop["proposed_json"])
        if proposed.get("samples",0)<=0 or not proposed.get("config"):
            raise ValueError("提案缺可运行配置或有效样本，目前只能待观察，不能接受为模型版本")
        from app.services.model_config import validate_strategy
        content = validate_strategy(proposed["config"])
        comparison=json.loads(prop["comparison_json"])
        if comparison.get("new_accuracy") is None: raise ValueError("提案缺新旧对照报告")
        content["version"] = new_version
        content["supersedes"] = prop["model_version_id"]
        chash = content_hash(content)
        vid = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO model_versions(id, kind, version, content_json, content_hash,"
            " supersedes_id, created_at) VALUES(?,?,?,?,?,?,?)",
            (vid, "STRATEGY", new_version, canonical_json(content), chash,
             prop["model_version_id"], utcnow_iso()))
        conn.execute(
            "UPDATE calibration_proposals SET state='ACCEPTED', accepted_by=?,"
            " new_version_id=? WHERE id=?",
            (owner_id, vid, proposal_id))
        from app.services.audit import log_event
        log_event(conn,actor_type="OWNER",actor_id=owner_id,event_type="MODEL_ACCEPTED",entity_type="PROPOSAL",entity_id=proposal_id,detail={"new_version":vid,"content_hash":chash})
        conn.commit()
        row = conn.execute("SELECT * FROM model_versions WHERE id=?", (vid,)).fetchone()
        return dict(row)
    finally:
        conn.close()


def reject_proposal(db_path, *, proposal_id: str, owner_id: str) -> dict:
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        if not owner_id:
            owners=conn.execute("SELECT id FROM owners").fetchall()
            if len(owners)!=1: raise ValueError("请创建账号，或用 --owner-id 指定现有 owner")
            owner_id=owners[0]["id"]
        if not conn.execute("SELECT 1 FROM owners WHERE id=?",(owner_id,)).fetchone(): raise ValueError("owner 不存在，请使用真实账号 ID")
        prop = conn.execute(
            "SELECT * FROM calibration_proposals WHERE id=?", (proposal_id,)).fetchone()
        if not prop:
            raise KeyError("提案不存在")
        if prop["state"] != "PROPOSED":
            raise ValueError(f"提案状态不允许拒绝：{prop['state']}")
        conn.execute(
            "UPDATE calibration_proposals SET state='REJECTED', accepted_by=? WHERE id=?",
            (owner_id, proposal_id))
        from app.services.audit import log_event
        log_event(conn, actor_type="OWNER", actor_id=owner_id,
                  event_type="MODEL_REJECTED", entity_type="PROPOSAL",
                  entity_id=proposal_id, detail={"base_version": prop["model_version_id"]})
        conn.commit()
        row = conn.execute(
            "SELECT * FROM calibration_proposals WHERE id=?", (proposal_id,)).fetchone()
        out = dict(row)
        out["proposed"] = json.loads(out.pop("proposed_json"))
        out["comparison"] = json.loads(out.pop("comparison_json"))
        return out
    finally:
        conn.close()


def list_proposals(db_path) -> list[dict]:
    conn = connect(db_path)
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM calibration_proposals ORDER BY created_at DESC").fetchall()]
        for r in rows:
            r["proposed"] = json.loads(r.pop("proposed_json"))
            r["comparison"] = json.loads(r.pop("comparison_json"))
        return rows
    finally:
        conn.close()
