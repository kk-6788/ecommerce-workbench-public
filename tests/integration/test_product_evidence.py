"""商品与证据服务测试。"""
from __future__ import annotations

import pytest

from app.db import init_db
from app.services.products import (
    ConflictError, add_evidence, add_offer, create_product, get_product,
    update_product,
)


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "p.db"
    init_db(p)
    return p


def test_create_and_get_product(db):
    p = create_product(db, title="狗狗牵引绳", category="牵引绳", strategy_mode="PUHUO",
                       facts={"material": "尼龙"})
    got = get_product(db, p["id"])
    assert got["title"] == "狗狗牵引绳"
    assert got["revision"] == 1
    assert got["stage"] == "DISCOVERY"


def test_update_revision_conflict(db):
    p = create_product(db, title="A", category="X", strategy_mode="PUHUO")
    update_product(db, p["id"], revision=1, facts={"a": 1})
    with pytest.raises(ConflictError):
        update_product(db, p["id"], revision=1, facts={"a": 2})


def test_title_change_keeps_old_prediction_snapshot(db):
    p = create_product(db, title="旧标题", category="X", strategy_mode="PUHUO")
    update_product(db, p["id"], revision=1, title="新标题")
    got = get_product(db, p["id"])
    assert got["title"] == "新标题"
    assert got["revision"] == 2


def test_evidence_cross_product_not_mislinked(db):
    p1 = create_product(db, title="商品1", category="X", strategy_mode="PUHUO")
    p2 = create_product(db, title="商品2", category="Y", strategy_mode="PUHUO")
    add_evidence(db, product_id=p1["id"], opportunity_id=None, source_key="kw:牵引绳",
                 content={"price": "10"})
    got1 = get_product(db, p1["id"])
    got2 = get_product(db, p2["id"])
    assert len(got1["evidence"]) == 1
    assert len(got2["evidence"]) == 0


def test_evidence_idempotent_same_source_hash(db):
    p = create_product(db, title="A", category="X", strategy_mode="PUHUO")
    e1 = add_evidence(db, product_id=p["id"], opportunity_id=None, source_key="kw:a", content={"v": 1})
    e2 = add_evidence(db, product_id=p["id"], opportunity_id=None, source_key="kw:a", content={"v": 1})
    assert e1["id"] == e2["id"]
    got = get_product(db, p["id"])
    assert len(got["evidence"]) == 1


def test_offer_linked_to_product(db):
    p = create_product(db, title="牵引绳", category="牵引绳", strategy_mode="PUHUO")
    o = add_offer(db, product_id=p["id"], supplier="爱贝特", sku_key="EPT-99",
                  specification={"color": "蓝"}, procurement_minor=1380,
                  freight_minor=330, sample_count=16, verification_status="VERIFIED")
    assert o["procurement_minor"] == 1380
    got = get_product(db, p["id"])
    assert len(got["offers"]) == 1
