"""内置插件模块。"""

from .system_manager import SystemManagerPlugin

BUILTIN_PLUGINS = [
    SystemManagerPlugin,
]

__all__ = [
    "BUILTIN_PLUGINS",
    "SystemManagerPlugin",
]
