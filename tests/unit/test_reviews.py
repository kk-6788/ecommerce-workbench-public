"""复盘测试：必填可能因素/支持反对/验证动作；无证据不宣称因果。"""
from __future__ import annotations

import pytest

from app.db import init_db
from app.domain.reviews import (
    classify_issue, create_review, validate_review,
)
from app.services.products import create_product


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "rv.db"
    init_db(p)
    return p


@pytest.fixture
def prod(db):
    return create_product(db, title="复盘商品", category="X", strategy_mode="PUHUO")


@pytest.fixture
def pred(db, prod):
    """构造真实 prediction 供外键引用。"""
    from app.db import connect
    conn = connect(db)
    conn.execute(
        "INSERT INTO model_versions(id, kind, version, content_json, content_hash,"
        " created_at) VALUES('mv-s','STRATEGY','v1','{}','h1',datetime('now'))")
    conn.execute(
        "INSERT INTO model_versions(id, kind, version, content_json, content_hash,"
        " created_at) VALUES('mv-e','ENGINE','v1','{}','h2',datetime('now'))")
    conn.execute(
        "INSERT INTO model_versions(id, kind, version, content_json, content_hash,"
        " created_at) VALUES('mv-o','OUTCOME','v1','{}','h3',datetime('now'))")
    conn.execute(
        "INSERT INTO predictions(id, product_id, strategy_version_id,"
        " engine_version_id, outcome_version_id, input_json, input_hash,"
        " result_json, decision, created_at)"
        " VALUES('pred-1', ?, 'mv-s','mv-e','mv-o','{}','h','{}','TEST',datetime('now'))",
        (prod["id"],))
    conn.commit()
    conn.close()
    return "pred-1"


def _good_payload(prod, **kw):
    return {
        "product_id": prod["id"], "prediction_id": "pred-1", "outcome": "MIXED",
        "verification": {"action": "可比窗口单独换图", "control": "其他因素不变",
                         "stop_conditions": {"max_cost": "10"}, "needs_approval": False},
        "hypotheses": [{
            "statement": "曝光够但点击低：主图表达弱",
            "supporting_evidence": ["同类目高点击主图对比"],
            "opposing_evidence": ["主图近期未换过"],
            "unknowns": ["人群定向是否变化"],
            "confidence": "medium",
        }],
        **kw,
    }


def test_valid_review_accepted(db, prod, pred):
    r = create_review(db, **_good_payload(prod))
    assert r["outcome"] == "INSUFFICIENT_DATA"
    assert len(r["hypotheses"]) == 1


def test_requires_hypotheses(db, prod, pred):
    payload = _good_payload(prod)
    payload["hypotheses"] = []
    assert any("至少需要一个可能因素假设" in e for e in validate_review(payload))


def test_hypothesis_requires_support_or_opposing(db, prod, pred):
    payload = _good_payload(prod)
    payload["hypotheses"] = [{"statement": "没有证据的假设"}]
    errs = validate_review(payload)
    assert any("缺少支持或反对证据" in e for e in errs)


def test_no_evidence_no_causality(db, prod, pred):
    """无控制条件的点击变化只能作为线索，不能宣称换图造成变化。"""
    payload = _good_payload(prod)
    # 验证动作缺失 → 校验失败
    payload.pop("verification")
    assert "缺少最小验证动作" in validate_review(payload)


def test_classify_issue_types(db, prod, pred):
    r1 = create_review(db, **_good_payload(prod))
    assert classify_issue(r1["outcome"], r1["hypotheses"]) == "business"
    payload = _good_payload(prod)
    payload["hypotheses"][0]["issue_type"] = "execution"
    r2 = create_review(db, **payload)
    assert classify_issue(r2["outcome"], r2["hypotheses"]) == "execution"


def test_invalid_outcome_rejected(db, prod, pred):
    payload = _good_payload(prod)
    payload["outcome"] = "SUCCESS"  # 非法值
    assert any("outcome 无效" in e for e in validate_review(payload))
