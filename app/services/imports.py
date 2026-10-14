"""基线导入：25 评分词 + 5 规则排除词 → opportunities 表。

- 保留原始行 recorded_layer / recorded_profit / source_hash
- 另写 computed_layer / computed_profit / missing_fields / difference_reason
- 重复导入幂等（source_hash+source_row 唯一）
- 四脚衣=保留观察，不硬淘汰
"""
from __future__ import annotations

import csv
import json
import uuid
from pathlib import Path

from app.db import connect
from app.domain.economics import legacy_margin
from app.domain.legacy_rules import (
    DATA_RECAPTURE,
    HISTORICAL_DATA_RECAPTURE,
    HISTORICAL_EXCLUSIONS,
    HISTORICAL_OBSERVATIONS,
    TierInput,
    grade_tier,
)
from decimal import Decimal as D

# 25 词评分总表 CSV 列名（与 v4.2.2勘误3-25词评分总表.csv 一致）
SCORE_CSV_COLUMNS = [
    "关键词", "品类组", "分层", "实测中位价", "淘宝样本", "头部付款",
    "保守拿货价", "最低拿货价", "到单成本", "拿货比", "中位档利润", "拿货来源",
    "1688样本", "数据置信度", "待供应链核验", "判定依据/可回归条件",
]
EXCLUDE_CSV_COLUMNS = ["关键词", "排除原因", "排除规则"]


def _parse_dec(v: str):
    if v is None or v.strip() == "":
        return None
    try:
        return D(v.strip())
    except Exception:
        return None


def _parse_int(v: str):
    if v is None or v.strip() == "":
        return None
    try:
        return int(v.strip())
    except Exception:
        return None


def _source_hash(source_dir: Path) -> str:
    """对源目录的关键 CSV 做 hash，标识一批基线。"""
    import hashlib
    h = hashlib.sha256()
    for name in ["v4.2.2勘误3-25词评分总表.csv", "v4.2.2勘误3-5词规则排除表.csv"]:
        p = source_dir / name
        if p.exists():
            h.update(p.read_bytes())
    return h.hexdigest()


def _load_score_rows(source_dir: Path) -> list[dict]:
    p = source_dir / "v4.2.2勘误3-25词评分总表.csv"
    with open(p, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return [row for row in reader]


def _load_exclude_rows(source_dir: Path) -> list[dict]:
    p = source_dir / "v4.2.2勘误3-5词规则排除表.csv"
    with open(p, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return [row for row in reader]


def _procurement_source(source_text: str) -> str:
    """从"拿货来源"列推导证据类型（R11/R12）。
    - 参考同类价/非直接证据 → PROXY（代理，不产出确定利润）
    - 原始记录缺失/待补采 → UNKNOWN
    - 其余（1688搜索摘要/采集/v3摘要价）→ FACT（直接证据，可核验）
    """
    t = source_text or ""
    if "参考同类" in t or "非直接证据" in t or "参考" in t:
        return "PROXY"
    if "缺失" in t or "待补采" in t or "无此词" in t:
        return "UNKNOWN"
    return "FACT" if any(token in t for token in ("1688", "采集", "实测", "v3摘要")) else "UNKNOWN"


def _compute_tier(row: dict) -> dict:
    """用现有字段重算分层。不篡改原 recorded_layer。"""
    kw = row.get("关键词", "").strip()
    median = _parse_dec(row.get("实测中位价"))
    pays = _parse_int(row.get("头部付款"))
    cons = _parse_dec(row.get("保守拿货价"))
    lowest = _parse_dec(row.get("最低拿货价"))
    supply = _parse_int(row.get("1688样本"))
    taobao_n = _parse_int(row.get("淘宝样本"))
    group = row.get("品类组", "").strip()
    verify = str(row.get("待供应链核验", "")).strip() in ("是", "1", "true", "True")
    source = _procurement_source(row.get("拿货来源", ""))

    inp = TierInput(
        keyword=kw, category_group=group, median_price=median,
        taobao_samples=taobao_n, top_payments=pays,
        conservative_procurement=cons, lowest_procurement=lowest,
        supply_samples=supply, needs_supply_verify=verify,
        data_confidence=row.get("数据置信度", ""),
        procurement_source=source,
    )
    r = grade_tier(inp)
    return {
        "computed_layer": r.layer,
        "computed_profit": str(r.cm) if r.cm is not None else None,
        "missing_fields": r.missing_fields,
        "rule_hits": r.rule_hits,
        "reason": r.reason,
        "block_execution": r.block_execution,
        "needs_evidence": r.needs_evidence,
    }


def import_baseline(db_path, source_dir: Path) -> dict:
    """导入 30 词基线。返回报告。重复导入幂等。"""
    source_dir = Path(source_dir)
    src_hash = _source_hash(source_dir)
    conn = connect(db_path)
    try:
        report = {"total_opportunities": 0, "duplicates_skipped": 0, "errors": [],
                  "source_hash": src_hash}

        # 先看是否已有同源导入
        existing = conn.execute(
            "SELECT source_hash FROM opportunities WHERE source_hash=? LIMIT 1",
            (src_hash,),
        ).fetchone()

        # 评分词 25
        try:
            score_rows = _load_score_rows(source_dir)
        except FileNotFoundError as e:
            report["errors"].append(f"缺少评分表：{e}")
            score_rows = []

        # R12：先跑全池价格异常检测（grade_pool 同轮迭代），再逐行导入；
        # 异常词在导入时直接标 DATA_RECAPTURE，不进入正常分层
        try:
            from app.domain.legacy_rules import TierInput, grade_pool
            pool_inputs = []
            for row in score_rows:
                kw = row.get("关键词", "").strip()
                if not kw:
                    continue
                pool_inputs.append(TierInput(
                    keyword=kw,
                    category_group=row.get("品类组", "").strip(),
                    median_price=_parse_dec(row.get("实测中位价")),
                    taobao_samples=_parse_int(row.get("淘宝样本")),
                    top_payments=_parse_int(row.get("头部付款")),
                    conservative_procurement=_parse_dec(row.get("保守拿货价")),
                    lowest_procurement=_parse_dec(row.get("最低拿货价")),
                    supply_samples=_parse_int(row.get("1688样本")),
                    needs_supply_verify=str(row.get("待供应链核验", "")).strip()
                    in ("是", "1", "true", "True"),
                    procurement_source=_procurement_source(row.get("拿货来源", "")),
                ))
            pool_results = {r.keyword: r for r in grade_pool(pool_inputs)}
        except Exception as e:  # 异常检测失败不应阻止导入，降级为逐行
            report["errors"].append(f"价格异常检测失败：{e}")
            pool_results = {}

        for idx, row in enumerate(score_rows):
            kw = row.get("关键词", "").strip()
            if not kw:
                continue
            source_row = idx + 1
            source_key = f"score-csv:row{source_row}"
            raw_json = json.dumps({k: row.get(k, "") for k in SCORE_CSV_COLUMNS},
                                  ensure_ascii=False)
            import hashlib
            row_hash = hashlib.sha256(raw_json.encode("utf-8")).hexdigest()
            uniq = f"{src_hash}:{source_row}"

            dup = conn.execute(
                "SELECT 1 FROM opportunities WHERE source_hash=? AND source_row=?",
                (src_hash, source_row),
            ).fetchone()
            if dup:
                report["duplicates_skipped"] += 1
                continue

            computed = _compute_tier(row)
            # R12：批次异常检测结果覆盖单词重算（异常词强制 DATA_RECAPTURE）
            pool_r = pool_results.get(kw)
            if pool_r is not None and pool_r.layer == "数据待重抓":
                computed = {
                    "computed_layer": "数据待重抓",
                    "computed_profit": None,
                    "missing_fields": computed.get("missing_fields", []),
                    "rule_hits": ["价格异常", "数据待重抓"],
                    "reason": pool_r.reason,
                    "block_execution": True,
                    "needs_evidence": False,
                }
            recorded_layer = row.get("分层", "").strip() or "未定级"
            conn.execute(
                "INSERT INTO opportunities(id, keyword, category_group, source_hash,"
                " source_row, recorded_layer, disposition, raw_json, missing_json, created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,datetime('now'))",
                (
                    str(uuid.uuid4()), kw, row.get("品类组", "").strip(),
                    src_hash, source_row, recorded_layer,
                    computed["computed_layer"],
                    raw_json,
                    json.dumps(computed["missing_fields"]),
                ),
            )
            report["total_opportunities"] += 1

        # 排除/观察词 5
        try:
            exclude_rows = _load_exclude_rows(source_dir)
        except FileNotFoundError as e:
            report["errors"].append(f"缺少排除表：{e}")
            exclude_rows = []

        for idx, row in enumerate(exclude_rows):
            kw = row.get("关键词", "").strip()
            if not kw:
                continue
            source_row = 100 + idx + 1  # 排除表独立行号空间
            raw_json = json.dumps({k: row.get(k, "") for k in EXCLUDE_CSV_COLUMNS},
                                  ensure_ascii=False)
            import hashlib
            row_hash = hashlib.sha256(raw_json.encode("utf-8")).hexdigest()

            dup = conn.execute(
                "SELECT 1 FROM opportunities WHERE source_hash=? AND source_row=?",
                (src_hash, source_row),
            ).fetchone()
            if dup:
                report["duplicates_skipped"] += 1
                continue

            # 四脚衣=保留观察；其余按历史排除原因
            if kw in HISTORICAL_OBSERVATIONS:
                disposition = "保留观察"
            else:
                disposition = "历史排除"
            conn.execute(
                "INSERT INTO opportunities(id, keyword, category_group, source_hash,"
                " source_row, recorded_layer, disposition, raw_json, missing_json, created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,datetime('now'))",
                (
                    str(uuid.uuid4()), kw, row.get("品类组", "").strip() or "未分组",
                    src_hash, source_row, disposition, disposition,
                    raw_json, "[]",
                ),
            )
            report["total_opportunities"] += 1

        from app.domain.evidence import utcnow_iso
        for record in conn.execute("SELECT * FROM opportunities WHERE source_hash=? AND source_row<100",(src_hash,)).fetchall():
            calculated=_compute_tier(json.loads(record["raw_json"]))
            pool_result=pool_results.get(record["keyword"])
            if pool_result:
                calculated.update(computed_layer=pool_result.layer,computed_profit=str(pool_result.cm) if pool_result.cm is not None else None,rule_hits=pool_result.rule_hits,reason=pool_result.reason,block_execution=pool_result.block_execution,needs_evidence=pool_result.needs_evidence)
            calculated["recorded_layer"]=record["recorded_layer"]
            calculated["differs_from_recorded"]=calculated["computed_layer"]!=record["recorded_layer"]
            conn.execute("INSERT OR REPLACE INTO opportunity_computations VALUES(?,?,?,?)",(record["id"],json.dumps(calculated,ensure_ascii=False),"v4.2.2-r2",utcnow_iso()))
        conn.commit()
        return report
    finally:
        conn.close()


def verify_baseline(db_path, source_hash=None) -> dict:
    """核验基线：30 词、25 评分、5 排除/观察、9 品类组、8 零供应样本。

    R33：输出 PASS/FAIL 与明细；失败时调用方以非零退出。
    未知与 0 分别统计（不再把空字符串当 0）。
    """
    conn = connect(db_path)
    try:
        if source_hash is None:
            hashes=conn.execute("SELECT DISTINCT source_hash FROM opportunities").fetchall()
            if len(hashes)>1: raise ValueError("存在多版本基线，请指定 source_hash")
            source_hash=hashes[0][0] if hashes else ""
        conn.execute("CREATE TEMP VIEW selected_opportunities AS SELECT * FROM main.opportunities")
        # Use a temporary table to scope every existing invariant query to one import.
        conn.execute("CREATE TEMP TABLE opportunities AS SELECT * FROM main.opportunities WHERE source_hash=?",(source_hash,))
        total = conn.execute("SELECT COUNT(*) c FROM opportunities").fetchone()["c"]
        scored = conn.execute(
            "SELECT COUNT(*) c FROM opportunities WHERE source_row < 100").fetchone()["c"]
        excluded = total - scored
        groups = conn.execute(
            "SELECT COUNT(DISTINCT category_group) c FROM opportunities"
            " WHERE source_row < 100 AND category_group != ''").fetchone()["c"]
        # 未知样本（空/None）与真实 0 分别统计（R33）
        zero_supply = conn.execute(
            "SELECT COUNT(*) c FROM opportunities"
            " WHERE json_extract(raw_json, '$.1688样本') = '0' AND source_row < 100"
        ).fetchone()["c"]
        unknown_supply = conn.execute(
            "SELECT COUNT(*) c FROM opportunities"
            " WHERE (json_extract(raw_json, '$.1688样本') IS NULL"
            "        OR json_extract(raw_json, '$.1688样本') = '') AND source_row < 100"
        ).fetchone()["c"]
        # 四脚衣观察状态
        obs = conn.execute(
            "SELECT disposition FROM opportunities WHERE keyword='狗狗四脚衣'").fetchone()
        sijiaoyi_observed = bool(obs and obs["disposition"] == "保留观察")

        # 不变量判定（R33：目标 30=25+5 / 9 组 / 8 个真实 0 / 四脚衣观察）
        checks = {
            "总词数=30": total == 30,
            "唯一关键词=30": conn.execute("SELECT COUNT(DISTINCT keyword) FROM opportunities").fetchone()[0]==30,
            "无未知供应样本": unknown_supply==0,
            "关键词来源一致": all(json.loads(r["raw_json"]).get("关键词")==r["keyword"] for r in conn.execute("SELECT * FROM opportunities")),
            "品类来源一致": all(json.loads(r["raw_json"]).get("品类组")==r["category_group"] for r in conn.execute("SELECT * FROM opportunities WHERE source_row<100")),
            "评分词=25": scored == 25,
            "排除/观察=5": excluded == 5,
            "品类组=9": groups == 9,
            "零供应样本=8": zero_supply == 8,
            "四脚衣保留观察": sijiaoyi_observed,
        }
        failed = [k for k, ok in checks.items() if not ok]
        return {
            "total": total, "scored": scored, "excluded_or_observed": excluded,
            "groups": groups, "zero_supply_samples": zero_supply,
            "unknown_supply_samples": unknown_supply,
            "sijiaoyi_observed": sijiaoyi_observed,
            "checks": checks, "failed": failed,
            "pass": len(failed) == 0,
        }
    finally:
        conn.close()
