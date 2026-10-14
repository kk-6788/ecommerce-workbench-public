"""经济性计算。金额用 Decimal，缺失为 None（不是 0）。"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

D = Decimal

# 历史公式默认费率（旧规则投流前贡献利润近似口径，非已验证当前费率）
DEFAULT_COMMISSION_RATE = D("0.055")   # 平台佣金
DEFAULT_REFUND_PROXY = D("0.08")       # 退款近似
DEFAULT_FREIGHT = D("4")               # 代发运费
DEFAULT_PACKAGING = D("0.5")           # 包材


def _dec(value) -> Decimal | None:
    if value is None:
        return None
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError("金额必须有限")
        return result
    except (InvalidOperation, TypeError) as exc:
        raise ValueError("金额格式错误") from exc


def legacy_margin(price, procurement, *,
                  commission: Decimal = DEFAULT_COMMISSION_RATE,
                  refund_proxy: Decimal = DEFAULT_REFUND_PROXY,
                  freight: Decimal = DEFAULT_FREIGHT,
                  packaging: Decimal = DEFAULT_PACKAGING) -> Decimal | None:
    """旧规则投流前贡献利润：售价×(1−费率) − (拿货+运费+包材)。

    R14：保留原始 Decimal 精度用于规则比较（只在展示层取整）；
    拿货缺失返回 None（未知，不冒充 0）；价格/金额非法抛 ValueError。
    """
    p = _dec(price)
    if p is None or not p.is_finite() or p <= 0:
        raise ValueError("price 必须为正数")
    c = _dec(procurement)
    if c is None:
        return None
    if not c.is_finite() or c < 0:
        raise ValueError("procurement 必须为非负有限数")
    commission, refund_proxy, freight, packaging = map(_dec, (commission, refund_proxy, freight, packaging))
    if any(v is None for v in (commission, refund_proxy, freight, packaging)):
        return None
    if not 0 <= commission <= 1 or not 0 <= refund_proxy <= 1 or commission + refund_proxy > 1 or freight < 0 or packaging < 0:
        raise ValueError("费率或运费包材不合法")
    revenue = p * (D("1") - commission - refund_proxy)
    cost = c + freight + packaging
    return revenue - cost


def margin_rate(margin, price) -> Decimal | None:
    """利润率 = 贡献利润 / 售价。无有效分母返回 None；保留原精度（R14）。"""
    p = _dec(price)
    m = _dec(margin)
    if p is None or p <= 0 or m is None:
        return None
    return m / p


def arrival_cost(procurement, freight: Decimal | None = DEFAULT_FREIGHT,
                 packaging: Decimal | None = DEFAULT_PACKAGING) -> Decimal | None:
    """到单成本 = 拿货 + 运费 + 包材。拿货缺失返回 None；保留原精度（R14）。"""
    values = list(map(_dec, (procurement, freight, packaging)))
    if any(v is not None and v < 0 for v in values):
        raise ValueError("成本不能为负")
    return None if None in values else sum(values)


def procurement_ratio(procurement, price) -> Decimal | None:
    """拿货/售价。无有效分母返回 None；保留原精度（R14）。"""
    c = _dec(procurement)
    p = _dec(price)
    if c is None or p is None or p <= 0:
        return None
    return c / p


@dataclass
class OrderEconomics:
    """单笔订单口径：退款净影响与商品损失分开计量，避免重复扣款。"""
    revenue: Decimal | None
    refund_amount: Decimal | None = None      # 退款本金（已退货款）
    return_freight: Decimal | None = None     # 退货运费
    product_loss: Decimal | None = None       # 退货商品损失（残值回收后）
    procurement: Decimal | None = None
    freight: Decimal | None = None
    packaging: Decimal | None = None
    platform_fee: Decimal | None = None
    ad_spend: Decimal | None = None
    content_cost: Decimal | None = None

    def contribution_margin(self) -> Decimal | None:
        """贡献利润 = 净收入 − 商品成本 − 运费 − 包材 − 平台费 − 广告 − 内容。
        退款净影响 = 退款本金 + 退货运费 + 商品损失，只计一次。

        R14：必需成本（拿货/运费/包材）未知时返回 None——未知 ≠ 0。
        所有未知成本保持未知；未发生费用必须明确填 0。
        保留原精度；展示层再取整。
        """
        values = {name: _dec(getattr(self, name)) for name in self.__dataclass_fields__}
        if any(v is not None and v < 0 for v in values.values()):
            raise ValueError("收入和成本必须为非负有限金额")
        if any(v is None for v in values.values()):
            return None
        return values["revenue"] - sum(v for k, v in values.items() if k != "revenue")



@dataclass
class Scenarios:
    """保守 / 基准 / 乐观三情景。"""
    conservative: Decimal | None
    base: Decimal | None
    optimistic: Decimal | None

    @staticmethod
    def from_prices(retail_price, procurement_low, procurement_mid, procurement_high, *,
                    freight=DEFAULT_FREIGHT, packaging=DEFAULT_PACKAGING) -> "Scenarios":
        """按拿货价低/中/高估算三情景贡献利润（旧口径）。"""
        def m(proc):
            if proc is None:
                return None
            return legacy_margin(retail_price, proc, freight=freight, packaging=packaging)
        return Scenarios(
            conservative=m(procurement_high),
            base=m(procurement_mid),
            optimistic=m(procurement_low),
        )


def break_even_cac(contribution_margin_per_order) -> Decimal | None:
    """盈亏平衡 CAC：每单贡献利润为 0 时的获客成本上限。
    无有效贡献利润返回 None。"""
    m = _dec(contribution_margin_per_order)
    if m is None:
        return None
    if m < 0:
        return D("0")
    return m.quantize(D("0.01"), rounding=ROUND_HALF_UP)


def inventory_capital(cost_per_unit, units) -> Decimal | None:
    """库存占资 = 单位成本 × 库存件数。缺失返回 None。"""
    c = _dec(cost_per_unit)
    u = _dec(units)
    if c is not None and c < 0 or u is not None and (u < 0 or u != u.to_integral_value()):
        raise ValueError("成本必须非负、库存必须非负整数")
    if c is None or u is None:
        return None
    return (c * u).quantize(D("0.01"), rounding=ROUND_HALF_UP)


def cash_recovery_days(inventory_capital_amount, daily_margin) -> Decimal | None:
    """回款周期（天）= 库存占资 / 日贡献利润。分母无效返回 None。"""
    cap = _dec(inventory_capital_amount)
    dm = _dec(daily_margin)
    if cap is None or dm is None or dm <= 0:
        return None
    return (cap / dm).quantize(D("0.1"))
