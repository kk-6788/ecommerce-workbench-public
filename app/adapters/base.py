"""Adapter 契约：capabilities / probe / collect / quote / execute / reconcile。

- 构造或导入时不得联网
- execution_token 由 execution 服务在通过审批后生成，adapter 不能绕过服务自行执行
- probe 只用已知免费只读方法；若 probe 收费也走 plan 审批
- fake adapter 只在 test_mode 可用，输出 is_synthetic=true，不能混入生产指标
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

# 统一错误码
NEEDS_LOGIN = "NEEDS_LOGIN"
CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"
NEEDS_EVIDENCE = "NEEDS_EVIDENCE"
RATE_LIMITED = "RATE_LIMITED"
PROVIDER_ERROR = "PROVIDER_ERROR"
UNKNOWN_RESULT = "UNKNOWN_RESULT"

# capability 类型
READ = "READ"
WRITE = "WRITE"
QUOTE = "QUOTE"
IMAGE = "IMAGE"
VIDEO = "VIDEO"


@dataclass
class Capability:
    kind: str                     # READ/WRITE/QUOTE/IMAGE/VIDEO
    status: str                   # UNVERIFIED/NEEDS_LOGIN/VERIFIED_READ/VERIFIED_WRITE/UNAVAILABLE
    verified_at: str | None = None
    billing: str = "free"         # free/paid/unknown
    requires_login: bool = False
    notes: str = ""
    evidence_id: str | None = None
    hard_cap_supported: bool = False


@dataclass
class AdapterResult:
    ok: bool
    code: str = "OK"
    data: dict = field(default_factory=dict)
    provider_request_id: str | None = None
    is_synthetic: bool = False
    evidence_path: str | None = None
    evidence_hash: str | None = None
    error_message: str | None = None


class Adapter(Protocol):
    name: str

    def capabilities(self) -> dict: ...
    def probe(self, request: dict) -> AdapterResult: ...
    def collect(self, request: dict) -> AdapterResult: ...
    def quote(self, request: dict) -> AdapterResult: ...
    def execute(self, request: dict, execution_token: str) -> AdapterResult: ...
    def reconcile(self, provider_request_id: str) -> AdapterResult: ...
