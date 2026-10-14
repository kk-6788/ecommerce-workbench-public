"""v4.2.2 勘误3 铺货分层规则引擎。

分层（均含有效证据门槛）：
- T1: CM≥8 且 CM/售价≥0.25 且头部付款≥2000 且供应样本≥3
- T1候选: 同 T1 但供应样本<3
- T2: CM≥3 且 拿货/售价≤0.5 且头部付款≥1000
- T2观察型: CM≥2.5 且 售价×1.1的CM≥3.5 且 拿货/售价≤0.3 且 头部付款≥1000（四项同时）
- T3: CM≥0 且 头部付款≥300
- T3候选: 同 T3 但供应样本<3 或报价待核验
- 清洁类: 直接拿货证据≤2 且 CM≥0 且 头部付款≥50000
- 清洁类候选: 对应需求达标但缺直接拿货证据，或淘宝需求样本为0待补采
- 组合组件: 单卖亏损、付款≥30000、适合组包、实际套装测算CM≥0；禁止独立销售
- 组合潜力候选: 付款≥30000、适合组包、单卖经济性未验证或亏损、套装尚未算完
- 暂缓/换货源: 保守CM<0 或到单成本≥售价

执行顺序：历史排除/观察 → 语义规格有效性 → 价格异常 → 证据缺口 →
专用清洁/组合分支 → 一般 T1/T2/T2观察/T3 → 暂缓。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal as D
from typing import Optional

from app.domain.economics import (
    arrival_cost, legacy_margin, margin_rate, procurement_ratio,
)

# 分层常量
T1 = "T1"
T1_CANDIDATE = "T1候选"
T2 = "T2"
T2_OBSERVE = "T2观察型"
T3 = "T3"
T3_CANDIDATE = "T3候选"
CLEAN = "清洁类"
CLEAN_CANDIDATE = "清洁类候选"
COMBO = "组合组件"
COMBO_CANDIDATE = "组合潜力候选"
DEFERRED = "暂缓/换货源"
UNGRADED = "未定级"
DATA_RECAPTURE = "数据待重抓"
HISTORICAL_EXCLUDE = "历史排除"
HISTORICAL_OBSERVE = "保留观察"

# 历史 4 词数据待重抓（价格一致性存疑）——只作记录展示，不参与正式分层
HISTORICAL_DATA_RECAPTURE = {
    "杀菌湿巾", "狗狗冬季加厚衣服", "帅气边牧衣服", "狗狗衣服小型犬冬装",
}

# 历史 5 词排除表（关键词 → (原因, 规则类型)）
HISTORICAL_EXCLUSIONS = {
    "狗狗专用泪痕湿巾": ("泪痕品类，真需求在口服液食品线不能碰", "伪品类"),
    "狗狗去泪痕神器": ("同上，泪痕系伪品类", "伪品类"),
    "猫咪泪痕怎么去除": ("泪痕系，且是疑问词非购买词", "伪品类"),
    "耳朵清洁湿巾": ("耳道清洁涉医药表述合规风险", "合规风险"),
}
HISTORICAL_OBSERVATIONS = {
    "狗狗四脚衣": ("季节/需求未定，保留观察不评分", "保留观察"),
}

# 需补采的需求词（历史淘宝样本=0 估算）
NEEDS_RECAPTURE_DEMAND = {
    "狗狗免洗澡湿巾", "比熊专用湿巾", "狗狗洗屁股神器",
}


@dataclass
class TierInput:
    """分层输入。缺项为 None（未知 ≠ 0）。"""
    keyword: str
    category_group: str
    median_price: Optional[D] = None          # 实测中位价
    taobao_samples: Optional[int] = None      # 淘宝有效样本
    top_payments: Optional[int] = None        # 头部付款
    conservative_procurement: Optional[D] = None  # 保守拿货价
    lowest_procurement: Optional[D] = None    # 最低拿货价
    supply_samples: Optional[int] = None      # 1688 有效供应样本
    data_confidence: str = "淘宝实抓"
    needs_supply_verify: bool = False         # 待供应链核验
    price_spans: Optional[dict] = None        # 价格异常检查结果
    historical_disposition: Optional[str] = None  # 历史分层记录
    # R11：证据来源类型（FACT=直接证据 / PROXY=代理价 / UNKNOWN=缺失）
    procurement_source: str = "FACT"          # 拿货价来源（FACT/PROXY/UNKNOWN）
    # R13：清洁/组合专用分支字段（由可追溯的商品形态与证据指定，不从标题猜测）
    product_form: str = "GENERAL"             # GENERAL/CLEAN/COMBO
    has_direct_evidence: Optional[bool] = None   # 直接拿货证据是否足够
    combo_margin: Optional[D] = None          # 套装测算贡献利润
    combo_viable: Optional[bool] = None       # 适合组包


@dataclass
class TierResult:
    keyword: str
    layer: str
    cm: Optional[D] = None                    # 贡献利润
    cm_rate: Optional[D] = None               # 利润率
    arrival: Optional[D] = None               # 到单成本
    procurement_ratio_val: Optional[D] = None  # 拿货/售价
    reason: str = ""
    rule_hits: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    block_execution: bool = False             # 是否进入执行池
    needs_evidence: bool = False              # R11：证据不足需补证


def _price_span_check(inp: TierInput) -> dict:
    """内部跨度检查（单词级，无同行基准）。

    历史无最高价时：保守/最低>2 是确定性超门槛证据，但**是否判 DATA_RECAPTURE
    还需同行基准偏差>0.5 同时成立**（见 grade_pool 批次级逻辑）。单词级只标
    供应链核验；不当作"价格已验证通过"。
    """
    if inp.conservative_procurement is None or inp.lowest_procurement is None:
        return {"status": "UNKNOWN", "reason": "缺保守/最低价，无法检查跨度"}
    if inp.conservative_procurement <= 0 or inp.lowest_procurement <= 0:
        return {"status": "UNKNOWN", "reason": "价格非正，无法检查跨度"}
    ratio = inp.conservative_procurement / inp.lowest_procurement
    if ratio > D("1.5"):
        inp.needs_supply_verify = True
    if ratio > D("2"):
        return {"status": "SPAN_OVER", "ratio": str(ratio),
                "reason": f"保守/最低={ratio:.2f}>2，内部跨度超门槛，需同行基准复核"}
    return {"status": "UNKNOWN_SPAN", "ratio": str(ratio),
            "reason": "跨度≤2，无最高价只能标未知，不作为通过证据"}


def _tier_cm(inp: TierInput) -> Optional[D]:
    if inp.median_price is None or inp.conservative_procurement is None:
        return None
    return legacy_margin(inp.median_price, inp.conservative_procurement)


def grade_tier(inp: TierInput) -> TierResult:
    """对单个词执行分层。返回 TierResult。"""
    for name in ("taobao_samples", "top_payments", "supply_samples"):
        value = getattr(inp, name)
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError(f"{name} 必须为非负整数")
    for name in ("median_price", "conservative_procurement", "lowest_procurement", "combo_margin"):
        value = getattr(inp, name)
        if value is not None and (not value.is_finite() or (name != "combo_margin" and value < 0)):
            raise ValueError(f"{name} 必须为有效有限金额")
    kw = inp.keyword
    hits: list[str] = []
    missing: list[str] = []

    # 1. 历史排除 / 观察（最高优先级）
    if kw in HISTORICAL_EXCLUSIONS:
        reason, kind = HISTORICAL_EXCLUSIONS[kw]
        return TierResult(kw, HISTORICAL_EXCLUDE,
                          reason=f"{reason}（{kind}）", rule_hits=["历史排除"],
                          block_execution=True)
    if kw in HISTORICAL_OBSERVATIONS:
        reason, kind = HISTORICAL_OBSERVATIONS[kw]
        return TierResult(kw, HISTORICAL_OBSERVE,
                          reason=f"{reason}（{kind}）", rule_hits=["保留观察"],
                          block_execution=True)

    # 2. 语义规格有效性：淘宝有效样本 = 0 或 None 不能进入任何正式执行层（R11）
    demand_unknown = inp.taobao_samples is None
    demand_zero = inp.taobao_samples is not None and inp.taobao_samples == 0
    if demand_unknown:
        missing.append("taobao_samples")
        hits.append("缺淘宝需求样本")
    if demand_zero:
        hits.append("淘宝需求样本=0")

    # 3. 证据缺口：缺关键字段
    if inp.median_price is None:
        missing.append("median_price")
    if inp.conservative_procurement is None:
        missing.append("conservative_procurement")
    if inp.top_payments is None:
        missing.append("top_payments")

    # 4. 价格异常检查（单词级只做内部跨度标记；DATA_RECAPTURE 需同行基准，在 grade_pool 判定）
    span = _price_span_check(inp)
    if span["status"] == "SPAN_OVER":
        hits.append(f"内部跨度>2倍（{span.get('ratio')}），待同行基准复核")
        inp.needs_supply_verify = True

    # 5. 经济性计算（R14：保留原精度；R11：代理价不产出确定利润）
    cm = None
    if inp.median_price is not None and inp.conservative_procurement is not None:
        if inp.procurement_source != "FACT":
            hits.append("拿货价为代理/参考价，不产出确定利润")
            missing.append("conservative_procurement(direct)")
        else:
            cm = _tier_cm(inp)
    cmr = margin_rate(cm, inp.median_price) if cm is not None and inp.median_price else None
    arrival = arrival_cost(inp.conservative_procurement) if inp.conservative_procurement is not None else None
    pr = procurement_ratio(inp.conservative_procurement, inp.median_price) if inp.median_price else None

    if inp.has_direct_evidence is False:
        return TierResult(kw, CLEAN_CANDIDATE if inp.product_form=="CLEAN" else UNGRADED, reason="直接证据尚未核验",block_execution=True,needs_evidence=True)
    # 证据缺口硬门槛：保守 CM 未知则无法分层 → NEEDS_EVIDENCE（R11）
    if cm is None:
        return TierResult(kw, UNGRADED, cm=cm, cm_rate=cmr, arrival=arrival,
                          procurement_ratio_val=pr,
                          reason="证据不足（缺售价或直接拿货价），不进入任何执行层",
                          rule_hits=["证据缺口"], missing_fields=missing,
                          block_execution=True, needs_evidence=True)

    # 缺淘宝需求样本 → 不能正式执行（R11：与 0 样本同级别阻塞）
    if demand_unknown:
        return TierResult(kw, UNGRADED, cm=cm, cm_rate=cmr, arrival=arrival,
                          procurement_ratio_val=pr,
                          reason="缺淘宝需求样本，不能进入执行层，需补采",
                          rule_hits=["证据缺口", "需求待补采"], missing_fields=missing,
                          block_execution=True, needs_evidence=True)

    # 淘宝有效样本 = 0 → 不能进入任何正式执行层（R11：0 与未知同级别阻塞）
    if demand_zero:
        return TierResult(kw, CLEAN_CANDIDATE if inp.product_form == "CLEAN" else UNGRADED,
                          cm=cm, cm_rate=cmr, arrival=arrival,
                          procurement_ratio_val=pr,
                          reason="淘宝需求样本=0，按'需求有效性门槛'不能进入执行层，需补采",
                          rule_hits=["需求待补采"], missing_fields=missing,
                          block_execution=True, needs_evidence=True)

    # 7. 清洁/组合专用分支（R13：先于一般分层与暂缓；由商品形态字段指定，不从标题猜测）
    if inp.product_form == "CLEAN":
        pays = inp.top_payments or 0
        if inp.has_direct_evidence is not True or inp.needs_supply_verify:
            return TierResult(kw, CLEAN_CANDIDATE, cm=cm, cm_rate=cmr, arrival=arrival,
                              procurement_ratio_val=pr,
                              reason="需求达标但缺直接拿货证据，按规则降为候选",
                              rule_hits=["清洁类候选"], missing_fields=missing,
                              block_execution=True, needs_evidence=True)
        if cm >= D("0") and pays >= 50000 and inp.conservative_procurement <= D("2"):
            return TierResult(kw, CLEAN, cm=cm, cm_rate=cmr, arrival=arrival,
                              procurement_ratio_val=pr,
                              reason="清洁类：直接证据+CM≥0+头部付款≥50000",
                              rule_hits=["清洁类"], missing_fields=missing,
                              block_execution=False)
        return TierResult(kw, CLEAN_CANDIDATE, cm=cm, cm_rate=cmr, arrival=arrival,
                          procurement_ratio_val=pr,
                          reason="清洁类候选：付款不足或经济性未达正式层",
                          rule_hits=["清洁类候选"], missing_fields=missing,
                          block_execution=True)

    if inp.product_form == "COMBO":
        pays = inp.top_payments or 0
        if pays < 30000:
            return TierResult(kw, DEFERRED, cm=cm, cm_rate=cmr, arrival=arrival,
                              procurement_ratio_val=pr,
                              reason="组包形态但头部付款<30000，暂缓",
                              rule_hits=["暂缓"], missing_fields=missing,
                              block_execution=True)
        if inp.combo_viable is False:
            return TierResult(kw, DEFERRED, cm=cm, cm_rate=cmr, arrival=arrival,
                              procurement_ratio_val=pr,
                              reason="不适合组包，暂缓",
                              rule_hits=["暂缓"], missing_fields=missing,
                              block_execution=True)
        # 组合组件：单卖亏损且套装测算 CM≥0
        if inp.combo_viable is True and cm < D("0") and inp.combo_margin is not None and inp.combo_margin >= D("0"):
            return TierResult(kw, COMBO, cm=cm, cm_rate=cmr, arrival=arrival,
                              procurement_ratio_val=pr,
                              reason="组合组件：单卖亏损但套装测算CM≥0；禁止独立销售",
                              rule_hits=["组合组件"], missing_fields=missing,
                              block_execution=True)
        # 组合潜力候选：单卖未验证或亏损、套装尚未算完
        if inp.combo_viable is not True or inp.combo_margin is None or cm < D("0"):
            return TierResult(kw, COMBO_CANDIDATE, cm=cm, cm_rate=cmr, arrival=arrival,
                              procurement_ratio_val=pr,
                              reason="组合潜力候选：套装测算未完成或单卖亏损，禁止独立销售",
                              rule_hits=["组合潜力候选"], missing_fields=missing,
                              block_execution=True, needs_evidence=True)
        # 单卖已盈利且适合组包 → 正常分层
        # fall through 到一般分层

    # 8. 一般分层（R13：顺序 T1 → T2 → T2观察 → T3 → 暂缓）
    pays = inp.top_payments or 0
    supply_n = inp.supply_samples or 0

    # 报价待核验：正式层降级为候选（R11：needs_supply_verify 阻塞正式执行）
    verify_blocked = inp.needs_supply_verify

    # T1
    if cm >= D("8") and cmr is not None and cmr >= D("0.25") and pays >= 2000:
        if supply_n < 3 or verify_blocked:
            return TierResult(kw, T1_CANDIDATE, cm=cm, cm_rate=cmr, arrival=arrival,
                              procurement_ratio_val=pr,
                              reason="T1 达标但供应样本<3 或报价待核验，锁厂实价复核后转正",
                              rule_hits=["T1候选"], missing_fields=missing,
                              block_execution=True, needs_evidence=verify_blocked)
        return TierResult(kw, T1, cm=cm, cm_rate=cmr, arrival=arrival,
                          procurement_ratio_val=pr, reason="T1 利润核心",
                          rule_hits=["T1"], missing_fields=missing,
                          block_execution=False)

    # T2（先于 T2观察型，R13：满足 T2 的数据归 T2 不被观察型截走）
    if cm >= D("3") and pr is not None and pr <= D("0.5") and pays >= 1000:
        if verify_blocked:
            return TierResult(kw, T2_OBSERVE, cm=cm, cm_rate=cmr, arrival=arrival,
                              procurement_ratio_val=pr,
                              reason="T2 达标但报价待核验，暂按观察型处理",
                              rule_hits=["T2观察型"], missing_fields=missing,
                              block_execution=True, needs_evidence=True)
        return TierResult(kw, T2, cm=cm, cm_rate=cmr, arrival=arrival,
                          procurement_ratio_val=pr, reason="T2",
                          rule_hits=["T2"], missing_fields=missing,
                          block_execution=False)

    # T2 观察型（四项同时）
    cm_plus10 = None
    if inp.median_price is not None:
        cm_plus10 = _tier_cm(TierInput(
            keyword=kw, category_group=inp.category_group,
            median_price=inp.median_price * D("1.1"),
            conservative_procurement=inp.conservative_procurement,
            supply_samples=supply_n,
        ))
    if (cm >= D("2.5") and cm_plus10 is not None and cm_plus10 >= D("3.5")
            and pr is not None and pr <= D("0.3") and pays >= 1000):
        return TierResult(kw, T2_OBSERVE, cm=cm, cm_rate=cmr, arrival=arrival,
                          procurement_ratio_val=pr,
                          reason="T2 观察型：四项条件同时满足",
                          rule_hits=["T2观察型"], missing_fields=missing,
                          block_execution=verify_blocked, needs_evidence=verify_blocked)

    # T3 / T3候选
    if cm >= D("0") and pays >= 300:
        if supply_n < 3 or verify_blocked:
            return TierResult(kw, T3_CANDIDATE, cm=cm, cm_rate=cmr, arrival=arrival,
                              procurement_ratio_val=pr,
                              reason="T3 达标但供应样本<3 或报价待核验",
                              rule_hits=["T3候选"], missing_fields=missing,
                              block_execution=True, needs_evidence=verify_blocked)
        return TierResult(kw, T3, cm=cm, cm_rate=cmr, arrival=arrival,
                          procurement_ratio_val=pr, reason="T3",
                          rule_hits=["T3"], missing_fields=missing,
                          block_execution=False)

    # 兜底：暂缓（R13：经济性不足/付款不足统一在此，含 CM<0 与到单成本≥售价）
    if (cm < D("0")) or (arrival is not None and inp.median_price is not None and arrival >= inp.median_price):
        cond = "保守贡献利润<0" if cm < D("0") else "到单成本≥售价"
        return TierResult(kw, DEFERRED, cm=cm, cm_rate=cmr, arrival=arrival,
                          procurement_ratio_val=pr,
                          reason=f"暂缓/换货源：{cond}",
                          rule_hits=["经济性不足"], missing_fields=missing,
                          block_execution=True)
    return TierResult(kw, DEFERRED, cm=cm, cm_rate=cmr, arrival=arrival,
                      procurement_ratio_val=pr,
                      reason="未命中正式分层条件（付款不足或经济性不达标）",
                      rule_hits=["暂缓"], missing_fields=missing,
                      block_execution=True)


def _peer_baseline(words: list[TierInput], exclude_keywords: set[str]) -> dict[str, D]:
    """同品类基准：对每个词取同品类组内、除自身与已异常、供应样本≥3 的
    保守中位成本的中位数。返回 {keyword: baseline}。无同行基准的词不在结果中。"""
    from statistics import median
    by_group: dict[str, list[tuple[str, D]]] = {}
    for w in words:
        if w.keyword in exclude_keywords:
            continue
        if w.conservative_procurement is None or (w.supply_samples or 0) < 3:
            continue
        by_group.setdefault(w.category_group, []).append(
            (w.keyword, w.conservative_procurement))
    baselines: dict[str, D] = {}
    for word in words:
        peers = [c for k, c in by_group.get(word.category_group, []) if k != word.keyword]
        if peers:
            baselines[word.keyword] = D(str(median(sorted(peers))))
    return baselines


def detect_price_anomalies(words: list[TierInput]) -> dict[str, str]:
    """批次级价格异常检测（v4.2.2 勘误3 规则）。

    1. 全评分池的有效供应样本≥2 且字段可算者均检查；先剔除错款错规格。
    2. 同品类基准取"除自身、除已异常、供应样本≥3"的保守中位成本的中位数。
    3. 样本内最高/最低>2 **且** abs(本词中位/同行基准−1)>0.5 → DATA_RECAPTURE。
    4. 历史无最高价时：中位/最低>2 是确定超过跨度门槛的充分证据；≤2 只能标
       跨度未知，不能当成通过。
    5. 每轮同时确定新异常，再统一移出基准池，循环至无新增；避免按行顺序改
       变结果。无同行基准时不编造基准，只做可做的内部检查并标供应链核验。

    返回 {keyword: reason}。异常词由调用方标记 DATA_RECAPTURE 并移出基准池。
    """
    anomalies: dict[str, str] = {}
    pending = [w for w in words
               if w.keyword not in HISTORICAL_EXCLUSIONS
               and w.keyword not in HISTORICAL_OBSERVATIONS
               and w.conservative_procurement is not None
               and w.lowest_procurement is not None
               and (w.supply_samples or 0) >= 2]
    if not pending:
        return {}

    changed = True
    while changed:
        changed = False
        baselines = _peer_baseline(pending, set(anomalies.keys()))
        for w in pending:
            if w.keyword in anomalies:
                continue
            span = _price_span_check(w)
            if span["status"] != "SPAN_OVER":
                continue
            base = baselines.get(w.keyword)
            if base is None:
                # 无同行基准：只做内部检查，标供应链核验，不判异常
                continue
            deviation = abs(w.conservative_procurement / base - D("1"))
            if deviation > D("0.5"):
                anomalies[w.keyword] = (
                    f"跨度{span.get('ratio')}>2 且同行基准偏差{deviation:.1%}>50%"
                )
                changed = True
    return anomalies


def grade_pool(words: list[TierInput]) -> list[TierResult]:
    """批次分层：先做价格异常同步迭代，异常词标 DATA_RECAPTURE 且移出基准池；
    其余词走单词分层。返回与输入同序的 TierResult 列表。"""
    anomalies = detect_price_anomalies(words)
    results: list[TierResult] = []
    for w in words:
        if w.keyword in anomalies:
            results.append(TierResult(
                w.keyword, DATA_RECAPTURE,
                reason=f"价格一致性存疑：{anomalies[w.keyword]}；触发数据有效性检查→待重抓",
                rule_hits=["价格异常", "数据待重抓"],
                block_execution=True,
            ))
        else:
            results.append(grade_tier(w))
    return results
