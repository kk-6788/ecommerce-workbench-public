"""素材测试：真实解码校验、格式探测、用途关联、文件 hash、混款淘汰、路径穿越、URL 安全。"""
from __future__ import annotations

import pytest

from app.db import init_db
from app.services import media
from app.services.products import create_product
from tests.conftest import make_jpeg_bytes, make_png_bytes, make_webp_bytes


@pytest.fixture
def ctx(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    db = d / "app.db"
    init_db(db)
    assets = d / "assets"
    assets.mkdir()
    prod = create_product(db, title="素材商品", category="X", strategy_mode="PUHUO")
    return {"db": db, "assets": assets, "prod": prod}


def test_store_png_detected(ctx):
    a = media.store_asset(ctx["db"], ctx["assets"], product_id=ctx["prod"]["id"],
                          kind="IMAGE", role="MAIN", data=make_png_bytes(), filename="a.png")
    assert a["mime_type"] == "image/png"
    assert a["review_status"] == "PENDING"
    assert len(a["usages"]) == 1


def test_webp_disguised_as_jpg(ctx):
    """内容=WebP 但扩展名=.jpg：按内容识别真实格式。"""
    a = media.store_asset(ctx["db"], ctx["assets"], product_id=ctx["prod"]["id"],
                          kind="IMAGE", role="DETAIL", data=make_webp_bytes(), filename="fake.jpg")
    assert a["mime_type"] == "image/webp"


def test_corrupt_png_rejected(ctx):
    """R20：仅带 PNG 头加无效字节的文件必须拒绝（实际解码验证）。"""
    with pytest.raises(ValueError):
        media.store_asset(ctx["db"], ctx["assets"], product_id=ctx["prod"]["id"],
                          kind="IMAGE", role="MAIN",
                          data=b"\x89PNG\r\n\x1a\n" + b"\x00" * 100, filename="bad.png")


def test_svg_rejected(ctx):
    """R20：SVG 等活性内容拒收，不得同源入库。"""
    with pytest.raises(ValueError):
        media.store_asset(ctx["db"], ctx["assets"], product_id=ctx["prod"]["id"],
                          kind="IMAGE", role="MAIN",
                          data=b'<svg onload="alert(1)"></svg>', filename="x.svg")


def test_path_traversal_rejected(ctx):
    with pytest.raises(ValueError):
        media.store_asset(ctx["db"], ctx["assets"], product_id=ctx["prod"]["id"],
                          kind="IMAGE", role="MAIN", data=make_png_bytes(),
                          filename="../evil.png")


def test_empty_file_rejected(ctx):
    with pytest.raises(ValueError):
        media.store_asset(ctx["db"], ctx["assets"], product_id=ctx["prod"]["id"],
                          kind="IMAGE", role="MAIN", data=b"", filename="a.png")


def test_same_hash_dedup_on_disk(ctx):
    data = make_png_bytes()
    a1 = media.store_asset(ctx["db"], ctx["assets"], product_id=ctx["prod"]["id"],
                           kind="IMAGE", role="REFERENCE", data=data, filename="a.png")
    a2 = media.store_asset(ctx["db"], ctx["assets"], product_id=ctx["prod"]["id"],
                           kind="IMAGE", role="REFERENCE", data=data, filename="a.png")
    assert a1["id"] == a2["id"]           # 文件对象按 hash 去重
    assert a1["file_hash"] == a2["file_hash"]
    # 磁盘只存一份
    p = media.resolve_asset_path(ctx["assets"], a1)
    assert p.exists()


def test_same_bytes_multiple_roles_distinct_usages(ctx):
    """R21：同字节文件可被多个用途/SKU 引用且身份正确。"""
    data = make_png_bytes()
    a_main = media.store_asset(ctx["db"], ctx["assets"], product_id=ctx["prod"]["id"],
                               kind="IMAGE", role="MAIN", data=data, filename="a.png")
    a_sku = media.store_asset(ctx["db"], ctx["assets"], product_id=ctx["prod"]["id"],
                              kind="IMAGE", role="SKU", data=data, filename="a.png",
                              sku_key="A-blue")
    assert a_main["id"] == a_sku["id"]
    roles = sorted(u["role"] + ":" + (u["sku_key"] or "") for u in a_sku["usages"])
    assert "MAIN:" in roles and "SKU:A-blue" in roles


def test_mixed_kind_rejected_and_review_note(ctx):
    """混款淘汰：REJECTED + 理由保存。"""
    a = media.store_asset(ctx["db"], ctx["assets"], product_id=ctx["prod"]["id"],
                          kind="IMAGE", role="SKU", data=make_png_bytes(), filename="s.png")
    updated = media.mark_review(ctx["db"], a["id"], "REJECTED", "混款：包含其他品牌商品")
    assert updated["review_status"] == "REJECTED"
    assert updated["review_note"] == "混款：包含其他品牌商品"


def test_url_localhost_rejected():
    with pytest.raises(ValueError):
        media.validate_url_safety("http://127.0.0.1:8510/admin")
    with pytest.raises(ValueError):
        media.validate_url_safety("http://localhost/api")
    with pytest.raises(ValueError):
        media.validate_url_safety("http://169.254.169.254/latest/meta-data")
    with pytest.raises(ValueError):
        media.validate_url_safety("http://[::1]:8510/admin")
    with pytest.raises(ValueError):
        media.validate_url_safety("http://192.168.1.10/x")
    with pytest.raises(ValueError):
        media.validate_url_safety("file:///tmp/x.png")
    with pytest.raises(ValueError):
        media.validate_url_safety("http://internal.evil.test/x.png")  # 非白名单域名
    media.validate_url_safety("https://alicdn.com/img/1.jpg")  # 正常外链通过
    media.validate_url_safety("https://img.example.com/a.png")  # 白名单子域通过


def test_asset_not_mislinked_across_products(ctx):
    p2 = create_product(ctx["db"], title="B", category="Y", strategy_mode="PUHUO")
    a = media.store_asset(ctx["db"], ctx["assets"], product_id=ctx["prod"]["id"],
                          kind="IMAGE", role="MAIN", data=make_png_bytes(), filename="m.png")
    from app.services.products import get_product
    assert len(get_product(ctx["db"], ctx["prod"]["id"])["assets"]) == 1
    assert len(get_product(ctx["db"], p2["id"])["assets"]) == 0
    # B 商品显式引用同一字节文件 → 只出现在 B 的用途下，不污染 A
    a2 = media.store_asset(ctx["db"], ctx["assets"], product_id=p2["id"],
                           kind="IMAGE", role="MAIN", data=make_png_bytes(), filename="m.png")
    assert a2["id"] == a["id"]
    assert len(get_product(ctx["db"], ctx["prod"]["id"])["assets"]) == 1
    assert len(get_product(ctx["db"], p2["id"])["assets"]) == 1


def test_is_synthetic_persisted(ctx):
    """R23：is_synthetic 落库，生产聚合可排除合成数据。"""
    a = media.store_asset(ctx["db"], ctx["assets"], product_id=ctx["prod"]["id"],
                          kind="IMAGE", role="OUTPUT", data=make_png_bytes(),
                          filename="gen.png", is_synthetic=True)
    assert a["is_synthetic"] == 1


def test_jpeg_detected(ctx):
    a = media.store_asset(ctx["db"], ctx["assets"], product_id=ctx["prod"]["id"],
                          kind="IMAGE", role="MAIN", data=make_jpeg_bytes(), filename="b.jpg")
    assert a["mime_type"] == "image/jpeg"
