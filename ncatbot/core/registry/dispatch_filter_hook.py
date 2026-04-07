"""
DispatchFilterHook — 全局分发过滤

作为 HandlerDispatcher 的全局 BEFORE_CALL Hook，在每个 handler 执行前
查询 DispatchFilterService 判断是否拦截。

优先级 200（高于 CommandHook 的 95），保证最先执行。
"""

from typing import Optional

from ncatbot.utils import get_log

from .hook import Hook, HookAction, HookContext, HookStage

LOG = get_log("DispatchFilterHook")


class DispatchFilterHook(Hook):
    """全局分发过滤 Hook

    从 HookContext 提取:
    - plugin_name: ``handler_entry.plugin_name``
    - group_id / user_id: ``event.data`` 上的属性
    - command: handler 上绑定的 CommandHook 的命令名

    查询 DispatchFilterService.is_blocked() 决定是否 SKIP。
    """

    stage = HookStage.BEFORE_CALL
    priority = 200  # 高于所有现有 hooks

    async def execute(self, ctx: HookContext) -> HookAction:
        # 获取 DispatchFilterService
        if ctx.services is None:
            return HookAction.CONTINUE

        service = ctx.services.get("dispatch_filter")
        if service is None:
            return HookAction.CONTINUE

        plugin_name = ctx.handler_entry.plugin_name
        if not plugin_name:
            return HookAction.CONTINUE

        # 提取 group_id / user_id
        data = ctx.event.data
        group_id = _extract_id(data, "group_id")
        user_id = _extract_id(data, "user_id", "sender_id")

        # 提取命令名（从 handler 上的 CommandHook）
        command = _extract_command(ctx.handler_entry.func)

        if service.is_blocked(plugin_name, command, group_id, user_id):
            LOG.debug(
                "拦截 handler %s (plugin=%s, cmd=%s, group=%s, user=%s)",
                ctx.handler_entry.func.__name__,
                plugin_name,
                command,
                group_id,
                user_id,
            )
            return HookAction.SKIP

        return HookAction.CONTINUE


def _extract_id(data: object, *attr_names: str) -> Optional[str]:
    """从事件数据中提取 ID（尝试多个属性名）。"""
    for name in attr_names:
        val = getattr(data, name, None)
        if val is not None:
            return str(val)
    return None


def _extract_command(func: object) -> Optional[str]:
    """从 handler 函数的 __hooks__ 中提取 CommandHook 的首个命令名。"""
    from .command_hook import CommandHook

    hooks = getattr(func, "__hooks__", [])
    for hook in hooks:
        if isinstance(hook, CommandHook):
            return hook.names[0] if hook.names else None
    return None
