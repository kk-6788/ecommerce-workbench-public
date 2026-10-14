"""Adapter 契约测试：构造不联网、fake 仅 test_mode、未登录返回明确状态。"""
from __future__ import annotations

from app.adapters.base import NEEDS_LOGIN
from app.adapters.douyin import DouyinAdapter
from app.adapters.registry import default_registry
from app.adapters.supplier_1688 import Supplier1688Adapter
from app.adapters.taobao import TaobaoAdapter


def test_construct_does_not_network():
    """构造不联网：直接实例化所有渠道 adapter 应成功。"""
    TaobaoAdapter()
    DouyinAdapter()
    Supplier1688Adapter()


def test_unlogged_status():
    t = TaobaoAdapter(logged_in=False)
    caps = t.capabilities()
    assert caps["status"] == "NEEDS_LOGIN"
    probe = t.probe({})
    assert probe.ok is False
    assert probe.code == NEEDS_LOGIN


def test_collect_failure_not_zero():
    """采集失败不写 0：未接入真实通道返回 CAPABILITY_UNAVAILABLE。"""
    t = TaobaoAdapter(logged_in=True)
    r = t.collect({"keyword": "牵引绳"})
    assert r.ok is False
    assert r.code == "CAPABILITY_UNAVAILABLE"


def test_fixture_parse():
    import json
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump({"items": [{"title": "测试商品", "price": "34"}]}, f, ensure_ascii=False)
        fname = f.name
    t = TaobaoAdapter(logged_in=True, fixture_path=fname)
    r = t.collect({})
    assert r.ok is True
    assert r.data["items"][0]["title"] == "测试商品"


def test_fake_only_in_test_mode():
    reg_prod = default_registry(test_mode=False)
    assert "fake" not in reg_prod.names()
    reg_test = default_registry(test_mode=True)
    assert "fake" in reg_test.names()


def test_1688_quote_requires_login():
    s = Supplier1688Adapter(logged_in=False)
    r = s.quote({})
    assert r.code == NEEDS_LOGIN
