"""PM 决策字段校验（G03）：基于 docs/handoff/contracts/pm-fields.json。

- candidate/outcome/prediction_snapshot 逐字段类型校验（nullable、非负整数、
  decimal、enum、array、object）
- 未知字段拒绝；空值保留 null（导入导出不丢空语义）
- 导出防公式执行（单元格前导 = + - @ 加 '）
"""
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

PM_FIELDS_PATH = Path(__file__).resolve().parents[2] / "docs" / "handoff" / "contracts" / "pm-fields.json"

_TYPE_CHECKS = {
    "string": lambda v, f: isinstance(v, str),
    "nonnegative_integer": lambda v, f: (isinstance(v, bool) is False
                                          and isinstance(v, int) and v >= 0),
    "positive_integer": lambda v, f: (isinstance(v, bool) is False
                                       and isinstance(v, int) and v > 0),
    "integer": lambda v, f: isinstance(v, bool) is False and isinstance(v, int),
    "decimal_string": lambda v, f: _is_decimal(v),
    "boolean": lambda v, f: isinstance(v, bool),
    "array": lambda v, f: isinstance(v, list),
    "object": lambda v, f: isinstance(v, dict),
    "enum": lambda v, f: isinstance(v, str),
}


def _is_decimal(v) -> bool:
    try:
        return not isinstance(v,bool) and Decimal(str(v)).is_finite()
    except (InvalidOperation, ValueError, TypeError):
        return False


def _load_fields() -> dict:
    if not PM_FIELDS_PATH.exists():
        return {}
    try:
        return json.loads(PM_FIELDS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


_FIELD_CACHE: dict | None = None


def pm_fields() -> dict:
    global _FIELD_CACHE
    if _FIELD_CACHE is None:
        _FIELD_CACHE = _load_fields()
    return _FIELD_CACHE


def validate_section(section: str, record: dict) -> list[str]:
    """校验单个记录（candidate/outcome/prediction_snapshot）。返回错误列表。"""
    fields = pm_fields().get(section, {})
    if not fields:
        return [f"pm-fields 缺 {section} 定义"]
    errs = []
    for key, meta in fields.items():
        ftype = meta.get("type", "string")
        nullable = meta.get("nullable", True)
        if key not in record or record[key] is None or record[key] == "":
            if not nullable:
                errs.append(f"{section}.{key} 必填")
            continue
        val = record[key]
        if key in ("timestamp","test_start_date","test_end_date") and not _time(val):
            errs.append(f"{section}.{key} 必须带时区")
        if key in ("ad_spend","revenue") and _is_decimal(val) and Decimal(str(val)) < 0:
            errs.append(f"{section}.{key} 不能为负")
        if ftype == "enum":
            allowed = meta.get("values", [])
            if val not in allowed:
                errs.append(f"{section}.{key} 必须在 {allowed} 中（收到 {val!r}）")
            continue
        check = _TYPE_CHECKS.get(ftype)
        if check is None:
            errs.append(f"{section}.{key} 不支持类型 {ftype}")
        elif not check(val, key):
            errs.append(f"{section}.{key} 类型应为 {ftype}（收到 {val!r}）")
    # 未知字段拒绝（防止拼错列名静默透传）
    known = set(fields.keys())
    for key in record:
        if key not in known:
            errs.append(f"{section}.{key} 不在字段字典中（拼写错误？）")
    return errs


def csv_safe(value) -> str:
    """导出防公式执行：前导 = + - @ 前加 '（G03）。None → 空字符串。"""
    if value is None:
        return ""
    s = json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value,(dict,list)) else str(value)
    if s.startswith(("=", "+", "-", "@")) and len(s) > 1:
        return "'" + s
    return s


def _time(value):
    from datetime import datetime
    try: return isinstance(value,str) and datetime.fromisoformat(value).tzinfo is not None
    except ValueError: return False

def _field_evidence(value, _):
    required={"evidence_id","source_time","scope","is_proxy","confidence","missing_reason"}
    return isinstance(value,dict) and all(isinstance(v,dict) and required<=v.keys() and
        type(v["is_proxy"]) is bool and (v["source_time"] is None or _time(v["source_time"])) and
        bool(v["scope"]) and bool(v["confidence"]) and (bool(v["evidence_id"]) or bool(v["missing_reason"])) for v in value.values())

_TYPE_CHECKS.update({
 "decimal_money_string":lambda v,f:_is_decimal(v) and Decimal(str(v))>=0,
 "signed_decimal_money_string":lambda v,f:_is_decimal(v),
 "nonnegative_decimal_string":lambda v,f:_is_decimal(v) and Decimal(str(v))>=0,
 "ratio_0_to_1_decimal_string":lambda v,f:_is_decimal(v) and 0<=Decimal(str(v))<=1,
 "ratio_decimal_string":lambda v,f:_is_decimal(v) and ("margin" in f or Decimal(str(v))>=0),
 "array_of_ids":lambda v,f:isinstance(v,list) and all(isinstance(x,str) and x.strip() for x in v),
 "array_of_strings":lambda v,f:isinstance(v,list) and all(isinstance(x,str) for x in v),
 "object_by_field_name":_field_evidence,
 "configured_dimension_value":lambda v,f:isinstance(v,dict) and _is_decimal(v.get("value")) and _is_decimal(v.get("scale")) and Decimal(str(v["scale"]))>0 and bool(v.get("config_version")),
 "evidence_based_label":lambda v,f:isinstance(v,str) and bool(v.strip()),
})

def decode_csv(section,row):
    fields=pm_fields()[section]
    result={}
    for key,raw in row.items():
        if key not in fields: raise ValueError(f"未知字段 {key}")
        if raw in (None,""): result[key]=None; continue
        kind=fields[key]["type"]
        if raw.startswith("'") and raw[1:2] in ("=","+","-","@"): raw=raw[1:]
        if kind.endswith("integer"):
            import re
            if not re.fullmatch(r"[+-]?\d+",raw): raise ValueError(f"{key} 必须是整数")
            result[key]=int(raw)
        elif kind=="boolean":
            if raw.lower() not in ("true","false","1","0"): raise ValueError(f"{key} 布尔值无效")
            result[key]=raw.lower() in ("true","1")
        elif kind in ("object","object_by_field_name","configured_dimension_value") or kind.startswith("array"):
            result[key]=json.loads(raw)
        else: result[key]=raw
    errors=validate_section(section,result)
    for key in ("timestamp","test_start_date","test_end_date"):
        if result.get(key) is not None and not _time(result[key]): errors.append(f"{key} 必须带时区")
    if errors: raise ValueError("；".join(errors))
    return result
