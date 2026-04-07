"""通用事件工厂 — 支持按 platform 路由到各平台 factory"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Dict, Optional

from ncatbot.types.common import (
    BaseEventData,
    register_platform_secondary_keys,
    get_secondary_key,
)

from .base import BaseEvent

if TYPE_CHECKING:
    from ncatbot.api import IAPIClient

__all__ = [
    "create_entity",
    "register_platform_factory",
    "register_platform_secondary_keys",
    "get_secondary_key",
]

PlatformFactory = Callable[[BaseEventData, "IAPIClient"], Optional[BaseEvent]]

_platform_factories: Dict[str, PlatformFactory] = {}


def register_platform_factory(platform: str, factory: PlatformFactory) -> None:
    """注册平台专用事件工厂"""
    _platform_factories[platform] = factory


def create_entity(data: BaseEventData, api: "IAPIClient") -> BaseEvent:
    """数据模型 → 实体（平台工厂优先，最终 BaseEvent）"""
    factory = _platform_factories.get(data.platform)
    if factory is not None:
        entity = factory(data, api)
        if entity is not None:
            return entity

    return BaseEvent(data, api)
