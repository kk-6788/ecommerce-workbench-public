"""G02 批次流转测试：创建/分配、阶段推进、阻塞原因、目标判定。"""
from __future__ import annotations

import pytest

from app.db import init_db
from app.services import batch_flow
from app.services import media
from app.services.products import add_offer, create_product
from tests.conftest import make_png_bytes


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "g02.db"
    init_db(p)
    return p


def test_create_and_assign_batch(db):
    b = batch_flow.create_batch(db, name="第39周", week_start="2026-09-21",
                                target_min=3, target_max=5)
    p1 = create_product(db, title="P1", category="X", strategy_mode="PUHUO")
    p2 = create_product(db, title="P2", category="Y", strategy_mode="PUHUO")
    res = batch_flow.assign_to_batch(db, b["id"], [p1["id"], p2["id"], "nope"])
    assert res["assigned"] == [p1["id"], p2["id"]]
    assert res["missing"] == ["nope"]


def test_stage_progression_with_evidence_offer_prediction_assets(db):
    p = create_product(db, title="推进商品", category="X", strategy_mode="PUHUO")
    pid = p["id"]

    # DISCOVERY：档案完整 → EVIDENCE
    st = batch_flow.product_stage(db, pid)
    assert st["stage"] == "EVIDENCE"
    assert st["reason"] == "缺证据"

    # 添加证据 + 已核验货源 → PREDICTED
    from app.services.products import add_evidence
    add_evidence(db, product_id=pid, opportunity_id=None, source_key="k1",
                 evidence_kind="FACT", content={"a": 1})
    add_offer(db, product_id=pid, supplier="S", sku_key="SKU1",
              specification={"c": "蓝"}, procurement_minor=1800,
              verification_status="VERIFIED")
    st = batch_flow.product_stage(db, pid)
    assert st["stage"] == "PREDICTED"

    # 生成预测 → ASSETS
    from app.domain.legacy_rules import TierInput, grade_tier
    from app.domain.decisions import build_prediction_snapshot, save_prediction
    from app.api.business_routes import _ensure_model_versions
    versions = _ensure_model_versions(db)
    tier = grade_tier(TierInput(
        keyword="狗狗牵引绳", category_group="牵引", median_price=__import__("decimal").Decimal("39.9"),
        conservative_procurement=__import__("decimal").Decimal("18.6"),
        top_payments=2000, taobao_samples=50, supply_samples=5))
    snap = build_prediction_snapshot(candidate_id=pid, strategy_version="v1",
                                     engine_version="v1", outcome_definition_version="v1",
                                     tier=tier,input_snapshot={"revision":p["revision"]})
    save_prediction(db, product_id=pid, strategy_version_id=versions["STRATEGY"],
                    engine_version_id=versions["ENGINE"],
                    outcome_version_id=versions["OUTCOME"], snapshot=snap, decision="TEST")
    st = batch_flow.product_stage(db, pid)
    assert st["stage"] == "ASSETS"
    assert "MAIN,SKU,DETAIL" in st["reason"] or "缺素材" in (st["reason"] or "")

    # 上传三角色素材 → REVIEW（未审）
    for role in ("MAIN", "SKU", "DETAIL"):
        a = media.store_asset(db, db.parent / "assets", product_id=pid,
                              kind="IMAGE", role=role, data=make_png_bytes(),
                              filename=f"{role.lower()}.png", sku_key="SKU1")
    st = batch_flow.product_stage(db, pid)
    assert st["stage"] == "REVIEW"
    assert "未审" in (st["reason"] or "")

    # 全部审阅 → READY
    conn = __import__("app.db", fromlist=["connect"]).connect(db)
    asset_ids = [(r[0],r[1]) for r in conn.execute(
        "SELECT a.id,u.id FROM assets a JOIN asset_usages u ON u.asset_id=a.id"
        " WHERE u.product_id=?", (pid,)).fetchall()]
    conn.close()
    for aid,uid in asset_ids:
        media.mark_review(db, aid, "APPROVED",usage_id=uid)
    st = batch_flow.product_stage(db, pid)
    assert st["stage"] == "READY"
    assert st["reason"] == "未启动"


def test_batch_progress_and_target(db):
    b = batch_flow.create_batch(db, name="W39", week_start="2026-09-21",
                                target_min=1, target_max=3)
    p1 = create_product(db, title="P1", category="X", strategy_mode="PUHUO")
    batch_flow.assign_to_batch(db, b["id"], [p1["id"]])
    prog = batch_flow.batch_progress(db, b["id"])
    assert prog["ready_count"] == 0
    assert prog["target_reached"] is False
    assert prog["stage_counts"].get("EVIDENCE", 0) == 1
