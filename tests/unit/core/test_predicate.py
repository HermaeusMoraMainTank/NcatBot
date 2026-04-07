"""
Predicate DSL 单元测试

PR-01 ~ PR-06：覆盖组合运算、工厂函数、from_event 推导。
"""

from ncatbot.core.dispatcher.event import Event
from ncatbot.core.dispatcher.predicate import (
    P,
    AndP,
    OrP,
    NotP,
    same_user,
    msg_equals,
    msg_matches,
    from_event,
)


class _FakeData:
    """轻量级 fake event data"""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def _make_event(event_type: str, **data_kwargs) -> Event:
    return Event(type=event_type, data=_FakeData(**data_kwargs))


class TestPredicateCombination:
    """PR-01: AND/OR/NOT 组合求值"""

    def test_pr01_and_or_not(self):
        """PR-01: AND/OR/NOT 组合求值"""
        always_true = P.of(lambda e: True)
        always_false = P.of(lambda e: False)
        evt = _make_event("message.group")

        # AND
        assert (always_true * always_true)(evt)
        assert not (always_true * always_false)(evt)
        assert isinstance(always_true * always_true, AndP)

        # OR
        assert (always_true + always_false)(evt)
        assert not (always_false + always_false)(evt)
        assert isinstance(always_true + always_false, OrP)

        # NOT
        assert (~always_false)(evt)
        assert not (~always_true)(evt)
        assert isinstance(~always_true, NotP)

        # 复合
        assert (always_true * always_true + ~always_true)(evt)


class TestPredicateFactories:
    """PR-02 ~ PR-04: 预置工厂函数"""

    def test_pr02_same_user(self):
        """PR-02: same_user() 匹配 user_id"""
        p = same_user("12345")
        evt_match = _make_event("message.group", user_id="12345")
        evt_no = _make_event("message.group", user_id="99999")
        evt_none = _make_event("message.group")

        assert p(evt_match)
        assert not p(evt_no)
        assert not p(evt_none)

    def test_pr03_msg_equals(self):
        """PR-03: msg_equals() 文本匹配（含 trim）"""
        p = msg_equals("hello")
        assert p(_make_event("message.group", raw_message="hello"))
        assert p(_make_event("message.group", raw_message="  hello  "))
        assert not p(_make_event("message.group", raw_message="hello world"))
        assert not p(_make_event("message.group"))  # 无 raw_message

    def test_pr04_msg_matches(self):
        """PR-04: msg_matches() 正则匹配"""
        p = msg_matches(r"^\d{3,}$")
        assert p(_make_event("message.group", raw_message="12345"))
        assert not p(_make_event("message.group", raw_message="ab"))
        assert not p(_make_event("notice.group_increase"))  # 无 raw_message


class TestFromEvent:
    """PR-05 ~ PR-06: from_event 自动推导"""

    def test_pr05_from_group_event(self):
        """PR-05: from_event 群消息 → group+user 组合谓词"""
        origin = _make_event("message.group", user_id="111", group_id="222")
        pred = from_event(origin)

        # 同 session：命中
        same = _make_event("message.group", user_id="111", group_id="222")
        assert pred(same)

        # 不同用户：不匹配
        diff_user = _make_event("message.group", user_id="999", group_id="222")
        assert not pred(diff_user)

        # 不同群：不匹配
        diff_group = _make_event("message.group", user_id="111", group_id="333")
        assert not pred(diff_group)

    def test_pr06_from_private_event(self):
        """PR-06: from_event 私聊 → user+private 谓词"""
        origin = _make_event("message.private", user_id="111")
        pred = from_event(origin)

        same = _make_event("message.private", user_id="111")
        assert pred(same)

        diff = _make_event("message.private", user_id="999")
        assert not pred(diff)

        # 群消息不匹配
        group = _make_event("message.group", user_id="111", group_id="222")
        assert not pred(group)
