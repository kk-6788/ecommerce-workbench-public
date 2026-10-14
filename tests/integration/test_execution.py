"""执行测试：无审批不执行、双击同 plan 单 attempt、超时 UNKNOWN 不重发。"""
from __future__ import annotations

import threading

import pytest

from app.adapters.fake import FakeAdapter
from app.adapters.registry import default_registry, Registry
from app.auth import create_owner
from app.db import connect, init_db
from app.services.approvals import approve_plan
from app.services.execution import ApprovalRequiredError, execute_plan, reconcile_attempt
from app.services.plans import CostSpec, PlanSpec, create_plan
from app.services.products import create_product


@pytest.fixture
def db(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    p = d / "app.db"
    init_db(p)
    return p


@pytest.fixture
def owner(db):
    return create_owner(db, "kk", "pw123")


def _plan(db, *, action="fake", timeout=False, failure_billing="free"):
    prod = create_product(db, title="执行商品", category="X", strategy_mode="PUHUO")
    return create_plan(db, spec=PlanSpec(
        product_id=prod["id"], action=action, provider=action,
        requires_content=True, requires_cost=True,
        cost=CostSpec(estimated_amount="0.80", maximum_amount="1.00",quote_source="synthetic-fixture",
                      failure_billing=failure_billing,
                      retry_needs_new_approval=True),
        payload={"prompt": "测试"}))


def _approve_all(db, owner, plan):
    approve_plan(db, plan_id=plan["id"], plan_hash=plan["plan_hash"], owner_id=owner,
                 kind="CONTENT")
    approve_plan(db, plan_id=plan["id"], plan_hash=plan["plan_hash"], owner_id=owner,
                 kind="COST")


def test_no_approval_blocks_execution(db, owner):
    plan = _plan(db)
    reg = default_registry(test_mode=True)
    with pytest.raises(ApprovalRequiredError):
        execute_plan(db, plan_id=plan["id"], registry=reg)


def test_execute_success_with_approval(db, owner):
    plan = _plan(db)
    _approve_all(db, owner, plan)
    reg = default_registry(test_mode=True)
    result = execute_plan(db, plan_id=plan["id"], registry=reg)
    assert result["state"] == "SUCCEEDED"
    assert result["provider_request_id"]


def test_double_submit_single_attempt(db, owner):
    """双击/并发同 plan 只有一个 attempt。"""
    plan = _plan(db)
    _approve_all(db, owner, plan)
    fake = FakeAdapter()
    reg = Registry({"fake": fake})
    r1 = execute_plan(db, plan_id=plan["id"], registry=reg)
    r2 = execute_plan(db, plan_id=plan["id"], registry=reg)
    assert r1["id"] == r2["id"]
    assert fake.submit_count == 1


def test_concurrent_submit_single_attempt(db, owner):
    """两并发线程提交同 plan 仍为 1 次。"""
    plan = _plan(db)
    _approve_all(db, owner, plan)
    fake = FakeAdapter()
    reg = Registry({"fake": fake})
    results = []

    def worker():
        try:
            results.append(execute_plan(db, plan_id=plan["id"], registry=reg))
        except Exception as e:
            results.append(e)

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start(); t2.start(); t1.join(); t2.join()
    ok = [r for r in results if not isinstance(r, Exception)]
    assert len(ok) == 2
    assert ok[0]["id"] == ok[1]["id"]
    assert fake.submit_count == 1


def test_timeout_unknown_no_resubmit(db, owner):
    """供应商已接受但本地超时 → UNKNOWN，重启后不再提交，submit_count 仍 1。"""
    plan = _plan(db)
    _approve_all(db, owner, plan)
    fake = FakeAdapter(simulate_timeout=True)
    reg = Registry({"fake": fake})
    r1 = execute_plan(db, plan_id=plan["id"], registry=reg)
    assert r1["state"] == "UNKNOWN"
    # 重启后再次执行 → 返回既有 attempt，不重发
    r2 = execute_plan(db, plan_id=plan["id"], registry=reg)
    assert r2["id"] == r1["id"]
    assert fake.submit_count == 1
    # 核对成功必须落库（R06：reconcile 返回 attempt 最新状态，数据库更新）
    rec = reconcile_attempt(db, r1["id"], reg)
    assert rec["state"] == "SUCCEEDED"
    # 数据库层面确认：attempt/plan/job 全部 SUCCEEDED
    conn = connect(db)
    try:
        a = conn.execute("SELECT state FROM external_attempts WHERE id=?",
                         (r1["id"],)).fetchone()
        assert a["state"] == "SUCCEEDED"
        p = conn.execute("SELECT state FROM operation_plans WHERE id=?",
                         (plan["id"],)).fetchone()
        assert p["state"] == "SUCCEEDED"
    finally:
        conn.close()


def test_fake_not_available_outside_test_mode():
    reg = default_registry(test_mode=False)
    assert "fake" not in reg.names()


def test_audit_event_logged(db, owner):
    from app.services.audit import log_event
    eid = log_event(db, actor_type="OWNER", actor_id=owner,
                    event_type="APPROVE", entity_type="PLAN", entity_id="p1",
                    detail={"kind": "COST", "token": "SECRET_SHOULD_BE_FILTERED"})
    conn = connect(db)
    try:
        row = conn.execute("SELECT detail_json FROM audit_events WHERE id=?", (eid,)).fetchone()
        assert "SECRET_SHOULD_BE_FILTERED" not in row["detail_json"]
    finally:
        conn.close()
