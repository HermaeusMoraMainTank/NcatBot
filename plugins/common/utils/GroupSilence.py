"""插件侧群消息静默策略。"""

from __future__ import annotations

from typing import Any

from ncatbot.core import Hook, HookAction, HookContext, HookStage


BLOCKED_GROUP_IDS = frozenset({"1090875633"})


def is_group_silenced(group_id: Any) -> bool:
    return group_id is not None and str(group_id) in BLOCKED_GROUP_IDS


class _GroupSilenceHook(Hook):
    stage = HookStage.BEFORE_CALL
    priority = 1000

    async def execute(self, ctx: HookContext) -> HookAction:
        data = getattr(ctx.event, "data", None)
        if is_group_silenced(getattr(data, "group_id", None)):
            return HookAction.SKIP
        return HookAction.CONTINUE


_GROUP_SILENCE_HOOK = _GroupSilenceHook()


class GroupSilenceMixin:
    """让插件显式加入群静默策略，不改动框架发送底层。"""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        for member in cls.__dict__.values():
            if getattr(member, "__handler_meta__", None) is None:
                continue
            hooks = getattr(member, "__hooks__", None)
            if hooks is not None and _GROUP_SILENCE_HOOK not in hooks:
                hooks.append(_GROUP_SILENCE_HOOK)
