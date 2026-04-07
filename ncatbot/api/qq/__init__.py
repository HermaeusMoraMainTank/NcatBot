"""QQ 平台 API 子包"""

from .interface import IQQAPIClient
from .client import QQAPIClient

__all__ = [
    "IQQAPIClient",
    "QQAPIClient",
]
