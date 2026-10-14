"""持久任务队列测试：租约/心跳/取消、双 worker 不重复领取、恢复。"""
from __future__ import annotations

import threading
import time

import pytest

from app.db import init_db
from app.services import jobs


@pytest.fixture
def db(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    p = d / "app.db"
    init_db(p)
    return p


def test_enqueue_and_claim(db):
    j = jobs.enqueue_job(db, kind="echo", input_data={"value": "hi"})
    assert j["state"] == "QUEUED"
    claimed = jobs.claim_job(db)
    assert claimed["id"] == j["id"]
    assert claimed["state"] == "RUNNING"
    assert claimed["attempts"] == 1


def test_two_workers_do_not_claim_same(db):
    j = jobs.enqueue_job(db, kind="echo")
    c1 = jobs.claim_job(db)
    c2 = jobs.claim_job(db)
    assert c1["id"] == j["id"]
    assert c2 is None  # 第二个 worker 拿不到


def test_finish_succeeded(db):
    j = jobs.enqueue_job(db, kind="echo")
    claim = jobs.claim_job(db)
    done = jobs.finish_job(db, j["id"], state="SUCCEEDED", result={"ok": 1}, lease_token=claim["lease_token"])
    assert done["state"] == "SUCCEEDED"
    assert done["result"] == {"ok": 1}


def test_cancel_queued(db):
    j = jobs.enqueue_job(db, kind="echo")
    c = jobs.cancel_job(db, j["id"])
    assert c["state"] == "CANCELLED"


def test_cancel_running_marks_request(db):
    j = jobs.enqueue_job(db, kind="echo")
    jobs.claim_job(db)
    c = jobs.cancel_job(db, j["id"])
    assert c["cancel_requested"] == 1
    assert c["state"] == "RUNNING"  # 执行中只标请求


def test_expired_lease_pure_compute_recovered(db):
    j = jobs.enqueue_job(db, kind="pure_compute")
    jobs.claim_job(db, lease_seconds=0)  # 立即过期
    # 手动把 lease_until 改到过去
    from app.db import connect
    conn = connect(db)
    conn.execute("UPDATE jobs SET lease_until='2000-01-01T00:00:00+00:00' WHERE id=?", (j["id"],))
    conn.commit()
    conn.close()
    recovered = jobs.recover_expired(db)
    assert len(recovered) == 1
    assert recovered[0]["to"] == "QUEUED"
    # 可再次领取
    claimed = jobs.claim_job(db)
    assert claimed["id"] == j["id"]


def test_expired_lease_with_plan_reconciling(db):
    # 构造真实 plan 供外键引用
    from app.services.products import create_product
    from app.services.plans import PlanSpec, create_plan
    prod = create_product(db, title="外部任务商品", category="X", strategy_mode="PUHUO")
    plan = create_plan(db, spec=PlanSpec(
        product_id=prod["id"], action="EXTERNAL_CALL", requires_cost=False,
        requires_content=False))
    j = jobs.enqueue_job(db, kind="external", plan_id=plan["id"])
    jobs.claim_job(db, lease_seconds=0)
    from app.db import connect
    conn = connect(db)
    conn.execute("UPDATE jobs SET lease_until='2000-01-01T00:00:00+00:00' WHERE id=?", (j["id"],))
    conn.commit()
    conn.close()
    recovered = jobs.recover_expired(db)
    assert recovered[0]["to"] == "RECONCILING"


def test_heartbeat_updates(db):
    j = jobs.enqueue_job(db, kind="echo")
    claim = jobs.claim_job(db)
    jobs.heartbeat(db, j["id"],lease_token=claim["lease_token"])
    got = jobs.get_job(db, j["id"])
    assert got["heartbeat_at"] is not None


def test_worker_once_end_to_end(db):
    """worker --once 处理一个任务后退出。"""
    j = jobs.enqueue_job(db, kind="echo", input_data={"value": "worker-test"})
    from app.worker import run_worker
    run_worker(data_dir=db.parent, once=True)
    got = jobs.get_job(db, j["id"])
    assert got["state"] == "SUCCEEDED"
    assert got["result"]["echo"] == "worker-test"
