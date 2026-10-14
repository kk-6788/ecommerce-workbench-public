"""淘宝 adapter：免费只读 collect 用商品级证据。

T14 只做 fixture 解析与状态返回；真实读取需 T26（登录态 + 只读核验）。
采集失败不写 0——缺失与 0 严格区分。
"""
from __future__ import annotations

import json
import uuid

from app.adapters.base import (
    AdapterResult, Capability, NEEDS_LOGIN, RATE_LIMITED, PROVIDER_ERROR,
)


class TaobaoAdapter:
    """淘宝商品与经营数据读取（只读能力骨架）。

    构造/导入不联网。实际 HTTP/CDP 调用在 T26 核验后接入。
    """
    name = "taobao"

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
                           notes="商品/经营只读，登录由用户处理"),
                Capability("WRITE", "UNVERIFIED", billing="free",
                           notes="上架/改价必须具体方案审批后执行"),
            ],
        }

    def probe(self, request: dict) -> AdapterResult:
        if not self.logged_in:
            return AdapterResult(ok=False, code=NEEDS_LOGIN,
                                 error_message="淘宝未登录，需用户处理登录")
        return AdapterResult(ok=True, data={"probe": "taobao-read-ok"})

    def collect(self, request: dict) -> AdapterResult:
        """商品级证据读取。request: {keyword|item_id, fields}。"""
        if not self.logged_in:
            return AdapterResult(ok=False, code=NEEDS_LOGIN,
                                 error_message="淘宝未登录")
        if self.fixture_path:
            try:
                with open(self.fixture_path, encoding="utf-8") as f:
                    data = json.load(f)
                return AdapterResult(ok=True, data=data, is_synthetic=bool(data.get("is_synthetic", True)))
            except (OSError, json.JSONDecodeError) as e:
                return AdapterResult(ok=False, code=PROVIDER_ERROR,
                                     error_message=f"fixture 读取失败：{e}")
        # 未接入真实通道：明确 CAPABILITY_UNAVAILABLE，不假装打通
        return AdapterResult(ok=False, code="CAPABILITY_UNAVAILABLE",
                             error_message="真实读取通道待 T26 核验，当前仅 fixture 模式")

    def quote(self, request: dict) -> AdapterResult:
        return AdapterResult(ok=False, code="CAPABILITY_UNAVAILABLE",
                             error_message="淘宝只读能力，无收费调用")

    def execute(self, request: dict, execution_token: str) -> AdapterResult:
        return AdapterResult(ok=False, code="CAPABILITY_UNAVAILABLE",
                             error_message="淘宝写操作需具体方案审批，且未核验")

    def reconcile(self, provider_request_id: str) -> AdapterResult:
        return AdapterResult(ok=False, code="CAPABILITY_UNAVAILABLE",
                             error_message="无待核对请求")
