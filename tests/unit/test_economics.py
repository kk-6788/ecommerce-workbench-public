"""经济性计算测试。黄金用例：34/10.45 → 14.46。"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from app.domain.economics import (
    arrival_cost,
    break_even_cac,
    cash_recovery_days,
    inventory_capital,
    legacy_margin,
    margin_rate,
    procurement_ratio,
    Scenarios,
    OrderEconomics,
)


def test_legacy_margin_preserves_precision():
    assert legacy_margin(price=D("34"), procurement=D("10.45")) == D("14.46")


def test_legacy_margin_exact_zero():
    assert legacy_margin(price=D("20"), procurement=D("12.8")) == D("0.00")


def test_missing_procurement_is_unknown():
    assert legacy_margin(price=D("34"), procurement=None) is None


def test_invalid_price_raises():
    with pytest.raises(ValueError):
        legacy_margin(price=D("0"), procurement=D("10"))
    with pytest.raises(ValueError):
        legacy_margin(price="-5", procurement=D("10"))


def test_min_price_not_substitute_for_conservative():
    # 最低价 2.7 只显示议价空间，不用于替代保守成本 10.45
    conservative = legacy_margin(D("34"), D("10.45"))
    lowest = legacy_margin(D("34"), D("2.7"))
    assert conservative == D("14.46")
    assert lowest > conservative


def test_margin_rate():
    # R14：保留原精度；14.46/34 = 0.425294... 与 0.4253 同量级
    assert margin_rate(D("14.46"), D("34")).quantize(D("0.0001")) == D("0.4253")


def test_procurement_ratio():
    # R14：保留原精度；10.45/34 = 0.307352...
    assert procurement_ratio(D("10.45"), D("34")).quantize(D("0.0001")) == D("0.3074")
    assert procurement_ratio(None, D("34")) is None


def test_arrival_cost():
    assert arrival_cost(D("10.45")) == D("14.95")


def test_order_refund_not_double_counted():
    # 退款本金 + 退货运费 + 商品损失只扣一次
    ec = OrderEconomics(
        revenue=D("34"), refund_amount=D("34"), return_freight=D("6"),
        product_loss=D("15"), procurement=D("10.45"), freight=D("4"),
        packaging=D("0.5"), platform_fee=D("1.87"), ad_spend=D("0"), content_cost=D("0"),
    )
    assert ec.contribution_margin() == D("-37.82")


def test_order_missing_revenue_unknown():
    ec = OrderEconomics(revenue=None, procurement=D("10"))
    assert ec.contribution_margin() is None


def test_break_even_cac():
    assert break_even_cac(D("14.46")) == D("14.46")
    assert break_even_cac(D("-3")) == D("0")
    assert break_even_cac(None) is None


def test_inventory_capital():
    assert inventory_capital(D("10.45"), D("100")) == D("1045.00")
    assert inventory_capital(D("10.45"), None) is None


def test_cash_recovery_days():
    assert cash_recovery_days(D("1045.00"), D("14.46")) == D("72.3")
    assert cash_recovery_days(D("1045.00"), D("0")) is None


def test_scenarios():
    s = Scenarios.from_prices(D("34"), D("2.7"), D("10.45"), D("14.95"))
    assert s.conservative == D("9.96")  # 34*0.865 - (14.95+4+0.5) = 29.41-19.45 = 9.96
    assert s.base == D("14.46")
    assert s.optimistic == D("22.21")
