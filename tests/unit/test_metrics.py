"""指标测试：幂等、修订、零分母 null、对照手算样例。"""
from __future__ import annotations

import pytest

from app.db import init_db
from app.domain.metrics import derive_rates, latest_snapshot, save_metric_snapshot
from app.services.products import create_product


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "met.db"
    init_db(p)
    return p


@pytest.fixture
def prod(db):
    return create_product(db, title="指标商品", category="X", strategy_mode="PUHUO")


def _m(**kw):
    d = dict(platform="taobao", account_id="acc1", channel="organic",
             source_timezone="Asia/Shanghai", granularity="day",
             measure_mode="INTERVAL", period_start="2026-09-01T00:00:00+08:00",
             period_end="2026-09-02T00:00:00+08:00",
             impressions=1000, clicks=50, orders=5)
    d.update(kw)
    return d


def test_save_and_idempotent(db, prod):
    m = _m()
    s1 = save_metric_snapshot(db, product_id=prod["id"], listing_id=None,
                              source_key="tb:item-1:2026-09-01", metrics=m)
    s2 = save_metric_snapshot(db, product_id=prod["id"], listing_id=None,
                              source_key="tb:item-1:2026-09-01", metrics=m)
    assert s1["id"] == s2["id"]


def test_revision_created_for_new_hash(db, prod):
    m1 = _m(orders=5)
    save_metric_snapshot(db, product_id=prod["id"], listing_id=None,
                         source_key="k1", metrics=m1)
    m2 = _m(orders=8)  # 新 hash → 新修订
    s2 = save_metric_snapshot(db, product_id=prod["id"], listing_id=None,
                              source_key="k1", metrics=m2)
    assert s2["revision"] == 2
    latest = latest_snapshot(db, "k1")
    assert latest["metrics"]["orders"] == 8
    # 旧修订仍留证据
    assert latest["revision"] == 2


def test_zero_denominator_null(db, prod):
    m = _m(impressions=0, clicks=0, orders=0)
    rates = derive_rates(m)
    assert rates["ctr"] is None
    assert rates["conversion_rate"] is None


def test_hand_calculated_rates(db, prod):
    m = _m(impressions=1000, clicks=50, orders=5)
    rates = derive_rates(m)
    assert rates["ctr"] == "0.0500"      # 50/1000
    assert rates["conversion_rate"] == "0.1000"  # 5/50


def test_validation_rejects_negative(db, prod):
    with pytest.raises(ValueError):
        save_metric_snapshot(db, product_id=prod["id"], listing_id=None,
                             source_key="bad", metrics=_m(impressions=-1))


def test_validation_requires_platform(db, prod):
    m = _m()
    m.pop("platform")
    with pytest.raises(ValueError):
        save_metric_snapshot(db, product_id=prod["id"], listing_id=None,
                             source_key="bad2", metrics=m)
