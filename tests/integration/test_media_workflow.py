"""图片/视频工作流测试：方案与事实绑定、未批准不生成、提供方缺失。"""
from __future__ import annotations

import pytest

from app.adapters.registry import Registry
from app.db import init_db
from app.services import media
from app.services.plans import get_plan
from app.services.products import create_product


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "m.db"
    init_db(p)
    return p


@pytest.fixture
def prod(db):
    p=create_product(db,title="图商品",category="X",strategy_mode="PUHUO",facts={"color":"blue","material":"尼龙"})
    from tests.conftest import make_png_bytes
    a=media.store_asset(db,db.parent/"assets",product_id=p["id"],kind="IMAGE",role="REFERENCE",data=make_png_bytes(),filename="r.png")
    media.mark_review(db,a["id"],"APPROVED",usage_id=a["usage_id"])
    p["test_hash"]=a["file_hash"]
    return p


def test_image_plan_bound_to_facts(db, prod):
    plan = media.plan_image_generation(
        db, product_id=prod["id"], provider="fake-image-adapter",
        sku_key="blue-m", facts={"color": "blue", "material": "尼龙"},
        input_asset_hashes=[prod["test_hash"]], prompt="生成主图，不改规格",
        estimated_amount="0.80", maximum_amount="1.00")
    assert plan["state"] == "DRAFT"  # 未批准不生成
    assert plan["requires_content"] == 1
    assert plan["requires_cost"] == 1
    assert plan["plan_hash"]


def test_image_plan_hash_changes_with_facts(db, prod):
    p1 = media.plan_image_generation(
        db, product_id=prod["id"], provider="fake", sku_key="s",
        facts={"color": "blue"}, input_asset_hashes=[prod["test_hash"]], prompt="a")
    p2 = media.plan_image_generation(
        db, product_id=prod["id"], provider="fake", sku_key="s",
        facts={"color": "red"}, input_asset_hashes=[prod["test_hash"]], prompt="a")
    assert p1["plan_hash"] != p2["plan_hash"]


def test_video_plan_with_missing_materials(db, prod):
    with pytest.raises(ValueError,match="缺少视频材料"):
        media.plan_video_generation(db,product_id=prod["id"],provider="fake",title="片",script="旁白",shot_list=[{"id":1}],missing_materials=["开箱"],maximum_amount="2.00")


def test_provider_missing_returns_capability_unavailable(db, prod):
    """提供方缺失返回 CAPABILITY_UNAVAILABLE（由调用方处理，不创建 plan）。"""
    reg = Registry()
    assert media.provider_available(reg, "不存在的提供方") is False
