"""Bounded table ingestion: preview, explicit mapping, atomic commit and provenance."""
import csv
import hashlib
import io
import json
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zipfile import ZipFile, BadZipFile

from app.db import connect

LIMIT = 4 * 1024 * 1024
KINDS = {
    "products": {"label": "商品资料", "fields": {
        "title": ["商品标题", "商品名称", "标题", "title"], "category": ["品类", "类目", "category"],
        "strategy_mode": ["经营模式", "模式", "strategy_mode"]}, "required": ["title", "category"]},
    "suppliers": {"label": "货源清单", "fields": {
        "product_id": ["商品ID", "product_id"], "supplier": ["供应商", "商家", "supplier"],
        "sku_key": ["SKU", "规格编码", "sku_key"], "price": ["拿货价", "采购价", "price"],
        "freight": ["运费", "freight"]}, "required": ["product_id", "supplier", "sku_key"]},
    "market": {"label": "选品／市场数据", "fields": {
        "keyword": ["关键词", "搜索词", "keyword"], "category": ["品类组", "品类", "category"],
        "layer": ["分层", "评分", "layer"]}, "required": ["keyword", "category"]},
    "orders": {"label": "订单明细", "fields": {
        "platform": ["平台", "platform"], "order_id": ["订单号", "订单编号", "order_id"],
        "line_id": ["订单行号", "明细ID", "line_id"], "title": ["商品名称", "商品标题", "title"], "amount": ["实付金额", "金额", "amount"],
        "quantity": ["数量", "quantity"], "date": ["订单日期", "日期", "date"]},
        "required": ["platform", "order_id", "amount"]},
    "sales": {"label": "销售汇总", "fields": {
        "platform": ["平台", "platform"], "date": ["日期", "date"],
        "title": ["商品名称", "商品标题", "title"], "amount": ["销售额", "成交金额", "amount"],
        "quantity": ["订单数", "销量", "quantity"]}, "required": ["platform", "date", "amount"]},
}

def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

def now():
    return datetime.now(timezone.utc).isoformat()

def parse(filename, data):
    if not data or len(data) > LIMIT:
        raise ValueError("请选择非空表格，文件不能超过 4 MB")
    suffix = Path(filename).suffix.lower()
    tables = []
    if suffix in (".csv", ".tsv"):
        for encoding in ("utf-8-sig", "gb18030"):
            try:
                text = data.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ValueError("文件编码无法识别，请另存为 UTF-8 CSV")
        try:
            grid = list(csv.reader(io.StringIO(text), delimiter="\t" if suffix == ".tsv" else ",", strict=True))
        except csv.Error:
            raise ValueError("表格格式错误，请检查分隔符与引号")
        tables = [("数据表", grid)]
    elif suffix == ".xlsx":
        try:
            with ZipFile(io.BytesIO(data)) as z:
                if len(z.infolist()) > 1000 or sum(x.file_size for x in z.infolist()) > 24 * 1024 * 1024:
                    raise ValueError("Excel 解压体积过大，请拆分文件")
                # Validate real cell coordinates, not the workbook's optional dimension hint.
                import re
                from xml.etree import ElementTree as ET
                for entry in z.infolist():
                    if not entry.filename.endswith(".xml"):
                        continue
                    xml = z.read(entry)
                    check = xml.replace(bytes([0]), b"").upper()
                    if b"<!DOCTYPE" in check or b"<!ENTITY" in check:
                        raise ValueError("Excel 含不支持的 XML 声明")
                    for _, node in ET.iterparse(io.BytesIO(xml), events=("end",)):
                        local_tag = node.tag.rsplit("}", 1)[-1]
                        if local_tag == "row":
                            row_ref = node.get("r", "")
                            if not row_ref.isdigit() or not 1 <= int(row_ref) <= 1001 or len(node) > 60:
                                raise ValueError("Excel 行号无效或超出行列限制")
                        if local_tag == "c":
                            if not node.get("r"):
                                raise ValueError("Excel 缺少单元格坐标，请重新另存文件")
                            match = re.fullmatch(r"([A-Z]+)([1-9][0-9]*)", node.get("r"))
                            if not match:
                                raise ValueError("Excel 单元格坐标无效")
                            col = 0
                            for letter in match[1]:
                                col = col * 26 + ord(letter) - 64
                            if col > 60 or int(match[2]) > 1001:
                                raise ValueError("每张表最多 1000 条数据、60 列")
                        node.clear()
            from openpyxl import load_workbook
            book = load_workbook(io.BytesIO(data), read_only=True, data_only=False, keep_links=False)
            try:
                if len(book.worksheets) > 20:
                    raise ValueError("最多支持 20 个工作表")
                for sheet in book:
                    if (sheet.max_row or 0) > 1001 or (sheet.max_column or 0) > 60:
                        raise ValueError("每张表最多 1000 条数据、60 列")
                    grid = []
                    sheet.reset_dimensions()
                    for cells in sheet.iter_rows(max_row=1001, max_col=60):
                        if len(grid) > 1000 or len(cells) > 60:
                            raise ValueError("每张表最多 1000 条数据、60 列")
                        if any(c.data_type == "f" for c in cells):
                            raise ValueError("表格含公式，请复制并粘贴为值后再导入")
                        values = [str(c.value) if c.value is not None else "" for c in cells]
                        while values and not values[-1]:
                            values.pop()
                        grid.append(values)
                    while grid and not grid[-1]:
                        grid.pop()
                    tables.append((sheet.title, grid))
            finally:
                book.close()
        except ImportError:
            raise ValueError("当前环境尚未安装 Excel 读取组件，请先使用 CSV／TSV")
        except ValueError:
            raise
        except Exception:
            raise ValueError("Excel 文件损坏或格式不正确")
    else:
        raise ValueError("支持 .xlsx、.csv、.tsv；旧版 .xls 请另存为 .xlsx")
    result = []
    for name, grid in tables:
        if not grid or not any(any(str(v).strip() for v in row) for row in grid):
            continue
        if len(grid) > 1001 or len(grid[0]) > 60:
            raise ValueError("每张表最多 1000 条数据、60 列")
        headers = [str(x).strip() for x in grid[0]]
        if any(not h for h in headers) or len(headers) != len(set(headers)):
            raise ValueError("首行必须是非空、不重复的列名")
        rows = []
        for n, values in enumerate(grid[1:], 2):
            if not any(str(v).strip() for v in values):
                continue
            if len(values) > len(headers) or any(len(str(v)) > 4000 for v in values):
                raise ValueError(f"第 {n} 行列数不匹配或单元格超过 4000 字符")
            values = list(values) + [""] * (len(headers) - len(values))
            rows.append({"number": n, "values": dict(zip(headers, map(str, values)))})
        if rows:
            result.append({"name": name, "headers": headers, "rows": rows})
    if not result:
        raise ValueError("表格没有可导入的数据")
    return result

def mapping_for(kind, headers):
    return {key: next((h for h in headers if h in aliases), "") for key, aliases in KINDS[kind]["fields"].items()}

def prepare(db, owner, filename, data, kind):
    if kind not in KINDS:
        raise ValueError("请选择导入类型")
    tables = parse(filename, data)
    batch = str(uuid.uuid4())
    c = connect(db)
    try:
        c.execute("INSERT INTO table_imports(id,owner_id,filename,kind,source_hash,payload_json,created_at) VALUES(?,?,?,?,?,?,?)",
                  (batch, owner, Path(filename.replace("\\", "/")).name[:200], kind, hashlib.sha256(data).hexdigest(), dump(tables), now()))
        c.commit()
    finally:
        c.close()
    return {"id": batch, "tables": [{"name": t["name"], "headers": t["headers"], "count": len(t["rows"]),
            "preview": t["rows"][:10], "mapping": mapping_for(kind, t["headers"])} for t in tables]}

def normalize(kind, row, mapping):
    out = {key: row.get(mapping.get(key, ""), "").strip() for key in KINDS[kind]["fields"]}
    missing = [KINDS[kind]["fields"][k][0] for k in KINDS[kind]["required"] if not out[k]]
    if missing:
        raise ValueError("缺少：" + "、".join(missing))
    for key, value in out.items():
        if len(value) > (200 if key in ("title", "supplier", "keyword") else 100):
            raise ValueError("字段过长：" + key)
    if kind == "products":
        out["strategy_mode"] = {"铺货": "PUHUO", "研发": "RND", "": "PUHUO"}.get(out["strategy_mode"], out["strategy_mode"])
        if out["strategy_mode"] not in ("PUHUO", "RND"):
            raise ValueError("模式只能为铺货或研发")
    for key in ("price", "freight", "amount"):
        if key in out and out[key]:
            try:
                amount = Decimal(out[key])
                if not amount.is_finite() or amount < 0 or amount > 100000000 or amount.as_tuple().exponent < -2:
                    raise ValueError("金额需为非负数字，最多两位小数")
                out[key + "_minor"] = int(amount * 100)
            except InvalidOperation:
                raise ValueError("金额格式错误，请使用元为单位的数字")
    if out.get("quantity"):
        if not out["quantity"].isdigit() or int(out["quantity"]) > 100000000:
            raise ValueError("数量必须为非负整数")
        out["quantity"] = int(out["quantity"])
    if out.get("date"):
        try:
            datetime.fromisoformat(out["date"])
        except ValueError:
            raise ValueError("日期请使用 YYYY-MM-DD 或 ISO 日期时间")
    return out

def _business_identity(kind, values, item, sheet, row_number):
    """EC-04: 按导入类型定义业务身份（跨文件去重键），不再默认绑死文件哈希+行号。

    - orders: 平台+订单号+行号（原有行为保留）
    - sales: 平台+日期+商品名（同日同平台同商品的汇总行=同一记录，重导是更新不是新增）
    - suppliers: 商品+供应商+SKU（同货源重导=同一报价记录，价格变化走冲突提示人工核对）
    - products/market: 无稳定业务键（标题/关键词非唯一标识），保留文件+行号身份，
      但文件哈希改为文件名，避免同一文件重传（哈希因微调字节变化）造成整批重复。
    """
    if kind == "orders":
        return {k: values[k] for k in ("platform", "order_id", "line_id")}
    if kind == "sales":
        return {"platform": values["platform"], "date": values["date"], "title": values["title"]}
    if kind == "suppliers":
        return {"product_id": values["product_id"], "supplier": values["supplier"], "sku_key": values["sku_key"]}
    return {"file": item["filename"], "sheet": sheet, "row": row_number}


def commit(db, owner, batch, sheet, mapping):
    c = connect(db)
    try:
        c.execute("BEGIN IMMEDIATE")
        item = c.execute("SELECT * FROM table_imports WHERE id=? AND owner_id=?", (batch, owner)).fetchone()
        if not item:
            raise ValueError("导入记录不存在")
        if item["state"] == "IMPORTED":
            return json.loads(item["report_json"])
        if datetime.fromisoformat(item["created_at"]) < datetime.now(timezone.utc) - timedelta(hours=24):
            raise ValueError("预览已过期，请重新上传")
        tables = json.loads(item["payload_json"])
        table = next((t for t in tables if t["name"] == sheet), None)
        if not table:
            raise ValueError("请选择有效工作表")
        kind = item["kind"]
        if set(mapping) - set(KINDS[kind]["fields"]) or any(v and v not in table["headers"] for v in mapping.values()):
            raise ValueError("列映射无效")
        used = [v for v in mapping.values() if v]
        if len(used) != len(set(used)):
            raise ValueError("同一列不能映射到多个字段")
        valid, errors = [], []
        for row in table["rows"]:
            try:
                values = normalize(kind, row["values"], mapping)
                if kind == "suppliers" and not c.execute("SELECT 1 FROM products WHERE id=?", (values["product_id"],)).fetchone():
                    raise ValueError("商品 ID 不存在，请先导入商品")
                valid.append((row, values))
            except ValueError as exc:
                errors.append({"row": row["number"], "message": str(exc)})
        if errors:
            return {"imported": 0, "duplicates": 0, "errors": errors, "state": "INVALID"}
        count, duplicates = 0, 0
        for row, values in valid:
            # EC-04: 业务身份由 _business_identity 按类型定义
            identity = _business_identity(kind, values, item, sheet, row["number"])
            key = hashlib.sha256(dump(identity).encode()).hexdigest()
            existing = c.execute("SELECT normalized_json,raw_json FROM imported_rows WHERE owner_id=? AND kind=? AND row_hash=?", (owner, kind, key)).fetchone()
            if existing and (existing["normalized_json"] != dump(values) or existing["raw_json"] != dump(row["values"])):
                raise ValueError(f"第 {row['number']} 行与已导入记录冲突，本批未写入。订单多行请填写不同的订单行号；修改记录请先核对，系统不会自动覆盖。")
            if existing:
                duplicates += 1
                continue
            target = str(uuid.uuid4())
            stamp = now()
            if kind == "products":
                facts = dump({"import_source": {"filename": item["filename"], "row": row["number"], "raw": row["values"]}})
                c.execute("INSERT INTO products(id,title,category,strategy_mode,stage,facts_json,revision,is_synthetic,created_at,updated_at) VALUES(?,?,?,?,'DISCOVERY',?,1,0,?,?)",
                          (target, values["title"], values["category"], values["strategy_mode"], facts, stamp, stamp))
            elif kind == "suppliers":
                c.execute("INSERT INTO offers(id,product_id,supplier,sku_key,specification_json,procurement_minor,freight_minor,currency,verification_status,created_at) VALUES(?,?,?,?,?,?,?,'CNY','UNVERIFIED',?)",
                          (target, values["product_id"], values["supplier"], values["sku_key"], dump(row["values"]), values.get("price_minor"), values.get("freight_minor"), stamp))
            elif kind == "market":
                c.execute("INSERT INTO opportunities(id,keyword,category_group,source_hash,source_row,recorded_layer,disposition,raw_json,missing_json,created_at) VALUES(?,?,?,?,?,?,'待核验',?,'[]',?)",
                          (target, values["keyword"], values["category"], batch, row["number"], values["layer"], dump(row["values"]), stamp))
            else:
                target = None
            c.execute("INSERT INTO imported_rows(id,import_id,owner_id,kind,row_hash,row_number,raw_json,normalized_json,target_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (str(uuid.uuid4()), batch, owner, kind, key, row["number"], dump(row["values"]), dump(values), target, stamp))
            count += 1
        report = {"imported": count, "duplicates": duplicates, "errors": [], "state": "IMPORTED"}
        c.execute("UPDATE table_imports SET state='IMPORTED',report_json=?,payload_json='[]' WHERE id=?", (dump(report), batch))
        c.commit()
        return report
    finally:
        c.close()

def history(db, owner):
    c = connect(db)
    try:
        return [dict(x) for x in c.execute("SELECT id,filename,kind,state,created_at,report_json FROM table_imports WHERE owner_id=? ORDER BY created_at DESC LIMIT 30", (owner,))]
    finally:
        c.close()

def records(db, owner, kind):
    if kind not in KINDS:
        raise ValueError("导入类型无效")
    c = connect(db)
    try:
        return [dict(x) for x in c.execute("SELECT row_number,raw_json,normalized_json,target_id FROM imported_rows WHERE owner_id=? AND kind=? ORDER BY created_at DESC LIMIT 200", (owner, kind))]
    finally:
        c.close()


def export_rows(db, owner, kind):
    if kind not in KINDS:
        raise ValueError("导出类型无效")
    c = connect(db)
    try:
        if kind == "products":
            return [dict(x) for x in c.execute("SELECT id AS 商品ID,title AS 商品标题,category AS 品类,strategy_mode AS 经营模式,stage AS 阶段,facts_json AS 原始资料 FROM products ORDER BY created_at")]
        if kind == "suppliers":
            rows = [dict(x) for x in c.execute("SELECT id AS 货源ID,product_id AS 商品ID,supplier AS 供应商,sku_key AS SKU,procurement_minor,freight_minor,verification_status AS 核验状态,specification_json AS 原始资料 FROM offers ORDER BY created_at")]
            for row in rows:
                price,freight=row.pop("procurement_minor"),row.pop("freight_minor")
                row["拿货价"] = str(Decimal(price)/100) if price is not None else ""
                row["运费"] = str(Decimal(freight)/100) if freight is not None else ""
            return rows
        if kind == "market":
            return [dict(x) for x in c.execute("SELECT id AS 机会ID,keyword AS 关键词,category_group AS 品类组,recorded_layer AS 分层,disposition AS 核验状态,raw_json AS 原始资料 FROM opportunities ORDER BY created_at")]
        rows=[]
        for row in c.execute("SELECT normalized_json,raw_json FROM imported_rows WHERE owner_id=? AND kind=? ORDER BY created_at",(owner,kind)):
            values=json.loads(row["normalized_json"])
            rows.append({**{aliases[0]:values.get(key,"") for key,aliases in KINDS[kind]["fields"].items()},"原始资料":row["raw_json"]})
        return rows
    finally:
        c.close()
