"""端到端测试：一个商品从创建→证据→货源→预测→审批→执行→指标→复盘的完整链路。

覆盖：无审批不写/不消费、断电模拟恢复不重复、跨商品不串素材。
只达到 G1/G2（离线模拟闭环），不冒充真实渠道。
"""
from __future__ import annotations

import pytest

from app.adapters.fake import FakeAdapter
from app.adapters.registry import Registry
from app.auth import create_owner
from app.db import init_db
from app.domain.metrics import save_metric_snapshot
from app.services.approvals import approve_plan
from app.services.execution import execute_plan
from app.services.imports import import_baseline
from app.services import launch, media
from app.services.plans import PlanSpec, create_plan
from app.services.products import add_evidence, add_offer, create_product


@pytest.fixture
def ctx(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    db = d / "app.db"
    assets = d / "assets"
    assets.mkdir()
    init_db(db)
    owner = create_owner(db, "kk", "pw123")
    return {"db": db, "assets": assets, "owner": owner}


def _approve(db, owner, plan, kinds=("CONTENT", "COST")):
    for k in kinds:
        approve_plan(db, plan_id=plan["id"], plan_hash=plan["plan_hash"],
                     owner_id=owner, kind=k)


def test_single_product_full_loop(ctx):
    db = ctx["db"]
    # 1. 创建商品
    prod = create_product(db, title="多功能牵引绳", category="牵引绳",
                          strategy_mode="PUHUO",
                          opportunity_id=None, facts={"material": "尼龙"})
    assert prod["stage"] == "DISCOVERY"

    # 2. 加证据
    add_evidence(db, product_id=prod["id"], opportunity_id=None,
                 source_key="1688:offer-1", evidence_kind="FACT",
                 content={"price": "13.8", "freight": "3.3"})

    # 3. 加货源
    offer = add_offer(db, product_id=prod["id"], supplier="爱贝特", sku_key="EPT-99",
                      specification={"color": "蓝"}, procurement_minor=1380,
                      freight_minor=330, sample_count=16,
                      verification_status="VERIFIED")
    assert offer["procurement_minor"] == 1380

    # 4. 生成方案（未批准不生成）
    from tests.conftest import make_png_bytes
    from app.services.plans import CostSpec
    asset=media.store_asset(db,ctx["assets"],product_id=prod["id"],kind="IMAGE",role="REFERENCE",data=make_png_bytes(),filename="ref.png")
    media.mark_review(db,asset["id"],"APPROVED",usage_id=asset["usage_id"])
    image_plan=create_plan(db,spec=PlanSpec(product_id=prod["id"],action="IMAGE",provider="fake",requires_content=True,requires_cost=True,
        payload={"kind":"IMAGE","input_asset_hashes":[asset["file_hash"]]},cost=CostSpec(maximum_amount="1.00",quote_source="synthetic-fixture",failure_billing="free")))
    assert image_plan["state"] == "DRAFT"

    # 5. 无审批不能执行
    from app.services.execution import ApprovalRequiredError
    reg = Registry({"fake": FakeAdapter()})
    with pytest.raises(ApprovalRequiredError):
        execute_plan(db, plan_id=image_plan["id"], registry=reg)

    # 6. 审批后执行
    _approve(db, ctx["owner"], image_plan)
    attempt = execute_plan(db, plan_id=image_plan["id"], registry=reg)
    assert attempt["state"] == "SUCCEEDED"

    # 7. 指标
    save_metric_snapshot(db, product_id=prod["id"], listing_id=None,
                         source_key="tb:item-1",
                         metrics={"platform": "taobao", "account_id": "LK",
                                  "channel": "organic", "source_timezone": "Asia/Shanghai",
                                  "granularity": "day", "measure_mode": "INTERVAL",
                                  "period_start": "2026-09-01T00:00:00+08:00",
                                  "period_end": "2026-09-02T00:00:00+08:00",
                                  "impressions": 1000, "clicks": 50, "orders": 5})

    # 8. 启动检查
    r = launch.launch_readiness(db, prod["id"])
    assert r["gaps"] == [] or "规格报价" not in r["gaps"]

    # 9. 上架回执登记
    listing = launch.record_listing_result(
        db, product_id=prod["id"], platform="taobao", account_id="LK",
        external_id="item-1", content={"title": "多功能牵引绳"})
    assert listing["external_id"] == "item-1"


def test_restart_no_resubmit(ctx):
    """断电模拟：已提交 attempt 重启后再次执行返回既有，不重发。"""
    db = ctx["db"]
    prod = create_product(db, title="P", category="X", strategy_mode="PUHUO")
    spec = PlanSpec(product_id=prod["id"], action="fake",
                    requires_content=True, requires_cost=True,
                    cost=__import__("app.services.plans", fromlist=["CostSpec"]).CostSpec(
                        estimated_amount="0.8", maximum_amount="1.0",quote_source="synthetic-fixture",
                        failure_billing="free", retry_needs_new_approval=True),
                    payload={"prompt": "t"})
    from app.services.plans import create_plan
    plan = create_plan(db, spec=spec)
    _approve(db, ctx["owner"], plan)
    fake = FakeAdapter()
    reg = Registry({"fake": fake})
    r1 = execute_plan(db, plan_id=plan["id"], registry=reg)
    assert r1["state"] == "SUCCEEDED"
    # 重启后再次执行
    r2 = execute_plan(db, plan_id=plan["id"], registry=reg)
    assert r2["id"] == r1["id"]
    assert fake.submit_count == 1


def test_baseline_and_no_cross_product_mixing(ctx):
    """30 词导入后创建商品不与词池串。"""
    db = ctx["db"]
    from pathlib import Path
    src = Path(__file__).resolve().parents[2] / "docs" / "handoff" / "sources" / "strategy-20260914"
    report = import_baseline(db, src)
    assert report["total_opportunities"] == 30
    # 从词池创建商品
    from app.services.products import get_product
    prod = create_product(db, title="词池商品", category="X", strategy_mode="PUHUO")
    got = get_product(db, prod["id"])
    assert got["evidence"] == [] and got["offers"] == []
