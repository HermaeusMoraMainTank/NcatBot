"""QQLoggingProxy — QQ 平台日志代理"""

from __future__ import annotations

from typing import cast

from ..proxy import BaseLoggingProxy

from .interface import IQQAPIClient


class QQLoggingProxy(BaseLoggingProxy):
    """QQ 平台的日志代理，包装 IQQAPIClient"""

    @staticmethod
    def wrap(api: IQQAPIClient) -> IQQAPIClient:
        """包装 IQQAPIClient 并返回代理（类型标记为 IQQAPIClient）"""
        return cast(IQQAPIClient, QQLoggingProxy(api))
