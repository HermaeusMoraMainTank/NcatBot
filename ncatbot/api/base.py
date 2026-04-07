"""IAPIClient — 所有平台 API 的最小接口"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional


class IAPIClient(ABC):
    """所有平台适配器 API 必须实现的最小接口"""

    @property
    @abstractmethod
    def platform(self) -> str:
        """平台标识，如 "qq"、"telegram" """

    @abstractmethod
    async def call(self, action: str, params: Optional[dict] = None) -> Any:
        """通用 API 调用入口"""
