"""跨平台通用事件基类、Mixin 与工厂"""

from .base import BaseEvent
from .factory import (
    create_entity,
    register_platform_factory,
    register_platform_secondary_keys,
    get_secondary_key,
)
from .mixins import (
    Approvable,
    Bannable,
    Deletable,
    GroupScoped,
    HasAttachments,
    HasSender,
    Kickable,
    Replyable,
)

__all__ = [
    "BaseEvent",
    "create_entity",
    "register_platform_factory",
    "register_platform_secondary_keys",
    "get_secondary_key",
    # mixins
    "Replyable",
    "Deletable",
    "HasSender",
    "GroupScoped",
    "Kickable",
    "Bannable",
    "Approvable",
    "HasAttachments",
]
