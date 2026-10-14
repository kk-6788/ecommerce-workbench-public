"""fake adapter：仅 test_mode 可用，is_synthetic=true。"""
from __future__ import annotations

import time
import uuid

from app.adapters.base import AdapterResult, Capability


class FakeAdapter:
    """模拟提交：成功但可能超时 → UNKNOWN 不重发。"""
    name = "fake"

    def __init__(self, *, simulate_timeout: bool = False, submit_count_ref=None):
        self.simulate_timeout = simulate_timeout
        self.submit_count = 0

    def capabilities(self) -> dict:
        return {
            "name": self.name,
            "caps": [
                Capability("READ", "VERIFIED_READ", billing="free"),
                Capability("WRITE", "VERIFIED_WRITE", billing="free",verified_at="2026-09-19T00:00:00+00:00",evidence_id="synthetic-fixture",hard_cap_supported=True),
                Capability("QUOTE", "VERIFIED_READ", billing="free"),
                Capability("IMAGE", "VERIFIED_READ", billing="paid",verified_at="2026-09-19T00:00:00+00:00",evidence_id="synthetic-fixture",hard_cap_supported=True),
                Capability("VIDEO", "VERIFIED_READ", billing="paid",verified_at="2026-09-19T00:00:00+00:00",evidence_id="synthetic-fixture",hard_cap_supported=True),
            ],
        }

    def probe(self, request: dict) -> AdapterResult:
        return AdapterResult(ok=True, data={"probe": "fake-ok"}, is_synthetic=True)

    def collect(self, request: dict) -> AdapterResult:
        return AdapterResult(ok=True, data={"rows": []}, is_synthetic=True)

    def quote(self, request: dict) -> AdapterResult:
        return AdapterResult(
            ok=True,
            data={"estimated_amount": "0.80", "maximum_amount": "1.00",
                  "currency": "CNY", "failure_billing": "free"},
            is_synthetic=True,
        )

    def execute(self, request: dict, execution_token: str) -> AdapterResult:
        if not execution_token:
            return AdapterResult(ok=False, code="FORBIDDEN",
                                 error_message="缺少 execution_token",
                                 is_synthetic=True)
        self.submit_count += 1
        provider_id = f"fake-{uuid.uuid4()}"
        if self.simulate_timeout:
            # 模拟"供应商已接受但本地超时"：不返回回执，状态未知
            return AdapterResult(ok=False, code="UNKNOWN_RESULT",
                                 provider_request_id=provider_id,
                                 is_synthetic=True,
                                 error_message="本地超时，结果未知")
        return AdapterResult(ok=True, provider_request_id=provider_id,
                             data={"receipt": "fake-success", "actual_amount_minor":20,"currency":"CNY","billing_receipt_id":provider_id},
                             is_synthetic=True)

    def reconcile(self, provider_request_id: str) -> AdapterResult:
        return AdapterResult(ok=True, data={"status": "SUCCEEDED","actual_amount_minor":20,"currency":"CNY","billing_receipt_id":provider_request_id},
                             provider_request_id=provider_request_id,
                             is_synthetic=True)
