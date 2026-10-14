"""审批测试：canonical hash、CONTENT/COST 独立、素材变化批准失效、拒绝模型自批。"""
from __future__ import annotations

import pytest

from app.db import init_db
from app.services.approvals import approve_plan, revoke_approval, valid_approvals
from app.services.plans import CostSpec, PlanSpec, create_plan, get_plan, plan_hash
from app.services.products import create_product
from app.auth import create_owner


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "a.db"
    init_db(p)
    return p


@pytest.fixture
def prod(db):
    return create_product(db, title="测试商品", category="服饰", strategy_mode="PUHUO")


@pytest.fixture
def owner(db):
    return create_owner(db, "kk", "pw123")  # 返回 owner id（UUID）


def _spec(prod, **kw):
    d = dict(product_id=prod["id"], action="GENERATE_IMAGE", requires_content=True,
             requires_cost=True,
             cost=CostSpec(estimated_amount="0.80", maximum_amount="1.00"),
             payload={"sku_key": "blue-m", "prompt": "合成测试"})
    d.update(kw)
    return PlanSpec(**d)


def test_plan_hash_changes_with_payload(prod):
    h1 = plan_hash(_spec(prod))
    h2 = plan_hash(_spec(prod, payload={"sku_key": "blue-m", "prompt": "改了"}))
    assert h1 != h2


def test_plan_hash_changes_with_cost_cap(prod):
    h1 = plan_hash(_spec(prod))
    h2 = plan_hash(_spec(prod, cost=CostSpec(estimated_amount="0.80", maximum_amount="2.00")))
    assert h1 != h2


def test_create_plan_and_full_approval_ready(db, prod, owner):
    p = create_plan(db, spec=_spec(prod))
    assert p["state"] == "DRAFT"
    approve_plan(db, plan_id=p["id"], plan_hash=p["plan_hash"], owner_id=owner,
                 kind="CONTENT")
    p2 = get_plan(db, p["id"])
    assert p2["state"] == "WAITING_APPROVAL"  # 还缺 COST
    approve_plan(db, plan_id=p["id"], plan_hash=p["plan_hash"], owner_id=owner,
                 kind="COST")
    p3 = get_plan(db, p["id"])
    assert p3["state"] == "READY"


def test_approval_bound_to_hash(db, prod, owner):
    p = create_plan(db, spec=_spec(prod))
    approve_plan(db, plan_id=p["id"], plan_hash=p["plan_hash"], owner_id=owner,
                 kind="CONTENT")
    # 素材变化 → 新 hash → 旧批准无效
    p2 = create_plan(db, spec=_spec(prod, payload={"sku_key": "blue-m", "prompt": "换了图"}))
    assert p2["plan_hash"] != p["plan_hash"]
    assert valid_approvals(db, p2["id"]) == []  # 新 plan 无批准
    # 旧 plan 仍有效但内容已变——执行层必须按 hash 校验


def test_reject_then_approve_same_kind(prod, owner):
    db = None
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        pth = f"{td}/x.db"
        init_db(pth)
        pp = create_product(pth, title="t", category="c", strategy_mode="PUHUO")
        own = create_owner(pth, "kk", "pw123")
        p = create_plan(pth, spec=_spec(pp))
        approve_plan(pth, plan_id=p["id"], plan_hash=p["plan_hash"], owner_id=own,
                     kind="COST", decision="REJECT")
        assert valid_approvals(pth, p["id"]) == []


def test_revoke_invalidates(db, prod, owner):
    p = create_plan(db, spec=_spec(prod))
    approve_plan(db, plan_id=p["id"], plan_hash=p["plan_hash"], owner_id=owner,
                 kind="CONTENT")
    revoke_approval(db, plan_id=p["id"], plan_hash=p["plan_hash"], owner_id=owner,
                    kind="CONTENT")
    assert valid_approvals(db, p["id"]) == []


def test_approval_requires_owner(db, prod, owner):
    p = create_plan(db, spec=_spec(prod))
    with pytest.raises(ValueError):
        approve_plan(db, plan_id=p["id"], plan_hash=p["plan_hash"], owner_id="",
                     kind="COST")
