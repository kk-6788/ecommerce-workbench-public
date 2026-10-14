"""可配置评分引擎（PM Decision OS 扩充）。

- 每个变量定义来源、方向、归一化、权重、置信度、刷新频率、代理标志和重叠组
- 配置不完整不生成伪精确总分；缺失值不隐式当 0 也不静默重分配权重
- 已触发 KILL 的商品永不因分高自动放行
- 没有历史验证的配置标 PROVISIONAL；未设置正式权重时禁用加权总分
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
import math

# 变量元数据必填字段（pm-fields.json score_variable_required_metadata）
REQUIRED_VAR_METADATA = [
    "definition", "source", "direction", "scale", "normalization",
    "weight", "confidence", "refresh_frequency", "is_proxy", "overlap_group",
    "rationale", "status",
]


@dataclass
class ScoreVariable:
    name: str
    definition: str
    source: str
    direction: str                 # higher_better / lower_better
    scale: float                   # 原始值域上界（归一化分母）
    normalization: str             # linear / log / boolean
    weight: float
    confidence: str                # high / medium / low
    refresh_frequency: str
    is_proxy: bool
    overlap_group: str | None
    rationale: str
    status: str                    # PROVISIONAL / ACTIVE / DISABLED

    def metadata_complete(self) -> bool:
        return all(getattr(self, f) not in (None, "") for f in [
            "definition", "source", "direction", "scale", "normalization",
            "weight", "confidence", "refresh_frequency", "rationale", "status"])


@dataclass
class ScoreConfig:
    version: str
    variables: list[ScoreVariable]
    status: str = "PROVISIONAL"   # 无历史验证 → PROVISIONAL
    weights_authorized: bool = False  # 未设置正式权重时禁用加权总分

    def validate(self) -> list[str]:
        """配置不完整不生成伪精确总分：返回缺失项清单。"""
        errors = []
        for v in self.variables:
            if not all(isinstance(x, (int, float)) and math.isfinite(x) for x in (v.scale, v.weight)) or v.scale <= 0 or v.weight < 0:
                errors.append(f"{v.name} 权重/量纲无效")
            if v.direction not in ("higher_better", "lower_better") or v.normalization not in ("linear", "log", "boolean"):
                errors.append(f"{v.name} 归一化配置无效")
            for f in REQUIRED_VAR_METADATA:
                if f == "overlap_group":
                    continue  # 无重叠组时合法为 None
                if getattr(v, f) in (None, ""):
                    errors.append(f"{v.name}.{f} 缺失")
        if self.status not in ("PROVISIONAL", "ACTIVE"):
            errors.append("配置状态必须是 PROVISIONAL 或 ACTIVE")
        return errors


@dataclass
class ScoreResult:
    variable_scores: dict[str, float]
    final_score: Optional[float]
    weighted_enabled: bool
    missing_variables: list[str] = field(default_factory=list)
    kill_hits: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class KillFilter:
    """硬性停止条件。KILL 永不因分高自动放行。"""
    def __init__(self, rules: dict[str, dict]):
        self.rules = rules  # {name: {"field": ..., "op": ..., "value": ...}}

    def evaluate(self, facts: dict) -> list[str]:
        hits = []
        for name, rule in self.rules.items():
            val = facts.get(rule["field"])
            op = rule["op"]
            target = rule["value"]
            if val is None:
                continue  # 缺证据与证伪分开——WAIT 而非 KILL
            try:
                if op == "lt" and float(val) < float(target):
                    hits.append(name)
                elif op == "le" and float(val) <= float(target):
                    hits.append(name)
                elif op == "gt" and float(val) > float(target):
                    hits.append(name)
                elif op == "ge" and float(val) >= float(target):
                    hits.append(name)
                elif op == "eq" and str(val) == str(target):
                    hits.append(name)
                elif op == "neq" and str(val) != str(target):
                    hits.append(name)
            except (TypeError, ValueError):
                continue
        return hits


def _normalize(value: Any, v: ScoreVariable) -> float:
    """按变量归一化方式处理，所有分支统一应用方向（R15）。

    线性/对数/布尔三种归一化都先算 0-1 的原始强度，再按 direction
    决定是否反转：higher_better 保持，lower_better 取反。
    """
    try:
        f = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("评分输入必须为有限数") from exc
    if not math.isfinite(f) or not math.isfinite(v.scale) or v.scale <= 0:
        raise ValueError("评分输入和量纲必须有效且有限")
    if v.normalization == "boolean":
        raw = 1.0 if f > 0 else 0.0
    elif v.normalization == "log":
        raw = min(1.0, math.log1p(max(f, 0.0)) / math.log1p(v.scale))
    else:  # linear
        raw = min(1.0, max(0.0, f / v.scale))
    return raw if v.direction == "higher_better" else (1.0 - raw)


def score_candidate(config: ScoreConfig, facts: dict,
                    kill_filter: KillFilter | None = None) -> ScoreResult:
    """评分。缺失值不隐式当 0：缺失变量记录，不静默重分配权重。

    返回 final_score=None 当：
    - 配置不完整（validate 有错误）
    - 权重未授权（weights_authorized=False）
    - 有 KILL 命中
    """
    notes: list[str] = []
    errors = config.validate()
    if errors:
        return ScoreResult(
            variable_scores={}, final_score=None, weighted_enabled=False,
            missing_variables=[], kill_hits=[],
            notes=["配置不完整，不生成伪精确总分"] + errors)

    if kill_filter:
        absent = [r["field"] for r in kill_filter.rules.values() if facts.get(r["field"]) is None]
        if absent:
            return ScoreResult({}, None, False, missing_variables=absent, notes=["WAIT：硬门槛缺证据"])
        for rule in kill_filter.rules.values():
            if rule["op"] in ("lt", "le", "gt", "ge"):
                if not all(math.isfinite(float(v)) for v in (facts[rule["field"]], rule["value"])):
                    raise ValueError("硬门槛数值必须有限")
    kill_hits = kill_filter.evaluate(facts) if kill_filter else []
    if kill_hits:
        return ScoreResult(
            variable_scores={}, final_score=None, weighted_enabled=False,
            missing_variables=[], kill_hits=kill_hits,
            notes=["已触发硬性停止条件，不因分高放行"])

    variable_scores: dict[str, float] = {}
    missing: list[str] = []
    active = [v for v in config.variables if v.status == "ACTIVE"]
    for v in active:
        if v.name not in facts or facts[v.name] is None:
            missing.append(v.name)
            continue
        variable_scores[v.name] = _normalize(facts[v.name], v)

    if not config.weights_authorized:
        return ScoreResult(
            variable_scores=variable_scores, final_score=None,
            weighted_enabled=False, missing_variables=missing,
            kill_hits=[], notes=["正式权重未授权，禁用加权总分"])

    # 加权总分（R15：缺变量不隐式重分配权重——缺项阻塞总分，或需显式缺失政策）
    missing_weight = sum(v.weight for v in active if v.name in missing)
    if missing_weight > 0:
        return ScoreResult(
            variable_scores=variable_scores, final_score=None,
            weighted_enabled=True, missing_variables=missing, kill_hits=[],
            notes=["存在缺失变量，禁止隐式重分配权重，不生成总分"] + missing)
    total_w = sum(v.weight for v in active if v.name in variable_scores)
    if total_w <= 0:
        return ScoreResult(
            variable_scores=variable_scores, final_score=None,
            weighted_enabled=True, missing_variables=missing, kill_hits=[],
            notes=["无有效权重分母，不生成总分"])
    weighted = sum(variable_scores[v.name] * v.weight for v in active
                   if v.name in variable_scores) / total_w
    return ScoreResult(
        variable_scores=variable_scores, final_score=round(weighted, 4),
        weighted_enabled=True, missing_variables=missing, kill_hits=[],
        notes=notes)
