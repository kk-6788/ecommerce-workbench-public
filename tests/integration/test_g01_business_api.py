"""G01 业务 API 端到端测试：登录 → 商品 → 预测 → 素材 → 审阅 → 方案 → 审批 → 执行 → 指标 → 复盘。

全部通过 HTTP 页面/API 完成，不直接调 Python 补数据（除构造 fixture 用服务层）。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.auth import create_owner
from app.main import create_app
from tests.conftest import make_png_bytes


@pytest.fixture
def client(tmp_path):
    app = create_app(data_dir=tmp_path, test_mode=True)
    create_owner(tmp_path / "app.db", "kk", "pw123")
    return TestClient(app, base_url="http://testserver")


def _login(client):
    r = client.post("/api/v1/auth/login", json={"username": "kk", "password": "pw123"})
    assert r.status_code == 200, r.text
    csrf = r.json()["data"]["csrf"]
    client.headers.update({"X-CSRF-Token": csrf})
    return csrf


def test_single_product_flow_through_apis(client):
    _login(client)

    # 1. 创建商品
    r = client.post("/api/v1/products", json={
        "title": "G01 测试商品", "category": "牵引绳", "strategy_mode": "PUHUO",
        "facts": {"keyword": "狗狗牵引绳", "median_price": "39.9",
                  "conservative_procurement": "18.6", "top_payments": 2000,
                  "taobao_samples": 50, "supply_samples": 5},
    })
    assert r.status_code == 200, r.text
    prod = r.json()["data"]
    pid = prod["id"]
    assert prod["stage"] == "DISCOVERY"

    # 2. 生成预测
    r = client.post(f"/api/v1/products/{pid}/predictions", json={
        "median_price": "39.9", "conservative_procurement": "18.6",
        "top_payments": 2000, "taobao_samples": 50, "supply_samples": 5,
    })
    assert r.status_code == 200, r.text
    pred = r.json()["data"]
    assert pred["decision"] in ("TEST", "WAIT", "KILL")
    assert pred["snapshot"]["recommended_action"] in ("TEST", "WAIT", "KILL")

    # 3. 上传素材（multipart）+ 审阅
    files = {"file": ("main.png", make_png_bytes(), "image/png")}
    r = client.post(f"/api/v1/products/{pid}/assets",
                    data={"kind": "IMAGE", "role": "MAIN", "is_synthetic": "1"},
                    files=files)
    assert r.status_code == 200, r.text
    asset = r.json()["data"]
    assert asset["mime_type"] == "image/png"
    assert asset["is_synthetic"] == 1

    r = client.post(f"/api/v1/assets/{asset['id']}/review",
                    json={"status": "APPROVED", "note": "G01 审阅通过"})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["review_status"] == "APPROVED"

    # 4. 方案查看 / 审批 / 执行（用 fake adapter）
    r = client.post("/api/v1/plans" if False else f"/api/v1/products/{pid}/offers",
                    json={"supplier": "东阳迪尼", "sku_key": "S",
                          "specification": {"color": "蓝"}, "procurement_minor": 1860,
                          "verification_status": "VERIFIED"})
    assert r.status_code == 200, r.text

    r=client.post("/api/v1/plans",json={"product_id":pid,"action":"IMAGE","provider":"fake","payload":{"input_asset_hashes":[asset["file_hash"]]},"cost":{"maximum_amount":"1.00","quote_source":"synthetic-fixture","failure_billing":"free"}})
    assert r.status_code==200,r.text
    plan=r.json()["data"]
    for kind in ("CONTENT","COST"):
        r=client.post(f"/api/v1/plans/{plan['id']}/approve",json={"kind":kind})
        assert r.status_code==200,r.text
    r=client.post(f"/api/v1/plans/{plan['id']}/execute")
    assert r.status_code==200,r.text
    attempt=r.json()["data"]
    assert attempt["state"]=="PREPARED"
    from app.worker import run_worker
    run_worker(data_dir=client.app.state.settings.data_dir,once=True,registry=client.app.state.registry)
    r2=client.post(f"/api/v1/plans/{plan['id']}/execute")
    assert r2.json()["data"]["id"]==attempt["id"]
    assert r2.json()["data"]["state"]=="SUCCEEDED"
    # 5. 任务查询
    r = client.get("/api/v1/jobs")
    assert r.status_code == 200, r.text

    # 6. 保存指标 + 创建复盘
    r = client.post(f"/api/v1/products/{pid}/metrics", json={
        "source_key": "tb:item-g01:2026-09-18",
        "metrics": {"platform": "taobao", "account_id": "LK",
                    "channel": "organic", "source_timezone": "Asia/Shanghai",
                    "granularity": "day", "measure_mode": "INTERVAL",
                    "period_start": "2026-09-01T00:00:00+08:00",
                    "period_end": "2026-09-20T00:00:00+08:00",
                    "impressions": 1000, "clicks": 50, "orders": 5},
    })
    assert r.status_code == 200, r.text
    metric_id = r.json()["data"]["id"]

    r = client.post(f"/api/v1/products/{pid}/reviews", json={
        "prediction_id": pred["id"], "outcome": "MIXED",
        "hypotheses": [{"statement": "点击低：主图表达弱",
                        "supporting_evidence": ["对比图"],
                        "opposing_evidence": [],
                        "unknowns": ["人群"]}],
        "verification": {"action": "换图", "control": "其他不变",
                         "stop_conditions": {"max_cost": "10"}},
        "metric_ids": [metric_id],
    })
    assert r.status_code == 200, r.text
    assert r.json()["data"]["outcome"] in ("MIXED", "INSUFFICIENT_DATA")

    r = client.get(f"/api/v1/products/{pid}/reviews")
    assert r.status_code == 200, r.text
    assert len(r.json()["data"]) >= 1
    assert client.get("/review").status_code==200


def test_cross_product_review_rejected(client):
    """G01/R18：跨商品预测引用被拒绝（409）。"""
    _login(client)
    r1 = client.post("/api/v1/products", json={"title": "A", "category": "X",
                                               "strategy_mode": "PUHUO"})
    r2 = client.post("/api/v1/products", json={"title": "B", "category": "Y",
                                               "strategy_mode": "PUHUO"})
    pa, pb = r1.json()["data"]["id"], r2.json()["data"]["id"]
    r = client.post(f"/api/v1/products/{pa}/predictions", json={})
    assert r.status_code == 200, r.text
    pred_a = r.json()["data"]["id"]
    # B 商品引用 A 的预测 → 409
    r = client.post(f"/api/v1/products/{pb}/reviews", json={
        "prediction_id": pred_a, "outcome": "FAILED",
        "hypotheses": [{"statement": "h", "supporting_evidence": ["e"],
                        "opposing_evidence": [], "unknowns": ["u"]}],
        "verification": {"action": "v"}, "metric_ids": [],
    })
    assert r.status_code == 409, r.text


def test_invalid_metric_rejected(client):
    """G01/R19：非法指标（负订单/倒置窗口）返回 422。"""
    _login(client)
    r = client.post("/api/v1/products", json={"title": "M", "category": "X",
                                              "strategy_mode": "PUHUO"})
    pid = r.json()["data"]["id"]
    r = client.post(f"/api/v1/products/{pid}/metrics", json={
        "source_key": "k-bad", "metrics": {"platform": "taobao", "account_id": "a",
                                           "channel": "organic",
                                           "source_timezone": "Asia/Shanghai",
                                           "granularity": "day",
                                           "measure_mode": "INTERVAL",
                                           "period_start": "2026-09-05T00:00:00+08:00",
                                           "period_end": "2026-09-01T00:00:00+08:00",
                                           "orders": -1}})
    assert r.status_code == 422, r.text
