"""上架与启动工作流：启动检查 → 上架/改价/视频发布计划 → 确认执行。

- 写操作审批后回执绑定账号/商品/作品
- 无真实写能力时显示待人工执行并导出包，不标发布成功
- R22：启动检查与方案形成做后端硬门禁——未核验货源、负价格/库存、
  空 SKU、缺 MAIN/SKU/DETAIL 素材、跨商品/非视频发布输入一律阻塞
"""
from __future__ import annotations

import json

from app.db import connect
from app.services.plans import CostSpec, PlanSpec, create_plan

# R22：上架素材门禁——必须同时具备且已审
REQUIRED_ASSET_ROLES = ("MAIN", "SKU", "DETAIL")


def launch_readiness(db_path, product_id: str) -> dict:
    """新品启动检查清单（后端硬门禁）。返回每项状态与缺口。"""
    conn = connect(db_path)
    try:
        prod = conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
        if not prod:
            raise KeyError("商品不存在")
        offers = conn.execute(
            "SELECT * FROM offers WHERE product_id=?", (product_id,)).fetchall()
        # R22：已核验且有实际价格/库存的货源（未核验/无价格 = 缺证据）
        usable_offers = [dict(o) for o in offers
                         if o["verification_status"] == "VERIFIED"
                         and o["procurement_minor"] is not None
                         and (o["sample_count"] or 0) >= 0]
        # R22：按商品用途关联查素材（asset_usages），必须含 MAIN/SKU/DETAIL 且已审
        asset_rows = conn.execute(
            "SELECT a.*, u.role AS usage_role, u.sku_key AS usage_sku"
            " FROM assets a JOIN asset_usages u ON u.asset_id=a.id"
            " WHERE u.product_id=? AND u.review_status='APPROVED' AND a.kind='IMAGE'",
            (product_id,)).fetchall()
        asset_roles = {r["usage_role"] for r in asset_rows}
        missing_asset_roles = [r for r in REQUIRED_ASSET_ROLES if r not in asset_roles]
        facts = json.loads(prod["facts_json"] or "{}")

        prediction=conn.execute('SELECT * FROM predictions WHERE product_id=? ORDER BY created_at DESC LIMIT 1',(product_id,)).fetchone()
        current=bool(prediction and prediction['decision']=='TEST' and json.loads(prediction['input_json']).get('revision')==prod['revision'])
        missing_skus=[o['sku_key'] for o in usable_offers if not any(a['usage_role']=='SKU' and a['usage_sku']==o['sku_key'] for a in asset_rows)]
        checks = {
            "当前预测": {"ok":current,"note":"需要当前商品版本的 TEST 预测"},
            "SKU图片": {"ok":bool(usable_offers) and not missing_skus,"note":"缺匹配的已审 SKU 图片："+','.join(missing_skus)},
            "商品档案": {"ok": bool(prod["title"]) and bool(prod["category"]),
                         "note": prod["title"] or "缺标题/类目"},
            "规格报价": {"ok": len(usable_offers) > 0,
                         "note": f"{len(usable_offers)} 条已核验货源"
                                 if usable_offers
                                 else "缺已核验且带价格的规格/报价"},
            "已审素材": {"ok": not missing_asset_roles,
                         "note": (f"已具备 {','.join(sorted(asset_roles))}"
                                  if asset_roles else "缺已审素材"),
                         "missing": missing_asset_roles},
        }
        # "已上架"是动作结果不是前置条件（启动检查发生在上架前）
        listings = conn.execute(
            "SELECT * FROM listings WHERE product_id=?", (product_id,)).fetchall()
        checks["已上架"] = {"ok": len(listings) > 0,
                          "note": f"{len(listings)} 条上架记录" if listings else "尚未上架（待启动）"}
        gaps = [k for k, v in checks.items() if not v["ok"] and k != "已上架"]
        return {"product_id": product_id, "checks": checks, "gaps": gaps,
                "ready": len(gaps) == 0}
    finally:
        conn.close()


def plan_listing(db_path, *, product_id: str, platform: str, account_id: str,
                 title: str, price_minor: int, stock: int, sku_map: dict,
                 asset_hashes: list[str], currency: str = "CNY") -> dict:
    """形成上架方案（具体内容：标题/价格/库存/素材版本）。

    R22：负价格/负库存/空 SKU 直接拒绝（422 语义），不落库。
    """
    if price_minor is None or int(price_minor) < 0:
        raise ValueError("price_minor 不能为负")
    if stock is None or int(stock) < 0:
        raise ValueError("stock 不能为负")
    if not sku_map or len(sku_map) == 0:
        raise ValueError("sku_map 不能为空")
    for sku, spec in sku_map.items():
        if not sku:
            raise ValueError("SKU 键不能为空")
        if not isinstance(spec, dict):
            raise ValueError(f"SKU {sku} 规格必须是对象")
        if spec.get("price") is not None and int(spec["price"]) < 0:
            raise ValueError(f"SKU {sku} 价格不能为负")
    spec = PlanSpec(
        product_id=product_id,
        action="LISTING_UPLOAD",
        provider=platform,
        account_id=account_id,
        requires_content=True,
        requires_cost=False,
        payload={
            "kind": "LISTING",
            "platform": platform,
            "title": title,
            "price_minor": int(price_minor),
            "stock": int(stock),
            "sku_map": sku_map,
            "asset_hashes": sorted(asset_hashes),
            "currency": currency,
        },
    )
    return create_plan(db_path, spec=spec)


def plan_price_change(db_path, *, product_id: str, platform: str, account_id: str,
                      external_id: str, new_price_minor: int,
                      reason: str) -> dict:
    """改价方案（需内容审批）。"""
    if new_price_minor is None or int(new_price_minor) < 0:
        raise ValueError("new_price_minor 不能为负")
    spec = PlanSpec(
        product_id=product_id,
        action="PRICE_CHANGE",
        provider=platform,
        account_id=account_id,
        requires_content=True,
        requires_cost=False,
        payload={
            "kind": "PRICE_CHANGE",
            "platform": platform,
            "external_id": external_id,
            "new_price_minor": int(new_price_minor),
            "reason": reason,
        },
    )
    return create_plan(db_path, spec=spec)


def plan_video_publish(db_path, *, product_id: str, platform: str, account_id: str,
                       video_asset_id: str, title: str, topics: list[str],
                       estimated_cost: str | None = None) -> dict:
    """视频发布方案（内容审批；投放另批）。

    R22：视频素材必须存在、kind=VIDEO 且归属当前商品；否则阻塞。
    """
    conn = connect(db_path)
    try:
        asset = conn.execute(
            "SELECT id, kind, review_status FROM assets WHERE id=?",
            (video_asset_id,)).fetchone()
        if asset is None:
            raise ValueError("video_asset_id 不存在")
        if asset["kind"] != "VIDEO":
            raise ValueError("素材不是视频，禁止作为视频发布输入")
        usage = conn.execute(
            "SELECT review_status FROM asset_usages WHERE asset_id=? AND product_id=?",
            (video_asset_id, product_id)).fetchone()
        if usage is None:
            raise ValueError("视频素材不属于该商品，禁止跨商品发布")
        if usage["review_status"] != "APPROVED":
            raise ValueError("视频素材未审阅通过，禁止发布")
    finally:
        conn.close()
    spec = PlanSpec(
        product_id=product_id,
        action="VIDEO_PUBLISH",
        provider=platform,
        account_id=account_id,
        requires_content=True,
        requires_cost=False,
        payload={
            "kind": "VIDEO_PUBLISH",
            "platform": platform,
            "video_asset_id": video_asset_id,
            "title": title,
            "topics": topics,
        },
    )
    return create_plan(db_path, spec=spec)


def record_listing_result(db_path, *, product_id: str, platform: str,
                          account_id: str, external_id: str, content: dict) -> dict:
    """执行回执后登记 listing（外部商品 id 与批准版本一致）。"""
    conn = connect(db_path)
    try:
        # 最新 revision
        latest = conn.execute(
            "SELECT COALESCE(MAX(revision),0) r FROM listings"
            " WHERE platform=? AND account_id=? AND external_id=?",
            (platform, account_id, external_id)).fetchone()
        rev = latest["r"] + 1
        from app.domain.evidence import canonical_json
        conn.execute(
            "INSERT INTO listings(id, product_id, platform, account_id, external_id,"
            " revision, content_json, created_at) VALUES(?,?,?,?,?,?,?,datetime('now'))",
            (str(__import__("uuid").uuid4()), product_id, platform, account_id,
             external_id, rev, canonical_json(content)),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM listings WHERE platform=? AND account_id=? AND external_id=?"
            " ORDER BY revision DESC LIMIT 1",
            (platform, account_id, external_id)).fetchone()
        return dict(row)
    finally:
        conn.close()
