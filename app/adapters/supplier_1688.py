"""1688 供应商 adapter：规格报价与素材读取（只读能力骨架）。"""
from __future__ import annotations

import json

from app.adapters.base import (
    AdapterResult, Capability, NEEDS_LOGIN, CAPABILITY_UNAVAILABLE, PROVIDER_ERROR,
)


class Supplier1688Adapter:
    name = "supplier_1688"

    def __init__(self, *, fixture_path: str | None = None, logged_in: bool = False):
        self.fixture_path = fixture_path
        self.logged_in = logged_in

    def capabilities(self) -> dict:
        return {
            "name": self.name,
            "status": "UNVERIFIED" if self.logged_in else "NEEDS_LOGIN",
            "caps": [
                Capability("READ", "UNVERIFIED" if self.logged_in else "NEEDS_LOGIN",
                           billing="free", requires_login=not self.logged_in,
                           notes="规格/报价/素材只读"),
                Capability("QUOTE", "UNVERIFIED" if self.logged_in else "NEEDS_LOGIN",
                           billing="free", requires_login=not self.logged_in,
                           notes="报价以页面明码标价为准"),
            ],
        }

    def probe(self, request: dict) -> AdapterResult:
        if not self.logged_in:
            return AdapterResult(ok=False, code=NEEDS_LOGIN,
                                 error_message="1688 未登录（采购账号由用户处理）")
        return AdapterResult(ok=True, data={"probe": "1688-read-ok"})

    def collect(self, request: dict) -> AdapterResult:
        if not self.logged_in:
            return AdapterResult(ok=False, code=NEEDS_LOGIN,
                                 error_message="1688 未登录")
        if self.fixture_path:
            try:
                with open(self.fixture_path, encoding="utf-8") as f:
                    data = json.load(f)
                return AdapterResult(ok=True, data=data, is_synthetic=bool(data.get("is_synthetic", True)))
            except (OSError, json.JSONDecodeError) as e:
                return AdapterResult(ok=False, code=PROVIDER_ERROR,
                                     error_message=f"fixture 读取失败：{e}")
        return AdapterResult(ok=False, code=CAPABILITY_UNAVAILABLE,
                             error_message="真实读取通道待 T26 核验")

    def quote(self, request: dict) -> AdapterResult:
        if not self.logged_in:
            return AdapterResult(ok=False, code=NEEDS_LOGIN, error_message="1688 未登录")
        if self.fixture_path:
            try:
                with open(self.fixture_path, encoding="utf-8") as f:
                    data = json.load(f)
                return AdapterResult(ok=True, data={"quote": data})
            except (OSError, json.JSONDecodeError) as e:
                return AdapterResult(ok=False, code=PROVIDER_ERROR,
                                     error_message=f"fixture 读取失败：{e}")
        return AdapterResult(ok=False, code=CAPABILITY_UNAVAILABLE,
                             error_message="真实报价通道待 T26 核验")

    def execute(self, request: dict, execution_token: str) -> AdapterResult:
        return AdapterResult(ok=False, code=CAPABILITY_UNAVAILABLE,
                             error_message="1688 采购/下单需用户采购账号，平台内人工操作")

    def reconcile(self, provider_request_id: str) -> AdapterResult:
        return AdapterResult(ok=False, code=CAPABILITY_UNAVAILABLE,
                             error_message="无待核对请求")
