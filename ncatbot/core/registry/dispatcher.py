"""
HandlerDispatcher — 纯机械分发器

订阅 AsyncEventDispatcher 的事件流，分发到已注册的 handlers。
不含任何 Session 逻辑（Session 完全通过 Hook 实现）。

事件类型格式使用 BaseEventData.resolve_type() 产出的格式:
  "message.group"、"notice.group_increase" 等。
前缀匹配: 注册 "message" 可匹配 "message.group" 和 "message.private"。
"""

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING
from ncatbot.event import create_entity as platform_create_entity
from ncatbot.utils import get_log

from .hook import Hook, HookAction, HookContext, HookStage, get_hooks

if TYPE_CHECKING:
    from ncatbot.api import IAPIClient
    from ..dispatcher import AsyncEventDispatcher, Event
    from ..dispatcher.stream import EventStream
    from ncatbot.service import ServiceManager

LOG = get_log("HandlerDispatcher")


@dataclass
class HandlerEntry:
    """已注册的 handler 条目"""

    func: Callable
    event_type: str
    priority: int = 0
    plugin_name: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class HandlerDispatcher:
    """
    纯机械分发器

    - 订阅 AsyncEventDispatcher 事件流消费事件
    - 收集匹配的 handlers（精确 + 前缀匹配）
    - 对每个 handler: BEFORE_CALL hooks → 执行 → AFTER_CALL hooks
    - revoke_plugin() 支持热重载精确清理
    """

    def __init__(
        self,
        api: Optional["IAPIClient"] = None,
        service_manager: Optional["ServiceManager"] = None,
        platform_apis: Optional[Dict[str, "IAPIClient"]] = None,
        global_hooks: Optional[List["Hook"]] = None,
    ):
        self._handlers: Dict[str, List[HandlerEntry]] = {}
        self._api = api
        self._service_manager = service_manager
        self._platform_apis: Dict[str, "IAPIClient"] = dict(platform_apis or {})
        self._stream: Optional["EventStream"] = None
        self._task: Optional[asyncio.Task] = None
        self._dispatch_tasks: set[asyncio.Task] = set()
        self._global_before_hooks: List["Hook"] = list(global_hooks or [])

    def set_platform_api(self, platform: str, api: "IAPIClient") -> None:
        """注册平台特定 API，用于事件实体注入"""
        self._platform_apis[platform] = api

    # ==================== 生命周期 ====================

    def start(self, event_dispatcher: "AsyncEventDispatcher") -> None:
        """订阅事件流并启动消费循环。

        在 asyncio 事件循环中作为后台 task 运行。
        """
        self._stream = event_dispatcher.events()  # 全部事件，不过滤
        self._task = asyncio.create_task(self._consume())

    async def stop(self) -> None:
        """停止消费循环并关闭事件流。"""
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        # 等待所有进行中的 dispatch task 完成
        if self._dispatch_tasks:
            for t in self._dispatch_tasks:
                t.cancel()
            await asyncio.gather(*self._dispatch_tasks, return_exceptions=True)
            self._dispatch_tasks.clear()
        if self._stream:
            await self._stream.aclose()
            self._stream = None

    async def _consume(self) -> None:
        """后台消费事件流，并发分发事件。"""
        assert self._stream is not None
        try:
            async for event in self._stream:
                task = asyncio.create_task(self._safe_dispatch(event))
                self._dispatch_tasks.add(task)
                task.add_done_callback(self._dispatch_tasks.discard)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOG.exception("事件消费循环异常")

    async def _safe_dispatch(self, event: "Event") -> None:
        """包装 _dispatch，捕获异常防止 task 静默失败。"""
        try:
            await self._dispatch(event)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOG.exception("事件分发异常 (type=%s)", event.type)

    # ==================== Handler 管理 ====================

    def register_handler(
        self,
        event_type: str,
        func: Callable,
        priority: int = 0,
        plugin_name: str = "",
        **metadata: Any,
    ) -> Optional[HandlerEntry]:
        """注册 handler 到分发器，handler 必须是 async 函数。"""
        if not asyncio.iscoroutinefunction(func):
            LOG.error(
                "跳过同步 handler: %s (plugin=%s)，handler 必须是 async 函数",
                func.__name__,
                plugin_name,
            )
            return None

        entry = HandlerEntry(
            func=func,
            event_type=event_type,
            priority=priority,
            plugin_name=plugin_name,
            metadata=metadata,
        )

        if event_type not in self._handlers:
            self._handlers[event_type] = []

        self._handlers[event_type].append(entry)
        # 按优先级降序排序
        self._handlers[event_type].sort(key=lambda e: -e.priority)

        LOG.debug(
            "注册 handler: %s → %s (priority=%d, plugin=%s)",
            func.__name__,
            event_type,
            priority,
            plugin_name,
        )
        return entry

    def unregister_handler(self, entry: HandlerEntry) -> bool:
        """移除单个 handler"""
        handlers = self._handlers.get(entry.event_type, [])
        try:
            handlers.remove(entry)
            if not handlers:
                del self._handlers[entry.event_type]
            return True
        except ValueError:
            return False

    def revoke_plugin(self, plugin_name: str) -> int:
        """热重载用: 移除该插件的所有 handler，返回移除数量"""
        removed = 0
        for event_type in list(self._handlers.keys()):
            original_len = len(self._handlers[event_type])
            self._handlers[event_type] = [
                e for e in self._handlers[event_type] if e.plugin_name != plugin_name
            ]
            removed += original_len - len(self._handlers[event_type])
            if not self._handlers[event_type]:
                del self._handlers[event_type]

        if removed:
            LOG.info("revoke_plugin(%s): 移除 %d 个 handler", plugin_name, removed)
        return removed

    # ==================== 核心分发 ====================

    async def _dispatch(self, event: "Event") -> None:
        """接收 Event 并分发到匹配的 handlers。"""
        event_type = event.type

        # 将数据模型包装为事件实体（如 GroupMessageEvent），供 handler 使用
        # 优先使用平台感知的 create_entity（根据 data.platform 路由到对应平台工厂）
        # 选择平台特定 API 注入到事件实体
        platform = getattr(event.data, "platform", "unknown")
        api = self._platform_apis.get(platform, self._api)
        entity = platform_create_entity(event.data, api) if api else event

        # snapshot 防止并发修改
        handlers = self._collect_handlers(event_type)
        LOG.debug(
            "_dispatch(%s): 收集到 %d 个 handler — %s",
            event_type,
            len(handlers),
            [(e.func.__name__, e.event_type, e.plugin_name, id(e)) for e in handlers],
        )

        for entry in handlers:
            if getattr(event.data, "_propagation_stopped", False):
                break

            ctx = HookContext(
                event=event,
                event_type=event_type,
                handler_entry=entry,
                api=api,
                services=self._service_manager,
            )

            # --- 全局 BEFORE_CALL hooks ---
            skip = False
            for hook in self._global_before_hooks:
                try:
                    action = await hook.execute(ctx)
                    if action == HookAction.SKIP:
                        skip = True
                        break
                except Exception as e:
                    LOG.exception(
                        "全局 BEFORE_CALL hook %s 执行异常 (handler=%s): %s",
                        hook,
                        entry.func.__name__,
                        e,
                    )
                    skip = True
                    break
            if skip:
                continue

            # --- handler 级 BEFORE_CALL hooks ---
            before_hooks = get_hooks(entry.func, HookStage.BEFORE_CALL)
            skip = False
            for hook in before_hooks:
                try:
                    action = await hook.execute(ctx)
                    if action == HookAction.SKIP:
                        skip = True
                        break
                except Exception as e:
                    LOG.exception(
                        "BEFORE_CALL hook %s 执行异常 (handler=%s): %s",
                        hook,
                        entry.func.__name__,
                        e,
                    )
                    skip = True
                    break
            if skip:
                continue

            # --- 执行 handler ---
            LOG.debug(
                "_dispatch: 执行 handler %s (plugin=%s, event_type=%s, id=%d)",
                entry.func.__name__,
                entry.plugin_name,
                entry.event_type,
                id(entry),
            )
            try:
                result = await self._execute(entry, entity, **ctx.kwargs)
                ctx.result = result

                # --- AFTER_CALL hooks ---
                after_hooks = get_hooks(entry.func, HookStage.AFTER_CALL)
                for hook in after_hooks:
                    try:
                        await hook.execute(ctx)
                    except Exception as e:
                        LOG.exception(
                            "AFTER_CALL hook %s 执行异常 (handler=%s): %s",
                            hook,
                            entry.func.__name__,
                            e,
                        )

            except Exception as e:
                ctx.error = e
                # --- ON_ERROR hooks ---
                error_hooks = get_hooks(entry.func, HookStage.ON_ERROR)
                handled = False
                for hook in error_hooks:
                    try:
                        await hook.execute(ctx)
                        handled = True
                    except Exception as hook_err:
                        LOG.exception("ON_ERROR hook %s 自身异常: %s", hook, hook_err)
                if not handled:
                    LOG.exception("Handler %s 执行异常: %s", entry.func.__name__, e)

    def _collect_handlers(self, event_type: str) -> List[HandlerEntry]:
        """收集匹配的 handlers (snapshot)。

        匹配规则:
        - 精确匹配: "message.group" 匹配注册了 "message.group" 的 handler
        - 前缀匹配: "message.group" 也匹配注册了 "message" 的 handler
        """
        # 精确匹配
        result = list(self._handlers.get(event_type, []))

        # 前缀匹配: 逐级截断
        parts = event_type.split(".")
        for i in range(len(parts) - 1, 0, -1):
            prefix = ".".join(parts[:i])
            result.extend(self._handlers.get(prefix, []))

        result.sort(key=lambda e: -e.priority)
        return result

    @staticmethod
    async def _execute(entry: HandlerEntry, entity: Any, **kwargs: Any) -> Any:
        """执行 handler，注入 plugin 实例 (如果是方法) + kwargs"""
        func = entry.func
        plugin = entry.metadata.get("plugin_instance")

        if plugin:
            return await func(plugin, entity, **kwargs)
        else:
            return await func(entity, **kwargs)

    # ==================== 查询 ====================

    def get_handlers(self, event_type: str) -> List[HandlerEntry]:
        """获取指定事件类型的 handlers"""
        return list(self._handlers.get(event_type, []))

    def get_all_handlers(self) -> Dict[str, List[HandlerEntry]]:
        """获取所有 handlers"""
        return {k: list(v) for k, v in self._handlers.items()}
