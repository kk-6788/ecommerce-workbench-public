"""页面测试：登录、页面渲染、错误可理解、内容转义、素材预览。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.auth import create_owner
from app.main import create_app
from app.services.products import create_product
from app.services import media
from tests.conftest import make_png_bytes


@pytest.fixture
def client(tmp_path):
    app = create_app(data_dir=tmp_path, test_mode=True)
    create_owner(tmp_path / "app.db", "kk", "pw123")
    return TestClient(app, base_url="http://testserver")


def _login(client):
    r = client.post("/api/v1/auth/login",
                    json={"username": "kk", "password": "pw123"})
    assert r.status_code == 200
    csrf = r.json()["data"]["csrf"]
    client.headers.update({"X-CSRF-Token": csrf})
    return csrf


def test_login_redirects_to_dashboard(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 307  # 未登录重定向 /login


def test_dashboard_renders_after_login(client):
    _login(client)
    r = client.get("/")
    assert r.status_code == 200
    assert "今日工作" in r.text
    assert "商品队列" in r.text


def test_selection_page_shows_opportunities(client, tmp_path):
    _login(client)
    r = client.get("/selection")
    assert r.status_code == 200
    assert "选品与货源" in r.text
    assert "词池" in r.text


def test_product_page_renders(client, tmp_path):
    _login(client)
    prod = create_product(tmp_path / "app.db", title="测试<商品>",
                          category="X", strategy_mode="PUHUO")
    r = client.get(f"/product?id={prod['id']}")
    assert r.status_code == 200
    assert "商品档案" in r.text


def test_media_page_and_asset_preview(client, tmp_path):
    _login(client)
    prod = create_product(tmp_path / "app.db", title="图商品", category="X",
                          strategy_mode="PUHUO")
    png = make_png_bytes()
    asset = media.store_asset(tmp_path / "app.db", tmp_path / "assets",
                              product_id=prod["id"], kind="IMAGE", role="MAIN",
                              data=png, filename="a.png")
    r = client.get(f"/api/v1/assets/{asset['id']}/file")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    # R20：预览响应带 CSP/nosniff，不随应用同源执行脚本
    assert "Content-Security-Policy" in r.headers
    assert r.headers["X-Content-Type-Options"] == "nosniff"


def test_launch_review_pages_render(client):
    _login(client)
    assert client.get("/launch").status_code == 200
    assert client.get("/review").status_code == 200


def test_content_escaped(client, tmp_path):
    """HTML 注入被转义。"""
    _login(client)
    prod = create_product(tmp_path / "app.db", title="<script>alert(1)</script>",
                          category="X", strategy_mode="PUHUO")
    r = client.get(f"/product?id={prod['id']}")
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;" in r.text


def test_api_requires_csrf(client):
    client.post("/api/v1/auth/login", json={"username": "kk", "password": "pw123"})
    # 无 CSRF 的写操作 403
    r = client.post("/api/v1/products",
                    json={"title": "无csrf", "category": "X", "strategy_mode": "PUHUO"})
    assert r.status_code == 403
