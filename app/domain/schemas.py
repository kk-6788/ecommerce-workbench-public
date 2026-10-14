"""JSON 字段域验证。禁止把未定义字典直接透传数据库。"""
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any


class SchemaError(ValueError):
    pass


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise SchemaError(msg)


def validate_decimal_money(value: Any, field: str, *, nullable: bool = True) -> str | None:
    """金额字符串校验。返回规范化字符串或 None。"""
    if value is None or value == "":
        if nullable:
            return None
        raise SchemaError(f"{field}: 金额必填")
    try:
        d = Decimal(str(value))
        return str(d)
    except (InvalidOperation, ValueError, TypeError):
        raise SchemaError(f"{field}: 金额格式错误：{value!r}")


def validate_ratio(value: Any, field: str, *, nullable: bool = True) -> str | None:
    """比率 0-1 校验。"""
    if value is None or value == "":
        if nullable:
            return None
        raise SchemaError(f"{field}: 比率必填")
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise SchemaError(f"{field}: 比率格式错误：{value!r}")
    if d < 0 or d > 1:
        raise SchemaError(f"{field}: 比率必须在 0-1 之间：{value!r}")
    return str(d)


def validate_nonneg_int(value: Any, field: str, *, nullable: bool = True) -> int | None:
    if value is None or value == "":
        if nullable:
            return None
        raise SchemaError(f"{field}: 必填")
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise SchemaError(f"{field}: 非负整数格式错误：{value!r}")
    if n < 0:
        raise SchemaError(f"{field}: 不能为负：{value!r}")
    return n


def validate_enum(value: Any, allowed: list[str], field: str, *, nullable: bool = True):
    if value is None or value == "":
        if nullable:
            return None
        raise SchemaError(f"{field}: 必填")
    if value not in allowed:
        raise SchemaError(f"{field}: 值 {value!r} 不在允许范围 {allowed}")
    return value


def validate_json_obj(value: str | dict, field: str) -> dict:
    """确保字段是 JSON 对象（字符串则解析）。"""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            raise SchemaError(f"{field}: JSON 解析失败")
        if not isinstance(parsed, dict):
            raise SchemaError(f"{field}: 必须是对象")
        return parsed
    raise SchemaError(f"{field}: 必须是对象")


def canonical_json(obj: Any) -> str:
    """规范 JSON：键排序、UTF-8、无无关空格。用于内容 hash。"""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
