"""
插件加载上下文

通过 ContextVar 隔离并发/串行插件加载时的 plugin name，
供装饰器（如 registrar.on）在模块执行期间读取当前插件标识。
"""

from contextvars import ContextVar, Token
from typing import Optional

_current_plugin_ctx: ContextVar[Optional[str]] = ContextVar(
    "_current_plugin_ctx", default=None
)


def set_current_plugin(name: str) -> Token:
    """设置当前正在加载的插件名，返回 Token 用于 reset。"""
    return _current_plugin_ctx.set(name)


def get_current_plugin() -> Optional[str]:
    """获取当前正在加载的插件名，不在插件加载上下文时返回 None。"""
    return _current_plugin_ctx.get()
