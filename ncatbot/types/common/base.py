from __future__ import annotations

from typing import Any, Dict

from pydantic import BaseModel, ConfigDict, model_validator

__all__ = [
    "BaseEventData",
    "register_platform_secondary_keys",
    "get_secondary_key",
]

# 平台 secondary key 注册表: {platform: {post_type: attr_name}}
_platform_secondary_keys: Dict[str, Dict[str, str]] = {}


def register_platform_secondary_keys(platform: str, mapping: Dict[str, str]) -> None:
    """注册平台的 secondary key 映射

    Args:
        platform: 平台名，如 "bilibili"、"github"
        mapping: {post_type: attr_name}，如 {"live": "live_event_type"}
    """
    _platform_secondary_keys[platform] = mapping


def get_secondary_key(platform: str, post_type: str) -> str:
    """查询平台的 secondary key 属性名"""
    return _platform_secondary_keys.get(platform, {}).get(post_type, "")


class BaseEventData(BaseModel):
    """事件数据模型基类 — 纯数据，可序列化"""

    model_config = ConfigDict(extra="allow")

    time: int
    self_id: str
    post_type: str = ""
    platform: str = "unknown"

    @model_validator(mode="before")
    @classmethod
    def _coerce_ids(cls, data: Any) -> Any:
        """统一将所有 *_id 字段从 int/float 转 str"""
        if isinstance(data, dict):
            for key, value in data.items():
                if key.endswith("_id") and isinstance(value, (int, float)):
                    data[key] = str(int(value))
        return data

    def resolve_type(self) -> str:
        """推导点分格式事件类型字符串。

        默认实现通过平台注册的 secondary key 查注册表。
        子类可 override 提供自定义逻辑（如 QQ NotifyEventData）。

        Returns:
            如 ``"message.group"``、``"meta_event.heartbeat"``、``"live.danmu_msg"``。
        """
        post_type = self.post_type
        if hasattr(post_type, "value"):
            post_type = post_type.value
        post_type = str(post_type)

        platform = self.platform
        if hasattr(platform, "value"):
            platform = platform.value

        attr_name = get_secondary_key(str(platform), post_type)
        if attr_name:
            val = getattr(self, attr_name, "")
            secondary = val.value if hasattr(val, "value") else str(val) if val else ""
            if secondary:
                return f"{post_type}.{secondary.lower()}"

        return post_type
