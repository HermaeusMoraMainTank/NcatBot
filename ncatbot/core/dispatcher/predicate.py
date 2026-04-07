"""
Predicate 语法糖系统

为 ``wait_event`` 的 ``predicate`` 参数提供可组合的声明式 DSL，
替代冗长的 lambda 表达式。

运算符
------
- ``p1 * p2`` / ``p1 & p2`` — AND（全部满足）
- ``p1 + p2`` / ``p1 | p2`` — OR（任一满足）
- ``~p`` — NOT（取反）

快速开始::

    from ncatbot.core import from_event, msg_equals

    # 等待同 session 的回复
    evt = await self.wait_event(predicate=from_event(event), timeout=30)

    # 同 session + 精确匹配
    evt = await self.wait_event(
        predicate=from_event(event) * msg_equals("确认"),
        timeout=15,
    )
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Callable, Sequence, Union

from ncatbot.types.qq import MessageType

if TYPE_CHECKING:
    from .event import Event

__all__ = [
    # 基类 & 组合
    "P",
    "AndP",
    "OrP",
    "NotP",
    # 工厂函数
    "same_user",
    "same_group",
    "is_private",
    "is_group",
    "is_message",
    "has_keyword",
    "msg_equals",
    "msg_in",
    "msg_matches",
    "event_type",
    "from_event",
]


# ---------------------------------------------------------------------------
# 基类
# ---------------------------------------------------------------------------


class P(ABC):
    """Predicate 抽象基类，可直接作为 ``Callable[[Event], bool]`` 传给 ``wait_event``。"""

    @abstractmethod
    def __call__(self, event: "Event") -> bool: ...

    # ---- 组合运算符 ----

    def __mul__(self, other: Union["P", Callable[["Event"], bool]]) -> "AndP":
        """``p1 * p2`` → AND"""
        return AndP(self, _ensure_p(other))

    def __and__(self, other: Union["P", Callable[["Event"], bool]]) -> "AndP":
        """``p1 & p2`` → AND"""
        return AndP(self, _ensure_p(other))

    def __add__(self, other: Union["P", Callable[["Event"], bool]]) -> "OrP":
        """``p1 + p2`` → OR"""
        return OrP(self, _ensure_p(other))

    def __or__(self, other: Union["P", Callable[["Event"], bool]]) -> "OrP":
        """``p1 | p2`` → OR"""
        return OrP(self, _ensure_p(other))

    def __invert__(self) -> "NotP":
        """``~p`` → NOT"""
        return NotP(self)

    # ---- 工具方法 ----

    @classmethod
    def of(cls, fn: Callable[["Event"], bool]) -> "P":
        """将普通 callable / lambda 升级为 :class:`P` 实例，使其支持运算符组合。"""
        if isinstance(fn, P):
            return fn
        return _LambdaP(fn)


# ---------------------------------------------------------------------------
# 组合类
# ---------------------------------------------------------------------------


class AndP(P):
    """全部子谓词均满足。"""

    __slots__ = ("_children",)

    def __init__(self, *children: P) -> None:
        # 展平嵌套 AndP，减少调用层级
        flat: list[P] = []
        for c in children:
            if isinstance(c, AndP):
                flat.extend(c._children)
            else:
                flat.append(c)
        self._children: tuple[P, ...] = tuple(flat)

    def __call__(self, event: "Event") -> bool:
        return all(c(event) for c in self._children)

    def __repr__(self) -> str:
        inner = ", ".join(repr(c) for c in self._children)
        return f"AndP({inner})"


class OrP(P):
    """任一子谓词满足。"""

    __slots__ = ("_children",)

    def __init__(self, *children: P) -> None:
        flat: list[P] = []
        for c in children:
            if isinstance(c, OrP):
                flat.extend(c._children)
            else:
                flat.append(c)
        self._children: tuple[P, ...] = tuple(flat)

    def __call__(self, event: "Event") -> bool:
        return any(c(event) for c in self._children)

    def __repr__(self) -> str:
        inner = ", ".join(repr(c) for c in self._children)
        return f"OrP({inner})"


class NotP(P):
    """取反。"""

    __slots__ = ("_inner",)

    def __init__(self, inner: P) -> None:
        self._inner = inner

    def __call__(self, event: "Event") -> bool:
        return not self._inner(event)

    def __repr__(self) -> str:
        return f"NotP({self._inner!r})"


class _LambdaP(P):
    """包装普通 callable 为 :class:`P`。"""

    __slots__ = ("_fn",)

    def __init__(self, fn: Callable[["Event"], bool]) -> None:
        self._fn = fn

    def __call__(self, event: "Event") -> bool:
        return self._fn(event)

    def __repr__(self) -> str:
        return f"P.of({self._fn!r})"


def _ensure_p(obj: Union[P, Callable[["Event"], bool]]) -> P:
    """确保对象为 P 实例。"""
    if isinstance(obj, P):
        return obj
    return _LambdaP(obj)


# ---------------------------------------------------------------------------
# 安全属性访问辅助
# ---------------------------------------------------------------------------


def _safe_raw_message(event: "Event") -> str | None:
    """安全获取 raw_message，不存在返回 None。"""
    data = event.data
    if hasattr(data, "raw_message"):
        return data.raw_message  # type: ignore[union-attr]
    return None


# ---------------------------------------------------------------------------
# 预置工厂函数
# ---------------------------------------------------------------------------


class _SameUser(P):
    __slots__ = ("_uid",)

    def __init__(self, uid: str) -> None:
        self._uid = uid

    def __call__(self, event: "Event") -> bool:
        return hasattr(event.data, "user_id") and str(event.data.user_id) == self._uid  # type: ignore[union-attr]

    def __repr__(self) -> str:
        return f"same_user({self._uid!r})"


def same_user(user_id: Union[str, int]) -> P:
    """匹配 ``event.data.user_id``。"""
    return _SameUser(str(user_id))


class _SameGroup(P):
    __slots__ = ("_gid",)

    def __init__(self, gid: str) -> None:
        self._gid = gid

    def __call__(self, event: "Event") -> bool:
        return hasattr(event.data, "group_id") and str(event.data.group_id) == self._gid  # type: ignore[union-attr]

    def __repr__(self) -> str:
        return f"same_group({self._gid!r})"


def same_group(group_id: Union[str, int]) -> P:
    """匹配 ``event.data.group_id``。"""
    return _SameGroup(str(group_id))


class _EventType(P):
    __slots__ = ("_prefix",)

    def __init__(self, prefix: str) -> None:
        self._prefix = prefix

    def __call__(self, event: "Event") -> bool:
        t = event.type
        return t == self._prefix or t.startswith(self._prefix + ".")

    def __repr__(self) -> str:
        return f"event_type({self._prefix!r})"


def event_type(prefix: str) -> P:
    """匹配 ``event.type`` 前缀（含精确匹配），如 ``"message"``、``"message.group"``。"""
    return _EventType(prefix)


# 单例实例，避免重复创建

_IS_PRIVATE = _EventType("message.private")
_IS_GROUP = _EventType("message.group")
_IS_MESSAGE = _EventType("message")


def is_private() -> P:
    """事件类型为私聊消息。"""
    return _IS_PRIVATE


def is_group() -> P:
    """事件类型为群消息。"""
    return _IS_GROUP


def is_message() -> P:
    """事件类型为消息（群或私聊）。"""
    return _IS_MESSAGE


class _HasKeyword(P):
    __slots__ = ("_words",)

    def __init__(self, words: Sequence[str]) -> None:
        self._words = tuple(words)

    def __call__(self, event: "Event") -> bool:
        text = _safe_raw_message(event)
        if text is None:
            return False
        return any(w in text for w in self._words)

    def __repr__(self) -> str:
        inner = ", ".join(repr(w) for w in self._words)
        return f"has_keyword({inner})"


def has_keyword(*words: str) -> P:
    """``raw_message`` 包含任一关键词。"""
    return _HasKeyword(words)


class _MsgEquals(P):
    __slots__ = ("_text",)

    def __init__(self, text: str) -> None:
        self._text = text

    def __call__(self, event: "Event") -> bool:
        raw = _safe_raw_message(event)
        if raw is None:
            return False
        return raw.strip() == self._text

    def __repr__(self) -> str:
        return f"msg_equals({self._text!r})"


def msg_equals(text: str) -> P:
    """``raw_message.strip()`` 完全匹配。"""
    return _MsgEquals(text)


class _MsgIn(P):
    __slots__ = ("_options",)

    def __init__(self, options: Sequence[str]) -> None:
        self._options = frozenset(options)

    def __call__(self, event: "Event") -> bool:
        raw = _safe_raw_message(event)
        if raw is None:
            return False
        return raw.strip() in self._options

    def __repr__(self) -> str:
        inner = ", ".join(repr(o) for o in sorted(self._options))
        return f"msg_in({inner})"


def msg_in(*options: str) -> P:
    """``raw_message.strip()`` 匹配选项之一，等价于多个 ``msg_equals`` 的 OR。"""
    return _MsgIn(options)


class _MsgMatches(P):
    __slots__ = ("_pattern", "_compiled")

    def __init__(self, pattern: str) -> None:
        self._pattern = pattern
        self._compiled = re.compile(pattern)

    def __call__(self, event: "Event") -> bool:
        raw = _safe_raw_message(event)
        if raw is None:
            return False
        return self._compiled.search(raw) is not None

    def __repr__(self) -> str:
        return f"msg_matches({self._pattern!r})"


def msg_matches(pattern: str) -> P:
    """``raw_message`` 正则匹配（search 模式）。"""
    return _MsgMatches(pattern)


# ---------------------------------------------------------------------------
# 核心语法糖：from_event
# ---------------------------------------------------------------------------


def from_event(event: object) -> P:
    """从触发事件自动推导「同 session」谓词。

    - **群消息事件** → ``same_user(uid) * same_group(gid) * is_group()``
    - **私聊消息事件** → ``same_user(uid) * is_private()``
    - **其他带 user_id 的事件** → ``same_user(uid) * is_message()``

    同时支持高层事件对象（``BaseEvent``，有 ``.user_id``）
    和底层 ``Event``（有 ``.data.user_id``）。
    """
    # 统一提取 user_id 和 group_id
    uid: str | None = None
    gid: str | None = None
    is_group_msg = False
    is_private_msg = False

    # 高层事件 (BaseEvent 及其子类): 有 .user_id 属性
    if hasattr(event, "user_id") and not hasattr(event, "type"):
        uid = str(event.user_id)  # type: ignore[union-attr]
        if hasattr(event, "group_id"):
            gid = str(event.group_id)  # type: ignore[union-attr]
            is_group_msg = True
        elif hasattr(event, "message_type"):
            is_private_msg = event.message_type is MessageType.PRIVATE  # type: ignore[union-attr]

    # 底层 Event (dispatcher Event): 有 .type 和 .data
    elif hasattr(event, "type") and hasattr(event, "data"):
        data = event.data  # type: ignore[union-attr]
        event_type_str: str = event.type  # type: ignore[union-attr]

        if hasattr(data, "user_id"):
            uid = str(data.user_id)
        if hasattr(data, "group_id"):
            gid = str(data.group_id)

        is_group_msg = event_type_str == "message.group"
        is_private_msg = event_type_str == "message.private"

    if uid is None:
        raise ValueError(
            f"from_event: 无法从 {type(event).__name__} 中提取 user_id，"
            "请手动组合 predicate"
        )

    if is_group_msg and gid is not None:
        return same_user(uid) * same_group(gid) * is_group()
    if is_private_msg:
        return same_user(uid) * is_private()
    # fallback: 只匹配用户 + 消息事件
    return same_user(uid) * is_message()
