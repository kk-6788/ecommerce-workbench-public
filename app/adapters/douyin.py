"""抖店 adapter：经营数据读取与短视频发布（只读能力骨架，写操作需审批）。"""
from __future__ import annotations

import json

from app.adapters.base import (
    AdapterResult, Capability, NEEDS_LOGIN, CAPABILITY_UNAVAILABLE, PROVIDER_ERROR,
)


class DouyinAdapter:
    name = "douyin"

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
                           notes="经营数据只读"),
                Capability("WRITE", "UNVERIFIED", billing="free",
                           notes="视频发布/改价必须具体方案审批"),
                Capability("VIDEO", "UNVERIFIED", billing="paid",
                           notes="短视频成片发布，逐笔审批"),
            ],
        }

    def probe(self, request: dict) -> AdapterResult:
        if not self.logged_in:
            return AdapterResult(ok=False, code=NEEDS_LOGIN,
                                 error_message="抖店未登录，需用户处理登录")
        return AdapterResult(ok=True, data={"probe": "douyin-read-ok"})

    def collect(self, request: dict) -> AdapterResult:
        if not self.logged_in:
            return AdapterResult(ok=False, code=NEEDS_LOGIN,
                                 error_message="抖店未登录")
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
        return AdapterResult(ok=False, code=CAPABILITY_UNAVAILABLE,
                             error_message="抖店只读能力，无收费调用")

    def execute(self, request: dict, execution_token: str) -> AdapterResult:
        return AdapterResult(ok=False, code=CAPABILITY_UNAVAILABLE,
                             error_message="发布写操作需具体方案审批，且未核验")

    def reconcile(self, provider_request_id: str) -> AdapterResult:
        return AdapterResult(ok=False, code=CAPABILITY_UNAVAILABLE,
                             error_message="无待核对请求")
