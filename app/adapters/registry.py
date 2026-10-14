"""Adapter 注册表。fake 只在 test_mode 可用。"""
from __future__ import annotations

from app.adapters.base import Adapter
from app.adapters.fake import FakeAdapter


class Registry:
    def __init__(self, adapters: dict[str, Adapter] | None = None):
        self._adapters = adapters or {}

    def register(self, name: str, adapter: Adapter) -> None:
        self._adapters[name] = adapter

    def get(self, name: str) -> Adapter:
        if name not in self._adapters:
            raise KeyError(f"adapter 不存在：{name}")
        return self._adapters[name]

    def names(self) -> list[str]:
        return sorted(self._adapters.keys())

    def capabilities_report(self) -> dict:
        return {n: self._adapters[n].capabilities() for n in sorted(self._adapters)}


def default_registry(*, test_mode: bool = False, settings=None) -> Registry:
    """默认注册表。fake 仅在 test_mode 注入。"""
    reg = Registry()
    if settings is not None:
        from app.adapters.typesafe import TypeSafeAdapter
        reg.register('typesafe', TypeSafeAdapter(settings))
    if test_mode:
        reg.register("fake", FakeAdapter())
    return reg
