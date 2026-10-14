"""评分引擎测试：硬门槛独立、缺变量不补 0、配置版本化。"""
from __future__ import annotations

import pytest

from app.domain.scoring import (
    KillFilter, ScoreConfig, ScoreResult, ScoreVariable, score_candidate,
)


def _var(name, weight=1.0, status="ACTIVE", direction="higher_better", scale=100.0,
         normalization="linear", **kw):
    return ScoreVariable(
        name=name, definition=f"{name}定义", source="test", direction=direction,
        scale=scale, normalization=normalization, weight=weight,
        confidence="medium", refresh_frequency="weekly", is_proxy=False,
        overlap_group=None, rationale=f"{name}依据", status=status, **kw)


def _cfg(weights_authorized=True, status="PROVISIONAL", vars_=None):
    return ScoreConfig(
        version="test-v1", status=status,
        variables=vars_ or [_var("demand"), _var("margin")],
        weights_authorized=weights_authorized)


def test_weighted_score_when_authorized():
    r = score_candidate(_cfg(), {"demand": 50, "margin": 20})
    assert r.final_score is not None
    assert r.variable_scores["demand"] == 0.5


def test_weighted_disabled_without_authorization():
    r = score_candidate(_cfg(weights_authorized=False), {"demand": 50, "margin": 20})
    assert r.final_score is None
    assert r.weighted_enabled is False
    assert any("权重未授权" in n for n in r.notes)


def test_missing_variable_not_zero():
    """R15：缺变量不隐式当 0 也不重分配权重——缺项阻塞总分。"""
    r = score_candidate(_cfg(), {"demand": 50})  # margin 缺失
    assert "margin" in r.missing_variables
    assert r.variable_scores["demand"] == 0.5
    # 缺项阻塞总分（禁止隐式重分配权重：缺 margin 0.9 权重却给 demand 满分）
    assert r.final_score is None
    assert any("禁止隐式重分配权重" in n for n in r.notes)


def test_incomplete_config_no_fake_score():
    v = _var("bad")
    v.definition = ""  # 元数据缺失
    r = score_candidate(_cfg(vars_=[v]), {"bad": 50})
    assert r.final_score is None
    assert any("配置不完整" in n for n in r.notes)


def test_kill_overrides_high_score():
    kf = KillFilter({"亏损": {"field": "margin", "op": "lt", "value": 0}})
    r = score_candidate(_cfg(), {"demand": 100, "margin": -5}, kill_filter=kf)
    assert r.final_score is None
    assert r.kill_hits == ["亏损"]


def test_missing_kill_field_is_wait_not_kill():
    """R15：缺证据与证伪分开——字段缺失不触发 KILL，但缺项也阻塞总分。"""
    kf = KillFilter({"亏损": {"field": "margin", "op": "lt", "value": 0}})
    r = score_candidate(_cfg(), {"demand": 100}, kill_filter=kf)
    assert r.kill_hits == []  # 不判 KILL
    assert r.final_score is None  # margin 缺失阻塞总分（不重分配权重）


def test_boolean_normalization():
    cfg = _cfg(vars_=[_var("has_license", normalization="boolean", scale=1)])
    r = score_candidate(cfg, {"has_license": 1})
    assert r.variable_scores["has_license"] == 1.0
    r2 = score_candidate(cfg, {"has_license": 0})
    assert r2.variable_scores["has_license"] == 0.0


def test_config_versioned():
    c1 = _cfg()
    c2 = _cfg()
    c2.version = "test-v2"
    assert c1.version != c2.version
