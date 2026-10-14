"""关键词意图去重：规范空格、别名、同义词 → intent_cluster_id。

规则（03 §7）：
- 同一渠道/窗口内未知重叠的同意图词不相加；
- 首版用簇内最大观测量作明确标注的保守代理，跨渠道不合并为真实人数；
- 时间窗不可比则返回不可汇总。
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

# 简单同义词/别名映射（可按业务扩充，版本化）。
# 结构：canonical（规范形）→ 同义成员集合。canonical 显式指定，
# 不用 min() 推断（同长度时 Python 按码点取最小会选错，如"犬"先于"狗"）。
SYNONYM_GROUPS: dict[str, set[str]] = {
    "狗": {"狗狗", "狗", "小狗", "犬"},
    "猫": {"猫咪", "猫", "小猫"},
    "衣服": {"衣服", "服装", "衣"},
    "牵引绳": {"牵引绳", "牵引带", "遛狗绳", "狗绳"},
    "湿巾": {"湿巾", "免洗湿巾"},
    "胸背带": {"胸背", "胸背带", "背带"},
    "雨衣": {"雨衣", "防雨衣"},
    "项圈": {"项圈", "脖圈", "颈圈"},
}
SYNONYM_MAPPING_VERSION = "2026-09-19-v1"

# 疑问词（非购买词）识别
QUESTION_WORDS = {"怎么", "如何", "为什么", "可以吗", "好不好", "多少钱"}


def normalize_keyword(kw: str) -> str:
    """规范空格与全半角。"""
    s = kw.strip().lower()
    s = re.sub(r"\s+", " ", s)
    # 全角转半角（基本）
    s = "".join(chr(ord(c) - 0xFEE0) if 0xFF01 <= ord(c) <= 0xFF5E else c for c in s)
    return s


def _synonym_chain(kw: str) -> set[str]:
    """返回该词的完整意图键：同义词组内做规范形替换，再保留完整词形。

    R17：不做 substring 级宠物词归并（"狗狗衣服"与"狗狗牵引绳"不能因都含
    "狗"归为一簇）；同义词（牵引绳/遛狗绳/狗绳）按整词归一为组内规范形。

    替换顺序：全局按成员长度降序（长词优先），避免"狗"先吃掉"遛狗绳"
    导致复合词无法再匹配产品词；canonical 为自身成员时跳过，避免
    "衣"→"衣服" 产生 "衣服服" 这类错误展开。
    """
    nk = normalize_keyword(kw)
    pairs: list[tuple[str, str]] = []
    for canonical, members in SYNONYM_GROUPS.items():
        for member in members:
            if member == canonical:
                continue
            # member 是 canonical 的子串时（如"衣"⊂"衣服"）跳过：
            # 反向替换会把已归一词再展开，破坏结果。
            if member in canonical:
                continue
            pairs.append((member, canonical))
    pairs.sort(key=lambda p: len(p[0]), reverse=True)
    replaced = nk
    for member, canonical in pairs:
        replaced = replaced.replace(member, canonical)
    return {replaced}


def is_question_keyword(kw: str) -> bool:
    nk = normalize_keyword(kw)
    return any(q in nk for q in QUESTION_WORDS)


@dataclass
class KeywordObservation:
    keyword: str
    volume: float
    channel: str          # 渠道（taobao/douyin/xhs/1688...）
    window: str           # 时间窗（如 2026-W37 / 2026-09）
    source_key: str = ""
    intent_cluster_id: str | None = None


def build_intent_clusters(observations: list[KeywordObservation]) -> list[dict]:
    """把观测按渠道+时间窗+同义词簇分组，形成 intent_cluster_id。

    同一渠道/窗口内同意图词不相加：簇量 = 簇内最大观测量（保守代理）。
    跨渠道不合并为真实人数。
    """
    clusters: dict[tuple, dict] = {}

    for obs in observations:
        nk = normalize_keyword(obs.keyword)
        chain = _synonym_chain(nk)
        key = (obs.channel, obs.window, obs.intent_cluster_id or frozenset(chain))
        if key not in clusters:
            clusters[key] = {
                "intent_cluster_id": obs.intent_cluster_id or str(uuid.uuid5(uuid.NAMESPACE_URL, SYNONYM_MAPPING_VERSION + ":" + "|".join(sorted(chain)))),
                "channel": obs.channel,
                "window": obs.window,
                "chain": sorted(chain),
                "keywords": [],
                "max_volume": 0.0,
                "sum_volume_raw": 0.0,
                "aggregation": "cluster_max_conservative",
                "synonym_mapping_version": SYNONYM_MAPPING_VERSION,
                "note": "同渠道同窗口同意图词不相加，用簇内最大观测量作保守代理",
            }
        c = clusters[key]
        c["keywords"].append(obs.keyword)
        c["max_volume"] = max(c["max_volume"], float(obs.volume or 0))
        c["sum_volume_raw"] += float(obs.volume or 0)
        obs.intent_cluster_id = c["intent_cluster_id"]

    # 汇总时同时给 sum（但标注不可作真实人数）
    result = []
    for c in clusters.values():
        result.append({
            **c,
            "sum_volume_raw": round(c["sum_volume_raw"], 2),
            "max_volume": round(c["max_volume"], 2),
        })
    return result


def dedup_demand(observations: list[KeywordObservation]) -> dict:
    """返回保守代理需求：跨渠道合并时只取各渠道簇最大值之和（明确标注代理）。

    R17：跨时间窗不可比——窗口粒度不同（周 vs 月）或窗口标签不同时，
    不输出汇总总量，只返回各窗口分组与"不可汇总"说明。
    """
    clusters = build_intent_clusters(observations)
    # 按窗口分组
    by_window: dict[str, dict[str, float]] = {}
    for c in clusters:
        by_window.setdefault(c["window"], {})
        by_window[c["window"]][c["channel"]] = max(
            by_window[c["window"]].get(c["channel"], 0), c["max_volume"])
    windows = sorted(by_window.keys())
    if len(windows) <= 1:
        # 单一窗口：正常聚合
        by_channel: dict[str, float] = {}
        for c in clusters:
            by_channel[c["channel"]] = max(by_channel.get(c["channel"], 0),
                                           c["max_volume"])
        total = round(sum(by_channel.values()), 2)
        return {
            "deduplicated_demand": total,
            "per_channel_max": by_channel,
            "window": windows[0] if windows else None,
            "is_proxy": True,
            "note": "跨渠道不合并为真实人数，取各渠道簇内最大观测量之和作为保守代理",
        }
    return {
        "deduplicated_demand": None,
        "per_window_channel_max": {w: by_window[w] for w in windows},
        "is_proxy": True,
        "note": f"跨 {len(windows)} 个时间窗（{', '.join(windows)}），粒度不可比，不输出汇总总量",
    }
