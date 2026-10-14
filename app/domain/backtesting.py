"""回测与学习：同一批已观察商品、测试前输入和相同结果定义。

- Top-N 成功率、FP/FN、SCALE precision/recall、排序相关、平均贡献利润
- 每项带样本分母、缺失数、窗口；分母 0 返回 null
- 未测试的被拒商品不是假阴性（FN）
- 学习只生成 proposal，owner 确认后新建版本，历史预测不改
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal as D


@dataclass
class ObservedCase:
    """一条已观察样本：测试前决策 + 真实结果。"""
    candidate_id: str
    predicted_decision: str   # TEST / WAIT / KILL（预测时）
    predicted_score: float | None
    outcome_status: str       # VALIDATED / FAILED / MIXED（真实结果）
    final_outcome: str | None = None   # SCALE / KEEP / IMPROVE / KILL / WAIT
    contribution_margin: str | None = None
    observed: bool = True     # False = 未测试/未观察（不当作负例）


def backtest_model(cases: list[ObservedCase], *, top_n: int | None = None) -> dict:
    """描述性回测：只算已观察样本；分母 0 返回 null。"""
    observed = [c for c in cases if c.observed]
    n = len(observed)
    if n == 0:
        return {"samples": 0, "note": "无已观察样本，回测不可用"}

    # 预测为正（TEST）且结果为 VALIDATED = 真阳性
    def is_positive(c: ObservedCase) -> bool:
        return c.predicted_decision == "TEST"

    def is_success(c: ObservedCase) -> bool:
        return c.outcome_status == "VALIDATED"

    def is_failure(c: ObservedCase) -> bool:
        return c.outcome_status == "FAILED"

    # R16：仅计入有结论的样本（VALIDATED/FAILED）；INSUFFICIENT_DATA 不算失败，
    # MIXED 不当作成功也不当作失败（无干净二分类）
    conclusive = [c for c in observed if is_success(c) or is_failure(c)]
    n_conclusive = len(conclusive)
    # 保留分母与缺失说明：observed 总数、可分类数
    insufficient = sum(1 for c in observed if c.outcome_status == "INSUFFICIENT_DATA")

    tp = sum(1 for c in conclusive if is_positive(c) and is_success(c))
    fp = sum(1 for c in conclusive if is_positive(c) and not is_success(c))
    fn = sum(1 for c in conclusive if not is_positive(c) and is_success(c))
    tn = sum(1 for c in conclusive if not is_positive(c) and not is_success(c))

    top_n_success = None
    if top_n and n_conclusive >= top_n:
        ranked = sorted(conclusive, key=lambda c: c.predicted_score if c.predicted_score is not None else -1, reverse=True)
        top = ranked[:top_n]
        top_n_success = sum(1 for c in top if is_success(c)) / len(top)

    precision = tp / (tp + fp) if (tp + fp) > 0 else None
    recall = tp / (tp + fn) if (tp + fn) > 0 else None

    # 排序相关（R16：Kendall 式配对——高分在前且成功、低分在后且失败 = 一致；
    # 同结果并列不计入；常量结果（全成功/全失败）相关不可解释返回 null）
    ranked = sorted(conclusive, key=lambda c: c.predicted_score if c.predicted_score is not None else -1, reverse=True)
    rank_corr = None
    if len(ranked) >= 2 and all(c.predicted_score is not None for c in conclusive):
        concordant = 0
        discordant = 0
        for i in range(len(ranked)):
            for j in range(i + 1, len(ranked)):
                a_succ = is_success(ranked[i])
                b_succ = is_success(ranked[j])
                if ranked[i].predicted_score == ranked[j].predicted_score or a_succ == b_succ:
                    continue  # 并列：结果相同，不贡献一致/不一致
                if a_succ and not b_succ:
                    concordant += 1  # 预测高分=成功、低分=失败 → 一致
                else:
                    discordant += 1
        total = concordant + discordant
        if total > 0:
            rank_corr = (concordant - discordant) / total

    margins = [D(c.contribution_margin) for c in conclusive if c.contribution_margin]
    avg_margin = str(sum(margins) / len(margins)) if margins else None

    return {
        "samples": n,
        "conclusive_samples": n_conclusive,
        "insufficient_excluded": insufficient,
        "top_n": top_n,
        "top_n_success_rate": round(top_n_success, 4) if top_n_success is not None else None,
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": round(precision, 4) if precision is not None else None,
        "recall": round(recall, 4) if recall is not None else None,
        "rank_correlation": round(rank_corr, 4) if rank_corr is not None else None,
        "avg_contribution_margin": avg_margin,
        "high_score_failures": [c.candidate_id for c in conclusive
                                if (c.predicted_score or 0) >= 0.7 and not is_success(c)],
        "low_score_winners": [c.candidate_id for c in conclusive
                              if (c.predicted_score or 0) < 0.4 and is_success(c)],
        "note": "描述性回测；样本量与选择偏差限制见报告；相关不等于因果",
    }


def _prediction_hit(c: ObservedCase) -> bool | None:
    """预测命中：TEST→VALIDATED 命中；WAIT/KILL→FAILED 命中；不足数据不判。"""
    if c.outcome_status not in ("VALIDATED", "FAILED"):
        return None
    if c.predicted_decision == "TEST":
        return c.outcome_status == "VALIDATED"
    return c.outcome_status == "FAILED"


def compare_models(old_cases: list[ObservedCase], new_cases: list[ObservedCase]) -> dict:
    """共同已观察批次、新旧模型版本 → 对照报告，不直接切换正式版。

    R16：accuracy 是"预测命中率"（预测决策 vs 同一结果定义），不是
    outcome_status 成功比例——同一批真实结果上正反预测必须不同。
    """
    old_by_id = {c.candidate_id: c for c in old_cases if c.observed}
    new_by_id = {c.candidate_id: c for c in new_cases if c.observed}
    common = sorted(set(old_by_id) & set(new_by_id))
    # 仅用可判定的共同样本（INSUFFICIENT_DATA 排除）
    decidable = [cid for cid in common
                 if _prediction_hit(old_by_id[cid]) is not None
                 and _prediction_hit(new_by_id[cid]) is not None
                 and old_by_id[cid].outcome_status == new_by_id[cid].outcome_status]
    old_acc = None
    new_acc = None
    if decidable:
        old_acc = sum(1 for cid in decidable if _prediction_hit(old_by_id[cid])) / len(decidable)
        new_acc = sum(1 for cid in decidable if _prediction_hit(new_by_id[cid])) / len(decidable)
    return {
        "common_samples": len(common),
        "decidable_samples": len(decidable),
        "old_accuracy": round(old_acc, 4) if old_acc is not None else None,
        "new_accuracy": round(new_acc, 4) if new_acc is not None else None,
        "improved": (new_acc or 0) > (old_acc or 0) if old_acc is not None else None,
        "missing_old": sorted(set(new_by_id) - set(old_by_id)),
        "missing_new": sorted(set(old_by_id) - set(new_by_id)),
        "note": "仅共同可判定样本可比较；不建议直接升级",
    }


def propose_calibration(report: dict, current_config: dict) -> dict:
    """校准提案：问题、样本、偏差、候选改动、新旧结果和风险。"""
    return {
        "state": "PROPOSED",
        "problem": report.get("note", ""),
        "samples": report.get("samples", 0),
        "proposed_changes": [],
        "risk": "样本不足不自动覆盖正式策略；需 owner 确认后新建版本",
        "current_config_version": current_config.get("version"),
    }
