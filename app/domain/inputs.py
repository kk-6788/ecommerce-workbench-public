"""API、导入和命令行共享的事实校验与实际预测输入。"""
from dataclasses import fields
from decimal import Decimal, InvalidOperation
import math

from app.domain.legacy_rules import TierInput

COUNTS = {'top_payments', 'taobao_samples', 'supply_samples', 'supplier_count', 'moq', 'lead_time_days', 'variant_count'}
MONEY = {'median_price', 'conservative_procurement', 'lowest_procurement', 'highest_procurement', 'combo_margin'}


def validate_facts(facts):
    if not isinstance(facts, dict):
        raise ValueError('facts 必须是对象')
    def finite(value):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError('事实不能包含 NaN/Infinity')
        if isinstance(value, dict):
            for v in value.values(): finite(v)
        if isinstance(value, list):
            for v in value: finite(v)
    finite(facts)
    for key in COUNTS:
        v = facts.get(key)
        if v is not None and (type(v) is not int or v < 0):
            raise ValueError(f'{key} 必须是非负整数')
    for key in MONEY:
        v = facts.get(key)
        if v is not None:
            try: d = Decimal(str(v))
            except InvalidOperation as exc: raise ValueError(f'{key} 金额格式错误') from exc
            if not d.is_finite() or (key != 'combo_margin' and d < 0):
                raise ValueError(f'{key} 金额无效')
    for key in ('needs_supply_verify', 'has_direct_evidence', 'combo_viable'):
        if facts.get(key) is not None and type(facts[key]) is not bool:
            raise ValueError(f'{key} 必须是布尔值')
    if facts.get('procurement_source', 'UNKNOWN') not in ('FACT','PROXY','ESTIMATE','UNKNOWN'):
        raise ValueError('procurement_source 无效')
    if facts.get('product_form', 'GENERAL') not in ('GENERAL','CLEAN','COMBO'):
        raise ValueError('product_form 无效')
    return facts


def effective_input(product, overrides=None):
    facts = dict(product.get('facts') or {})
    overrides = dict(overrides or {})
    facts.update(overrides.pop('facts', {}) or {})
    facts.update(overrides)
    validate_facts(facts)
    kwargs = {f.name: facts[f.name] for f in fields(TierInput) if f.name in facts}
    kwargs.pop("price_spans",None)  # Recompute; never trust a client-supplied PASS.
    kwargs.setdefault('keyword', product['title'])
    kwargs.setdefault('category_group', product.get('category', '未分组'))
    kwargs.setdefault('procurement_source', 'UNKNOWN')
    for key in MONEY & kwargs.keys():
        if kwargs[key] is not None: kwargs[key] = Decimal(str(kwargs[key]))
    inp = TierInput(**kwargs)
    frozen = {f.name: (str(getattr(inp, f.name)) if isinstance(getattr(inp, f.name), Decimal) else getattr(inp, f.name)) for f in fields(inp)}
    return inp, {'product_id': product['id'], 'revision': product['revision'], 'facts': facts, 'effective_input': frozen}
