"""上架/启动工作流测试：启动检查硬门禁、方案形成校验、回执登记。"""
from __future__ import annotations

import pytest

from app.db import init_db
from app.services import launch, media
from app.services.products import add_offer, create_product
from tests.conftest import make_png_bytes, make_video_bytes


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "launch.db"
    init_db(p)
    return p


@pytest.fixture
def prod(db):
    return create_product(db, title="上架商品", category="X", strategy_mode="PUHUO")


def _add_verified_offer(db, prod):
    return add_offer(db, product_id=prod["id"], supplier="供应商", sku_key="S",
                     specification={"color": "蓝"}, procurement_minor=1000,
                     verification_status="VERIFIED")


def _add_approved_assets(db, prod):
    """添加 MAIN/SKU/DETAIL 三个已审素材。"""
    for role in ("MAIN", "SKU", "DETAIL"):
        a = media.store_asset(db, db.parent / "assets", product_id=prod["id"],
                              kind="IMAGE", role=role, data=make_png_bytes(),
                              filename=f"{role.lower()}.png", sku_key=f"SKU-{role}")
        media.mark_review(db, a["id"], "APPROVED",usage_id=a["usage_id"])


def test_readiness_gaps_when_empty(db, prod):
    r = launch.launch_readiness(db, prod["id"])
    assert r["ready"] is False
    assert "规格报价" in r["gaps"]
    assert "已审素材" in r["gaps"]


def test_readiness_blocks_unverified_offer(db, prod):
    """R22：未核验货源不能推进。"""
    add_offer(db, product_id=prod["id"], supplier="供应商", sku_key="S",
              specification={"color": "蓝"}, procurement_minor=1000)
    _add_approved_assets(db, prod)
    r = launch.launch_readiness(db, prod["id"])
    assert r["ready"] is False
    assert "规格报价" in r["gaps"]


def test_readiness_blocks_missing_asset_roles(db, prod):
    """R22：缺 MAIN/SKU/DETAIL 任一素材都阻塞。"""
    _add_verified_offer(db, prod)
    a = media.store_asset(db, db.parent / "assets", product_id=prod["id"],
                          kind="IMAGE", role="MAIN", data=make_png_bytes(), filename="m.png")
    media.mark_review(db, a["id"], "APPROVED",usage_id=a["usage_id"])
    r = launch.launch_readiness(db, prod["id"])
    assert r["ready"] is False
    assert "已审素材" in r["gaps"]
    assert set(r["checks"]["已审素材"]["missing"]) == {"SKU", "DETAIL"}


def test_readiness_requires_current_prediction_and_matching_sku(db, prod):
    _add_verified_offer(db, prod)
    _add_approved_assets(db, prod)
    r = launch.launch_readiness(db, prod["id"])
    assert r["ready"] is False
    assert "当前预测" in r["gaps"] and "SKU图片" in r["gaps"]


def test_listing_plan_bound_to_content(db, prod):
    with pytest.raises(ValueError,match="素材|预测"):
        launch.plan_listing(db,product_id=prod["id"],platform="taobao",account_id="LK",title="test",price_minor=2990,stock=10,sku_map={"M":{}},asset_hashes=["missing"])


def test_listing_plan_rejects_negative_price(db, prod):
    """R22：负价格拒绝。"""
    with pytest.raises(ValueError):
        launch.plan_listing(
            db, product_id=prod["id"], platform="taobao", account_id="LK",
            title="x", price_minor=-1, stock=10,
            sku_map={"M": {"price": -1}}, asset_hashes=["h1"])


def test_listing_plan_rejects_negative_stock_and_empty_sku(db, prod):
    """R22：负库存与空 SKU 拒绝。"""
    with pytest.raises(ValueError):
        launch.plan_listing(
            db, product_id=prod["id"], platform="taobao", account_id="LK",
            title="x", price_minor=1000, stock=-5,
            sku_map={"M": {"price": 1000}}, asset_hashes=["h1"])
    with pytest.raises(ValueError):
        launch.plan_listing(
            db, product_id=prod["id"], platform="taobao", account_id="LK",
            title="x", price_minor=1000, stock=10,
            sku_map={}, asset_hashes=["h1"])


def test_price_change_plan(db, prod):
    plan = launch.plan_price_change(
        db, product_id=prod["id"], platform="taobao", account_id="LK",
        external_id="item-1", new_price_minor=2590, reason="测款调价")
    assert plan["action"] == "PRICE_CHANGE"


def test_video_publish_plan_requires_owned_video(db, prod):
    """R22：跨商品/非视频素材不能作为视频发布输入。"""
    # 其他商品视频素材 → 阻塞
    other = create_product(db, title="其他商品", category="Y", strategy_mode="PUHUO")
    v = media.store_asset(db, db.parent / "assets", product_id=other["id"],
                          kind="VIDEO", role="OUTPUT",
                          data=make_video_bytes(), filename="v.mp4")
    with pytest.raises(ValueError):
        launch.plan_video_publish(
            db, product_id=prod["id"], platform="douyin", account_id="AI造物",
            video_asset_id=v["id"], title="新品实测", topics=["宠物"])
    # 当前商品图片素材 → 阻塞
    img = media.store_asset(db, db.parent / "assets", product_id=prod["id"],
                            kind="IMAGE", role="MAIN", data=make_png_bytes(), filename="i.png")
    with pytest.raises(ValueError):
        launch.plan_video_publish(
            db, product_id=prod["id"], platform="douyin", account_id="AI造物",
            video_asset_id=img["id"], title="x", topics=[])


def test_video_publish_plan_ok_when_owned_approved(db, prod):
    v = media.store_asset(db, db.parent / "assets", product_id=prod["id"],
                          kind="VIDEO", role="OUTPUT",
                          data=make_video_bytes(), filename="v.mp4")
    media.mark_review(db, v["id"], "APPROVED")
    plan = launch.plan_video_publish(
        db, product_id=prod["id"], platform="douyin", account_id="AI造物",
        video_asset_id=v["id"], title="新品实测", topics=["宠物"])
    assert plan["action"] == "VIDEO_PUBLISH"


def test_record_listing_result_revision(db, prod):
    l1 = launch.record_listing_result(
        db, product_id=prod["id"], platform="taobao", account_id="LK",
        external_id="item-9", content={"title": "v1"})
    l2 = launch.record_listing_result(
        db, product_id=prod["id"], platform="taobao", account_id="LK",
        external_id="item-9", content={"title": "v2"})
    assert l1["revision"] == 1
    assert l2["revision"] == 2
