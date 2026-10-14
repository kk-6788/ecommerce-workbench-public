"""回测/学习/校准测试：precision/recall、0 分母、未测拒品不是 FN。"""
from __future__ import annotations

from app.domain.backtesting import (
    ObservedCase, backtest_model, compare_models, propose_calibration,
)


def _c(cid, decision, status, score=None, margin=None, observed=True):
    return ObservedCase(candidate_id=cid, predicted_decision=decision,
                        predicted_score=score, outcome_status=status,
                        contribution_margin=margin, observed=observed)


def test_backtest_basic_metrics():
    cases = [
        _c("a", "TEST", "VALIDATED", 0.9, "14.5"),
        _c("b", "TEST", "FAILED", 0.8, "-2"),
        _c("c", "WAIT", "VALIDATED", 0.3, "5"),   # FN：预测 WAIT 但实际成功
        _c("d", "KILL", "FAILED", 0.1, None),
    ]
    r = backtest_model(cases)
    assert r["samples"] == 4
    assert r["tp"] == 1
    assert r["fp"] == 1
    assert r["fn"] == 1
    assert r["tn"] == 1
    assert r["precision"] == 0.5
    assert r["recall"] == 0.5


def test_backtest_zero_denominator_null():
    """全预测 KILL → 无正例 → precision/recall null（不是 0）。"""
    cases = [
        _c("a", "KILL", "FAILED"),
        _c("b", "KILL", "FAILED"),
    ]
    r = backtest_model(cases)
    assert r["precision"] is None
    assert r["recall"] is None


def test_unobserved_rejection_is_not_false_negative():
    """未测试的被拒商品不是 FN：observed=False 不参与回测。"""
    cases = [
        _c("tested", "TEST", "VALIDATED", 0.9, "10"),
        _c("never_tested", "KILL", "INSUFFICIENT_DATA", observed=False),
    ]
    r = backtest_model(cases)
    assert r["samples"] == 1
    assert r["fn"] == 0


def test_no_samples_returns_note():
    r = backtest_model([_c("x", "KILL", "INSUFFICIENT_DATA", observed=False)])
    assert r["samples"] == 0
    assert "不可用" in r["note"]


def test_high_score_failures_and_low_score_winners():
    cases = [
        _c("hf", "TEST", "FAILED", 0.9),
        _c("lw", "WAIT", "VALIDATED", 0.2),
        _c("ok", "TEST", "VALIDATED", 0.7),
    ]
    r = backtest_model(cases)
    assert r["high_score_failures"] == ["hf"]
    assert r["low_score_winners"] == ["lw"]


def test_compare_models_common_samples_only():
    old = [_c("a", "TEST", "VALIDATED", 0.9), _c("b", "TEST", "FAILED", 0.8)]
    new = [_c("a", "TEST", "VALIDATED", 0.9), _c("c", "TEST", "VALIDATED", 0.7)]
    r = compare_models(old, new)
    assert r["common_samples"] == 1  # 只有 a
    assert r["old_accuracy"] == 1.0
    assert r["new_accuracy"] == 1.0
    assert r["improved"] is False


def test_propose_calibration_not_auto_activate():
    r = propose_calibration({"samples": 5, "note": "偏差"}, {"version": "v1"})
    assert r["state"] == "PROPOSED"
    assert "owner 确认" in r["risk"]
