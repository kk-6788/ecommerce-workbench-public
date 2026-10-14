"""商品档案与证据服务：关键词→商品→SKU/货源/链接关联；revision 冲突检测。"""
from __future__ import annotations

import json
import uuid

from app.db import connect
from app.domain.evidence import (
    EvidenceKind, EvidenceRecord, canonical_json, content_hash, utcnow_iso,
)
from app.domain.schemas import validate_json_obj


def create_product(db_path, *, title: str, category: str, strategy_mode: str,
                   opportunity_id: str | None = None, batch_id: str | None = None,
                   facts: dict | None = None, is_synthetic: bool = False) -> dict:
    if strategy_mode not in ("PUHUO", "RND"):
        raise ValueError("strategy_mode 必须是 PUHUO 或 RND")
    facts = facts or {}
    from app.domain.inputs import validate_facts
    validate_facts(facts)
    if not title.strip() or not category.strip():
        raise ValueError("标题/类目不能为空")
    conn = connect(db_path)
    try:
        if opportunity_id:
            opp = conn.execute(
                "SELECT id FROM opportunities WHERE id=?", (opportunity_id,)).fetchone()
            if not opp:
                raise ValueError("opportunity_id 不存在")
        if batch_id:
            b = conn.execute("SELECT id FROM batches WHERE id=?", (batch_id,)).fetchone()
            if not b:
                raise ValueError("batch_id 不存在")
        pid = str(uuid.uuid4())
        now = utcnow_iso()
        conn.execute(
            "INSERT INTO products(id, batch_id, opportunity_id, title, category,"
            " strategy_mode, stage, facts_json, revision, is_synthetic, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,'DISCOVERY',?,1,?,?,?)",
            (pid, batch_id, opportunity_id, title, category, strategy_mode,
             canonical_json(facts), 1 if is_synthetic else 0, now, now),
        )
        conn.commit()
        return get_product(db_path, pid)
    finally:
        conn.close()


def get_product(db_path, product_id: str) -> dict:
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
        if not row:
            raise KeyError("商品不存在")
        out = dict(row)
        out["facts"] = validate_json_obj(out.pop("facts_json"), "facts_json")
        # 关联信息
        out["offers"] = [dict(r) for r in conn.execute(
            "SELECT * FROM offers WHERE product_id=?", (product_id,)).fetchall()]
        for offer in out["offers"]:
            offer["specification"] = json.loads(offer.pop("specification_json"))
        out["evidence"] = [dict(r) for r in conn.execute(
            "SELECT * FROM evidence WHERE product_id=?", (product_id,)).fetchall()]
        out["listings"] = [dict(r) for r in conn.execute(
            "SELECT * FROM listings WHERE product_id=?", (product_id,)).fetchall()]
        out["assets"] = [dict(r) for r in conn.execute(
            "SELECT a.*, u.id AS usage_id,u.role AS usage_role,u.sku_key AS usage_sku,u.review_status AS usage_review_status,u.is_synthetic AS usage_synthetic,u.version AS usage_version FROM assets a"
            " JOIN asset_usages u ON u.asset_id=a.id"
            " WHERE u.product_id=?"
            " ORDER BY u.created_at DESC", (product_id,)).fetchall()]
        for a in out["assets"]:
            a.update(product_id=product_id,role=a["usage_role"],sku_key=a["usage_sku"],review_status=a["usage_review_status"],is_synthetic=a["usage_synthetic"],version=a["usage_version"])
            a["metadata"] = json.loads(a.pop("metadata_json") or "{}")
        out["predictions"] = [dict(r) for r in conn.execute(
            "SELECT * FROM predictions WHERE product_id=?", (product_id,)).fetchall()]
        return out
    finally:
        conn.close()


def update_product(db_path, product_id: str, *, revision: int, facts: dict | None = None,
                   title: str | None = None, category: str | None = None) -> dict:
    """更新商品事实；已绑定预测保持旧快照不变（facts_json 不可覆盖 predictions.input_json）。

    R10：单条 UPDATE ... WHERE id=? AND revision=? 执行比较并交换，检查 rowcount；
    并发同 revision 恰有一次成功，另一次返回 ConflictError，绝不静默覆盖。
    """
    from app.domain.inputs import validate_facts
    if facts is not None: validate_facts(facts)
    if title is not None and not title.strip() or category is not None and not category.strip():
        raise ValueError("标题/类目不能为空")
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT title, category, facts_json, revision FROM products WHERE id=?",
            (product_id,)).fetchone()
        if not row:
            raise KeyError("商品不存在")
        new_facts = canonical_json(facts) if facts is not None else row["facts_json"]
        new_title = title if title is not None else row["title"]
        new_cat = category if category is not None else row["category"]
        new_rev = revision + 1
        cur = conn.execute(
            "UPDATE products SET facts_json=?, title=?, category=?, revision=?, updated_at=?"
            " WHERE id=? AND revision=?",
            (new_facts, new_title, new_cat, new_rev, utcnow_iso(), product_id, revision),
        )
        if cur.rowcount == 0:
            # 冲突或并发下 revision 已变：重新读取给出准确原因
            cur_row = conn.execute(
                "SELECT revision FROM products WHERE id=?", (product_id,)).fetchone()
            if cur_row is None:
                raise KeyError("商品不存在")
            raise ConflictError(
                f"revision 冲突：当前 {cur_row['revision']}，请求 {revision}")
        conn.commit()
        return get_product(db_path, product_id)
    finally:
        conn.close()


class ConflictError(Exception):
    pass


def add_evidence(db_path, *, product_id: str | None, opportunity_id: str | None,
                 source_key: str, source_url: str | None = None,
                 source_path: str | None = None,
                 evidence_kind: str = "FACT", collection_mode: str = "MANUAL",
                 observed_at: str | None = None, content: dict | None = None,
                 is_synthetic: bool = False) -> dict:
    """添加证据。product 与 opportunity 必须存在其一；跨商品证据不能误关联。"""
    if product_id is None and opportunity_id is None:
        raise ValueError("product_id 和 opportunity_id 至少提供一个")
    if evidence_kind not in {k.value for k in EvidenceKind}:
        raise ValueError(f"evidence_kind 无效：{evidence_kind}")
    content = content or {}
    from pathlib import Path
    # A scoped storage key preserves the old UNIQUE constraint and isolates references.
    source_key = content_hash({"product": product_id, "opportunity": opportunity_id, "source": source_key})
    if source_path:
        base = (Path(db_path).parent / "evidence").resolve()
        source = (base / source_path).resolve()
        if not source.is_relative_to(base) or not source.is_file():
            raise ValueError("本地证据必须位于受管 evidence 目录且文件存在")
        import hashlib
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        destination = base / digest[:2] / (digest + source.suffix.lower())
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists(): destination.write_bytes(source.read_bytes())
        source_path = destination.relative_to(base).as_posix()
        content = {**content, "_file": {"sha256": digest, "size": destination.stat().st_size}}
    if observed_at:
        from datetime import datetime
        if datetime.fromisoformat(observed_at).tzinfo is None:
            raise ValueError("observed_at 必须带时区")
    conn = connect(db_path)
    try:
        if product_id:
            p = conn.execute("SELECT id FROM products WHERE id=?", (product_id,)).fetchone()
            if not p:
                raise ValueError("product_id 不存在")
        if opportunity_id:
            o = conn.execute("SELECT id FROM opportunities WHERE id=?", (opportunity_id,)).fetchone()
            if not o:
                raise ValueError("opportunity_id 不存在")
        rec = EvidenceRecord(
            source_key=source_key, source_url=source_url, source_path=source_path,
            evidence_kind=EvidenceKind(evidence_kind),
            collection_mode=collection_mode, observed_at=observed_at, content=content,
            product_id=product_id, opportunity_id=opportunity_id,
        )
        row = rec.to_row(is_synthetic=is_synthetic)
        # R09：幂等检查按商品隔离——同商品同 source_key+source_hash 不重复，
        # 不同商品可各自拥有相同来源标识（避免跨商品误返回）
        if product_id:
            dup = conn.execute(
                "SELECT id FROM evidence WHERE product_id=? AND source_key=? AND source_hash=?",
                (product_id, source_key, row["source_hash"])).fetchone()
        else:
            dup = conn.execute(
                "SELECT id FROM evidence WHERE product_id IS NULL AND source_key=? AND source_hash=?",
                (source_key, row["source_hash"])).fetchone()
        if dup:
            conn.close()
            return get_evidence(db_path, dup["id"])
        eid = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO evidence(id, product_id, opportunity_id, source_url, source_path,"
            " source_key, source_hash, evidence_kind, collection_mode, observed_at,"
            " collected_at, content_json, is_synthetic)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (eid, product_id, opportunity_id, source_url, source_path, source_key,
             row["source_hash"], row["evidence_kind"], row["collection_mode"],
             row["observed_at"], row["collected_at"], row["content_json"],
             row["is_synthetic"]),
        )
        conn.commit()
        return get_evidence(db_path, eid)
    finally:
        conn.close()


def get_evidence(db_path, evidence_id: str) -> dict:
    conn = connect(db_path)
    try:
        row = conn.execute("SELECT * FROM evidence WHERE id=?", (evidence_id,)).fetchone()
        if not row:
            raise KeyError("证据不存在")
        out = dict(row)
        out["content"] = json.loads(out.pop("content_json"))
        return out
    finally:
        conn.close()


def add_offer(db_path, *, product_id: str, supplier: str, sku_key: str,
              specification: dict, procurement_minor: int | None = None,
              freight_minor: int | None = None, currency: str = "CNY",
              sample_count: int | None = None, verification_status: str = "UNVERIFIED",
              evidence_id: str | None = None) -> dict:
    """添加具体规格报价。"""
    if not supplier.strip() or not sku_key.strip(): raise ValueError("供应商/SKU 不能为空")
    for value in (procurement_minor, freight_minor, sample_count):
        if value is not None and (type(value) is not int or value < 0): raise ValueError("报价/样本必须非负整数")
    if verification_status not in ("UNVERIFIED", "VERIFIED", "PENDING", "REJECTED"):
        raise ValueError("核验状态无效")
    conn = connect(db_path)
    try:
        p = conn.execute("SELECT id FROM products WHERE id=?", (product_id,)).fetchone()
        if not p:
            raise ValueError("product_id 不存在")
        # R09：evidence 必须归属同一商品，禁止跨商品引用
        if evidence_id:
            ev = conn.execute(
                "SELECT id, product_id FROM evidence WHERE id=?", (evidence_id,)).fetchone()
            if not ev:
                raise ValueError("evidence_id 不存在")
            if ev["product_id"] != product_id:
                raise ValueError("evidence 不属于该商品，禁止跨商品引用")
        oid = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO offers(id, product_id, supplier, sku_key, specification_json,"
            " procurement_minor, freight_minor, currency, sample_count,"
            " verification_status, evidence_id, created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (oid, product_id, supplier, sku_key, canonical_json(specification),
             procurement_minor, freight_minor, currency, sample_count,
             verification_status, evidence_id, utcnow_iso()),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM offers WHERE id=?", (oid,)).fetchone()
        out = dict(row)
        out["specification"] = json.loads(out.pop("specification_json"))
        return out
    finally:
        conn.close()
