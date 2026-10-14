"""关键词意图去重测试：同意图量不相加、跨窗口不汇总。"""
from __future__ import annotations

from app.domain.keyword_demand import (
    KeywordObservation, build_intent_clusters, dedup_demand, is_question_keyword,
    normalize_keyword,
)


def test_same_channel_window_same_intent_not_summed():
    """同一渠道/窗口内同意图词不相加，用簇内最大观测。"""
    obs = [
        KeywordObservation("狗狗衣服", 8000, "douyin", "W37"),
        KeywordObservation("狗衣服", 3000, "douyin", "W37"),
    ]
    clusters = build_intent_clusters(obs)
    assert len(clusters) == 1
    assert clusters[0]["max_volume"] == 8000
    assert clusters[0]["sum_volume_raw"] == 11000  # 保留原始和但标注代理
    assert clusters[0]["aggregation"] == "cluster_max_conservative"


def test_different_channel_not_merged_as_people():
    obs = [
        KeywordObservation("狗狗衣服", 8000, "douyin", "W37"),
        KeywordObservation("狗狗衣服", 5000, "taobao", "W37"),
    ]
    r = dedup_demand(obs)
    assert r["is_proxy"] is True
    assert r["deduplicated_demand"] == 8000 + 5000  # 各渠道最大之和
    assert r["per_channel_max"] == {"douyin": 8000, "taobao": 5000}


def test_different_window_not_summarized_as_same():
    """R17：跨时间窗不可比——build 分簇、dedup 不输出汇总总量。"""
    obs = [
        KeywordObservation("狗狗衣服", 8000, "douyin", "W36"),
        KeywordObservation("狗狗衣服", 9000, "douyin", "W37"),
    ]
    clusters = build_intent_clusters(obs)
    assert len(clusters) == 2  # 不同时间窗不可汇总
    r = dedup_demand(obs)
    assert r["deduplicated_demand"] is None
    assert "不可比" in r["note"]


def test_different_product_intent_not_merged():
    """R17：衣服与牵引绳不同簇（不能因都含"狗"归并）。"""
    obs = [
        KeywordObservation("狗狗衣服", 8000, "taobao", "W37"),
        KeywordObservation("狗狗牵引绳", 7000, "taobao", "W37"),
    ]
    clusters = build_intent_clusters(obs)
    assert len(clusters) == 2


def test_synonym_traction_words_same_cluster():
    """R17：同义牵引词归同簇。"""
    obs = [
        KeywordObservation("牵引绳", 5000, "taobao", "W37"),
        KeywordObservation("遛狗绳", 4000, "taobao", "W37"),
    ]
    clusters = build_intent_clusters(obs)
    assert len(clusters) == 1
    assert clusters[0]["max_volume"] == 5000


def test_question_keyword_detected():
    assert is_question_keyword("猫咪泪痕怎么去除")
    assert not is_question_keyword("狗狗衣服")


def test_normalize_fullwidth_and_space():
    assert normalize_keyword("　狗狗　衣服　") == "狗狗 衣服"
