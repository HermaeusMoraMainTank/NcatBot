"""跨平台 API Trait 协议

松散 Protocol：约束方法名，不强制签名一致。
等第二个平台接入时再收紧参数类型。
"""

from .messaging import IMessaging
from .group_manage import IGroupManage
from .query import IQuery
from .file import IFileTransfer

__all__ = [
    "IMessaging",
    "IGroupManage",
    "IQuery",
    "IFileTransfer",
]
