"""
HandlerDispatcher 全局 Hook + DispatchFilter 集成测试

规范:
  GH-01: 全局 BEFORE_CALL hook 在 handler 级 hook 之前执行
  GH-02: 全局 hook 返回 SKIP 跳过 handler
  GH-03: DispatchFilterHook 拦截被 deny 的 handler
  GH-04: DispatchFilterHook 不拦截无规则的 handler
"""

import asyncio


from ncatbot.adapter.mock.api import MockBotAPI
from ncatbot.core.dispatcher import AsyncEventDispatcher
from ncatbot.core.registry.dispatcher import HandlerDispatcher
from ncatbot.core.registry.dispatch_filter_hook import DispatchFilterHook
from ncatbot.core.registry.hook import Hook, HookAction, HookContext, HookStage
from ncatbot.service.manager import ServiceManager
from ncatbot.service.builtin.dispatch_filter.model import FilterRule
from ncatbot.testing.factories import qq as factory


class RecordHook(Hook):
    """记录执行顺序的 BEFORE_CALL hook"""

    stage = HookStage.BEFORE_CALL

    def __init__(self, name: str, order_list: list):
        self.hook_name = name
        self.order_list = order_list

    async def execute(self, ctx: HookContext) -> HookAction:
        self.order_list.append(self.hook_name)
        return HookAction.CONTINUE


class GlobalSkipHook(Hook):
    """总是跳过的全局 BEFORE_CALL hook"""

    stage = HookStage.BEFORE_CALL

    async def execute(self, ctx: HookContext) -> HookAction:
        return HookAction.SKIP


# ---- GH-01: 全局 hook 先于 handler 级 hook 执行 ----


async def test_global_hooks_execute_before_handler_hooks():
    """GH-01: 全局 BEFORE_CALL hook 在 handler 级 hook 之前执行"""
    order = []
    global_hook = RecordHook("global", order)
    handler_hook = RecordHook("handler", order)

    ed = AsyncEventDispatcher()
    hd = HandlerDispatcher(api=MockBotAPI(), global_hooks=[global_hook])
    hd.start(ed)

    called = asyncio.Event()

    @handler_hook
    async def handler(event):
        order.append("handler_exec")
        called.set()

    hd.register_handler("message.group", handler, plugin_name="test_plugin")
    await ed.callback(factory.group_message("hi", group_id="1"))
    await asyncio.sleep(0.1)

    assert called.is_set()
    assert order == ["global", "handler", "handler_exec"]
    await hd.stop()
    await ed.close()


# ---- GH-02: 全局 hook SKIP → 不执行 handler ----


async def test_global_hook_skip_blocks_handler():
    """GH-02: 全局 hook 返回 SKIP → handler 不执行"""
    ed = AsyncEventDispatcher()
    hd = HandlerDispatcher(api=MockBotAPI(), global_hooks=[GlobalSkipHook()])
    hd.start(ed)

    called = asyncio.Event()

    async def handler(event):
        called.set()

    hd.register_handler("message.group", handler, plugin_name="test_plugin")
    await ed.callback(factory.group_message("hi", group_id="1"))
    await asyncio.sleep(0.1)

    assert not called.is_set()
    await hd.stop()
    await ed.close()


# ---- GH-03: DispatchFilterHook 拦截 denied handler ----


async def test_dispatch_filter_hook_blocks_denied_handler():
    """GH-03: DispatchFilterHook + DispatchFilterService → SKIP 被 deny 的 handler"""
    sm = ServiceManager()
    sm.register(
        __import__(
            "ncatbot.service.builtin.dispatch_filter.service",
            fromlist=["DispatchFilterService"],
        ).DispatchFilterService,
        storage_path=None,
    )
    await sm.load_all()

    filter_svc = sm.get("dispatch_filter")
    filter_svc.add_rule(
        FilterRule(scope_type="group", scope_id="100", plugin_name="blocked_plugin")
    )

    ed = AsyncEventDispatcher()
    hd = HandlerDispatcher(
        api=MockBotAPI(),
        service_manager=sm,
        global_hooks=[DispatchFilterHook()],
    )
    hd.start(ed)

    called = asyncio.Event()

    async def handler(event):
        called.set()

    hd.register_handler("message.group", handler, plugin_name="blocked_plugin")
    await ed.callback(factory.group_message("hi", group_id="100", user_id="1"))
    await asyncio.sleep(0.1)

    assert not called.is_set()
    await hd.stop()
    await ed.close()
    await sm.close_all()


# ---- GH-04: DispatchFilterHook 通过未拦截的 handler ----


async def test_dispatch_filter_hook_passes_unblocked_handler():
    """GH-04: 无匹配规则 → handler 正常执行"""
    sm = ServiceManager()
    sm.register(
        __import__(
            "ncatbot.service.builtin.dispatch_filter.service",
            fromlist=["DispatchFilterService"],
        ).DispatchFilterService,
        storage_path=None,
    )
    await sm.load_all()

    ed = AsyncEventDispatcher()
    hd = HandlerDispatcher(
        api=MockBotAPI(),
        service_manager=sm,
        global_hooks=[DispatchFilterHook()],
    )
    hd.start(ed)

    called = asyncio.Event()

    async def handler(event):
        called.set()

    hd.register_handler("message.group", handler, plugin_name="allowed_plugin")
    await ed.callback(factory.group_message("hi", group_id="100", user_id="1"))
    await asyncio.sleep(0.1)

    assert called.is_set()
    await hd.stop()
    await ed.close()
    await sm.close_all()
