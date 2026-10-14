"""批次1回归测试：R01 登录后写请求、R30 非法输入拒绝、R29 页面 500。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.auth import create_owner
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(data_dir=tmp_path, test_mode=True)
    create_owner(tmp_path / "app.db", "kk", "pw123")
    return TestClient(app, base_url="http://testserver")


def _login(client) -> str:
    r = client.post("/api/v1/auth/login",
                    json={"username": "kk", "password": "pw123"})
    assert r.status_code == 200
    return r.json()["data"]["csrf"]


def test_r01_valid_csrf_after_login_can_write(client):
    """R01：登录返回的原始 csrf 经 sha256 摘要后应通过校验（原本 403）。"""
    csrf = _login(client)
    r = client.post("/api/v1/products",
                    json={"title": "测试商品", "category": "牵引绳",
                          "strategy_mode": "PUHUO"},
                    headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["title"] == "测试商品"


def test_r01_missing_csrf_rejected(client):
    _login(client)
    r = client.post("/api/v1/products",
                    json={"title": "无csrf", "category": "X",
                          "strategy_mode": "PUHUO"})
    assert r.status_code == 403


def test_r01_wrong_csrf_rejected(client):
    _login(client)
    r = client.post("/api/v1/products",
                    json={"title": "错csrf", "category": "X",
                          "strategy_mode": "PUHUO"},
                    headers={"X-CSRF-Token": "wrong-token"})
    assert r.status_code == 403


def test_r30_blank_title_rejected(client):
    csrf = _login(client)
    r = client.post("/api/v1/products",
                    json={"title": "  ", "category": "X", "strategy_mode": "PUHUO"},
                    headers={"X-CSRF-Token": csrf})
    assert r.status_code == 422


def test_r30_bad_strategy_mode_rejected(client):
    csrf = _login(client)
    r = client.post("/api/v1/products",
                    json={"title": "T", "category": "X", "strategy_mode": "EVIL"},
                    headers={"X-CSRF-Token": csrf})
    assert r.status_code == 422


def test_r30_negative_procurement_rejected(client):
    csrf = _login(client)
    p = client.post("/api/v1/products",
                    json={"title": "商品", "category": "X", "strategy_mode": "PUHUO"},
                    headers={"X-CSRF-Token": csrf})
    pid = p.json()["data"]["id"]
    r = client.post(f"/api/v1/products/{pid}/offers",
                    json={"supplier": "S", "sku_key": "K", "specification": {},
                          "procurement_minor": -5},
                    headers={"X-CSRF-Token": csrf})
    assert r.status_code == 422


def test_r30_bad_verification_status_rejected(client):
    csrf = _login(client)
    p = client.post("/api/v1/products",
                    json={"title": "商品2", "category": "X", "strategy_mode": "PUHUO"},
                    headers={"X-CSRF-Token": csrf})
    pid = p.json()["data"]["id"]
    r = client.post(f"/api/v1/products/{pid}/offers",
                    json={"supplier": "S", "sku_key": "K", "specification": {},
                          "verification_status": "not-a-status"},
                    headers={"X-CSRF-Token": csrf})
    assert r.status_code == 422


def test_r30_missing_batch_rejected(client):
    csrf = _login(client)
    r = client.post("/api/v1/products",
                    json={"title": "无批次", "category": "X", "strategy_mode": "PUHUO",
                          "batch_id": "no-such-batch"},
                    headers={"X-CSRF-Token": csrf})
    assert r.status_code == 422
