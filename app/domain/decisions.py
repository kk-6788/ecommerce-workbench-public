"""决策域：硬门槛、排序、预测快照。结果分离，缺数据不判失败。

- 对每次预测保存商品/SKU事实、数据时间、strategy_version、engine_version、
  outcome_definition_version、规则命中、未知项、证据id、建议决策和预测快照
- 已触发 KILL 永不自动放行；证据不足另标 WAIT
- 初始结果为 INSUFFICIENT_DATA；达到观察窗口且证据足够后才标 VALIDATED/FAILED/MIXED
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from app.domain.evidence import canonical_json, content_hash, utcnow_iso
from app.domain.legacy_rules import TierResult


@dataclass
class PredictionSnapshot:
    """预测快照（不可变）。"""
    candidate_id: str
    strategy_version: str
    engine_version: str
    outcome_definition_version: str
    input_hash: str
    timestamp: str
    kill_filter_result: str          # PASS / KILL / WAIT
    rule_hits: list[str]
    dimension_scores: dict
    weights: dict
    confidence: str                  # evidence_based_label
    final_score: Optional[str]
    predicted_rank: Optional[int]
    recommended_action: str          # TEST / WAIT / KILL
    input_snapshot: dict
    evidence_ids: list[str]
    missing_fields: list[str]


def build_prediction_snapshot(*, candidate_id: str, strategy_version: str,
                              engine_version: str, outcome_definition_version: str,
                              tier: TierResult, evidence_ids: list[str] | None = None,
                              final_score=None, predicted_rank=None,
                              input_snapshot: dict | None = None,
                              kill_filter_result: str = "PASS") -> PredictionSnapshot:
    """从分层结果构建不可变预测快照。"""
    if kill_filter_result == "KILL":
        recommended = "KILL"
    elif kill_filter_result == "WAIT" or tier.block_execution:
        recommended = "WAIT"
    else:
        recommended = "TEST"
    snap = PredictionSnapshot(
        candidate_id=candidate_id,
        strategy_version=strategy_version,
        engine_version=engine_version,
        outcome_definition_version=outcome_definition_version,
        input_hash=content_hash(input_snapshot or {}),
        timestamp=utcnow_iso(),
        kill_filter_result=kill_filter_result,
        rule_hits=tier.rule_hits,
        dimension_scores={
            "layer": tier.layer,
            "cm": str(tier.cm) if tier.cm is not None else None,
            "cm_rate": str(tier.cm_rate) if tier.cm_rate is not None else None,
            "arrival": str(tier.arrival) if tier.arrival is not None else None,
            "procurement_ratio": str(tier.procurement_ratio_val) if tier.procurement_ratio_val is not None else None,
        },
        weights={},
        confidence="PROVISIONAL" if tier.missing_fields else "MEDIUM",
        final_score=str(final_score) if final_score is not None else None,
        predicted_rank=predicted_rank,
        recommended_action=recommended,
        input_snapshot=input_snapshot or {},
        evidence_ids=evidence_ids or [],
        missing_fields=tier.missing_fields,
    )
    return snap


def save_prediction(db_path, *, product_id: str, strategy_version_id: str,
                    engine_version_id: str, outcome_version_id: str,
                    snapshot: PredictionSnapshot, decision: str) -> dict:
    """保存预测（不可变；未来版本可再生成但不覆盖）。"""
    from app.db import connect
    conn = connect(db_path)
    try:
        pid = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO predictions(id, product_id, strategy_version_id,"
            " engine_version_id, outcome_version_id, input_json, input_hash,"
            " result_json, decision, created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (pid, product_id, strategy_version_id, engine_version_id,
             outcome_version_id,
             canonical_json(snapshot.input_snapshot), snapshot.input_hash,
             canonical_json(snapshot.__dict__), decision, utcnow_iso()),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM predictions WHERE id=?", (pid,)).fetchone()
        return dict(row)
    finally:
        conn.close()


def rank_by_tier(results: list[TierResult]) -> list[TierResult]:
    """同层按经济性/证据排序，不发明一组权重。

    执行池优先级：T1 > T1候选 > T2 > T2观察 > T3 > T3候选；
    非执行池（候选/观察/待重抓/暂缓）排在后面。
    """
    exec_priority = {
        "T1": 0, "T1候选": 1, "T2": 2, "T2观察型": 3, "T3": 4, "T3候选": 5,
        "清洁类": 6, "清洁类候选": 7, "组合组件": 8, "组合潜力候选": 9,
    }
    def key(r: TierResult):
        p = exec_priority.get(r.layer, 100)
        cm = r.cm if r.cm is not None else -1
        return (p, -float(cm))
    return sorted(results, key=key)
