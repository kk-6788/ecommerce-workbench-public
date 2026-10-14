"""证据域：来源、时间、粒度、类型。缺失证据 ≠ 0 也不等于通过/淘汰。"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


class EvidenceKind(str, Enum):
    FACT = "FACT"          # 已核实事实
    PROXY = "PROXY"        # 代理指标（如参考同类价）
    ESTIMATE = "ESTIMATE"  # 估算（如需求估算）
    UNKNOWN = "UNKNOWN"    # 未知，不补造


class CollectionMode(str, Enum):
    MANUAL = "MANUAL"          # 人工录入
    IMPORT = "IMPORT"          # 文件导入
    ADAPTER = "ADAPTER"        # adapter 采集
    USER_EXPORT = "USER_EXPORT"  # 用户导出


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def content_hash(obj) -> str:
    """内容 hash = 规范 JSON 的 SHA256。"""
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


@dataclass
class EvidenceRecord:
    """一条证据。不混 FACT/PROXY/ESTIMATE/UNKNOWN。"""
    source_key: str          # 来源标识（如 keyword:多功能牵引绳）
    source_url: str | None
    source_path: str | None
    evidence_kind: EvidenceKind
    collection_mode: CollectionMode
    observed_at: str | None
    content: dict
    product_id: str | None = None
    opportunity_id: str | None = None

    def to_row(self, *, is_synthetic: bool = False) -> dict:
        kind = self.evidence_kind
        if not isinstance(kind, EvidenceKind):
            kind = EvidenceKind(kind)
        mode = self.collection_mode
        if not isinstance(mode, CollectionMode):
            mode = CollectionMode(mode)
        return {
            "source_key": self.source_key,
            "source_url": self.source_url,
            "source_path": self.source_path,
            "evidence_kind": kind.value,
            "collection_mode": mode.value,
            "observed_at": self.observed_at,
            "collected_at": utcnow_iso(),
            "content_json": canonical_json(self.content),
            "source_hash": content_hash(self.content),
            "is_synthetic": 1 if is_synthetic else 0,
        }


def validate_source_scope(source_key: str) -> None:
    """来源标识不允许越界/路径穿越。"""
    if not source_key or len(source_key) > 500:
        raise ValueError("source_key 无效")
    if "\x00" in source_key or ".." in source_key:
        raise ValueError("source_key 含非法字符")
