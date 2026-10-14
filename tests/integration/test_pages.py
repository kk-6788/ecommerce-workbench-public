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
    assert 'src="/static/theme.js"' in r.text
    assert 'class="theme-switcher"' in r.text
    for theme in ("obsidian", "ocean", "forest", "amber"):
        assert f'data-theme-choice="{theme}"' in r.text


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


def test_help_page_covers_core_workflows_and_boundaries(client):
    _login(client)
    response = client.get("/help")

    assert response.status_code == 200
    for anchor in ("selection-guide", "supplier-guide", "product-guide", "video-guide"):
        assert f'<details class="tutorial-guide" id="{anchor}" open>' in response.text
    for title in ("选品", "货源", "商品档案", "推视频"):
        assert title in response.text
    for href in ("/selection", "/imports", "/product", "/media", "/launch", "/integrations"):
        assert f'href="{href}"' in response.text
    assert "方案已保存不等于视频已生成" in response.text
    assert "发布前仍需你确认" in response.text
    for phrase in (
        "售价", "需求样本", "成本", "供应稳定性", "来源时间", "阶段",
        "修订版本", "素材审阅状态", "口播", "画面提示", "分镜",
        "修改后形成新版本", "当前只完成准备",
    ):
        assert phrase in response.text
    for boundary in (
        "内容审批是当前路径",
        "只有方案实际涉及费用且已有费用方案时，才需要费用审批",
        "才需要 COST",
        "当前网页尚不能录入费用上限、投放时长、指标、停止条件",
        "付费投放方案和费用审批只能先准备，另行补齐后再确认",
        "上架启动页可准备内容方案并查看、核对已有发布方案，但当前不能录入付费投放条件",
        "双击“打开电商作业平台”",
    ):
        assert boundary in response.text


def test_work_pages_do_not_render_literal_newline_escape(client):
    _login(client)
    for path in ("/selection", "/review"):
        response = client.get(path)
        assert response.status_code == 200
        assert "\\n" not in response.text


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
