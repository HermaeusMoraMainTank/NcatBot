"""
MockAPIBase — 所有平台 Mock API 的共享基类

提供 APICall 录制、查询、响应预设的统一基础设施。
所有参数以命名形式 (params: dict) 存储，杜绝位置元组。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ncatbot.api.base import IAPIClient


@dataclass
class APICall:
    """一次 API 调用的记录 — 所有参数按名存储"""

    action: str
    params: Dict[str, Any] = field(default_factory=dict)


class MockAPIBase(IAPIClient):
    """所有平台 Mock API 的共享基类

    子类的每个方法应调用 ``_record(action, key=val, ...)``，
    所有参数以关键字形式传入，统一存储到 ``APICall.params``。
    """

    def __init__(self, platform: str = "unknown") -> None:
        self._platform = platform
        self._calls: List[APICall] = []
        self._responses: Dict[str, Any] = {}

    @property
    def platform(self) -> str:
        return self._platform

    async def call(self, action: str, params: Optional[dict] = None) -> Any:
        return self._record(action, **(params or {}))

    def _record(self, action: str, **params: Any) -> Any:
        """录制一次 API 调用 — 所有参数必须以关键字形式传入"""
        self._calls.append(APICall(action=action, params=params))
        return self._responses.get(action, {})

    # ---- 响应预设 ----

    def set_response(self, action: str, response: Any) -> None:
        """预设某个 action 的返回值"""
        self._responses[action] = response

    # ---- 查询 ----

    @property
    def calls(self) -> List[APICall]:
        return list(self._calls)

    def called(self, action: str) -> bool:
        return any(c.action == action for c in self._calls)

    def call_count(self, action: str) -> int:
        return sum(1 for c in self._calls if c.action == action)

    def get_calls(self, action: str) -> List[APICall]:
        return [c for c in self._calls if c.action == action]

    def last_call(self, action: Optional[str] = None) -> Optional[APICall]:
        if action:
            matching = self.get_calls(action)
            return matching[-1] if matching else None
        return self._calls[-1] if self._calls else None

    def reset(self) -> None:
        self._calls.clear()
