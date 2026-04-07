"""
真实日志夹具事件解析测试

数据来源: 从 logs/ 提取的真实 OB11 WebSocket 事件，已脱敏存于
tests/fixtures/real_events.json (由 extract_events.py 生成)。

规范:
  RF-01: 群消息完整解析
  RF-02: 私聊消息完整解析
  RF-03: heartbeat / lifecycle 元事件解析
  RF-04: 群文件上传 notice 解析（含 FileInfo 子模型）
  RF-05: 群成员增减 / 禁言 / 撤回 / 戳一戳 notice 解析
  RF-06: lift_ban 变体（duration=0）解析
  RF-07: 未注册事件类型（group_card）正确抛 ValueError
  RF-08: 全部可解析事件一致性校验
"""

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from ncatbot.adapter.napcat.parser import EventParser
from ncatbot.types.common.segment.base import parse_segment, SEGMENT_MAP
from ncatbot.types.qq import (
    GroupMessageEventData,
    PrivateMessageEventData,
    HeartbeatMetaEventData,
    LifecycleMetaEventData,
    GroupUploadNoticeEventData,
    GroupIncreaseNoticeEventData,
    GroupBanNoticeEventData,
    GroupRecallNoticeEventData,
    GroupDecreaseNoticeEventData,
)
from ncatbot.types.qq.notice import NotifyEventData
from ncatbot.types.qq import NoticeNotifySubType

FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent.parent / "fixtures" / "real_events.json"
)


@pytest.fixture(scope="module")
def all_events() -> List[Dict[str, Any]]:
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        return json.load(f)


def _find(events, post_type, sub_val=None, sub_type=None):
    """按 post_type + 二级类型 + sub_type 查找夹具事件"""
    key_field = {
        "message": "message_type",
        "meta_event": "meta_event_type",
        "notice": "notice_type",
        "request": "request_type",
    }.get(post_type, "")
    for ev in events:
        if ev.get("post_type") != post_type:
            continue
        if sub_val and ev.get(key_field) != sub_val:
            continue
        if sub_type and ev.get("sub_type") != sub_type:
            continue
        return ev
    pytest.skip(f"夹具中不存在 {post_type}/{sub_val}/{sub_type}")


# ── RF-01: 群消息 ────────────────────────────────────────────


class TestRealGroupMessage:
    """RF-01: 群消息完整解析"""

    def test_rf01_group_message(self, all_events):
        raw = _find(all_events, "message", "group")
        result = EventParser.parse(raw)

        assert isinstance(result, GroupMessageEventData)
        assert result.group_id
        assert result.user_id
        assert result.message_id
        assert result.sender is not None
        assert result.sender.role in ("owner", "admin", "member")
        # 已注册的段类型可逐个 parse
        for seg in raw["message"]:
            if seg["type"] in SEGMENT_MAP:
                parsed = parse_segment(seg)
                assert parsed.to_dict()["type"] == seg["type"]


# ── RF-02: 私聊消息 ──────────────────────────────────────────


class TestRealPrivateMessage:
    """RF-02: 私聊消息完整解析"""

    def test_rf02_private_message(self, all_events):
        raw = _find(all_events, "message", "private")
        result = EventParser.parse(raw)

        assert isinstance(result, PrivateMessageEventData)
        assert result.user_id
        assert result.message_id
        assert len(raw["message"]) >= 1


# ── RF-03: 元事件 ────────────────────────────────────────────


class TestRealMetaEvents:
    """RF-03: heartbeat / lifecycle 元事件"""

    def test_rf03_heartbeat(self, all_events):
        raw = _find(all_events, "meta_event", "heartbeat")
        result = EventParser.parse(raw)

        assert isinstance(result, HeartbeatMetaEventData)
        assert result.interval == 30000
        assert result.status.online is True

    def test_rf03_lifecycle(self, all_events):
        raw = _find(all_events, "meta_event", "lifecycle")
        result = EventParser.parse(raw)

        assert isinstance(result, LifecycleMetaEventData)
        assert result.sub_type == "connect"


# ── RF-04: 群文件上传 ────────────────────────────────────────


class TestRealGroupUpload:
    """RF-04: 群文件上传 notice（含 FileInfo 子模型）"""

    def test_rf04_group_upload(self, all_events):
        raw = _find(all_events, "notice", "group_upload")
        result = EventParser.parse(raw)

        assert isinstance(result, GroupUploadNoticeEventData)
        assert result.group_id
        assert result.file.name
        assert result.file.size > 0


# ── RF-05: 群通知事件 ────────────────────────────────────────


class TestRealGroupNotices:
    """RF-05: 群成员增减 / 禁言 / 撤回 / 戳一戳"""

    def test_rf05_group_increase(self, all_events):
        raw = _find(all_events, "notice", "group_increase")
        result = EventParser.parse(raw)

        assert isinstance(result, GroupIncreaseNoticeEventData)
        assert result.sub_type == "approve"
        assert result.operator_id

    def test_rf05_group_ban(self, all_events):
        raw = _find(all_events, "notice", "group_ban", sub_type="ban")
        result = EventParser.parse(raw)

        assert isinstance(result, GroupBanNoticeEventData)
        assert result.sub_type == "ban"
        assert result.duration > 0
        assert result.operator_id

    def test_rf05_group_recall(self, all_events):
        raw = _find(all_events, "notice", "group_recall")
        result = EventParser.parse(raw)

        assert isinstance(result, GroupRecallNoticeEventData)
        assert result.operator_id
        assert result.message_id

    def test_rf05_group_decrease(self, all_events):
        raw = _find(all_events, "notice", "group_decrease")
        result = EventParser.parse(raw)

        assert isinstance(result, GroupDecreaseNoticeEventData)
        assert result.sub_type == "leave"

    def test_rf05_poke(self, all_events):
        raw = _find(all_events, "notice", "notify", sub_type="poke")
        result = EventParser.parse(raw)

        assert isinstance(result, NotifyEventData)
        assert result.sub_type == NoticeNotifySubType.POKE
        assert result.user_id


# ── RF-06: lift_ban 变体 ─────────────────────────────────────


class TestRealLiftBan:
    """RF-06: 解禁（duration=0）正确解析"""

    def test_rf06_lift_ban(self, all_events):
        raw = _find(all_events, "notice", "group_ban", sub_type="lift_ban")
        result = EventParser.parse(raw)

        assert isinstance(result, GroupBanNoticeEventData)
        assert result.sub_type == "lift_ban"
        assert result.duration == 0


# ── RF-07: 未注册事件 ────────────────────────────────────────


class TestRealUnregisteredEvent:
    """RF-07: 未注册事件类型（group_card）抛 ValueError"""

    def test_rf07_group_card_raises(self, all_events):
        raw = _find(all_events, "notice", "group_card")
        with pytest.raises(ValueError, match="No data class registered"):
            EventParser.parse(raw)


# ── RF-08: 全量一致性 ────────────────────────────────────────


class TestRealBulkConsistency:
    """RF-08: 全部可解析事件 post_type 一致"""

    def test_rf08_all_parseable_consistent(self, all_events):
        parseable = [ev for ev in all_events if ev.get("notice_type") != "group_card"]
        failed = []
        for i, ev in enumerate(parseable):
            try:
                result = EventParser.parse(ev)
                expected = ev["post_type"]
                if expected == "message_sent":
                    expected = "message"
                assert result.post_type.value == expected
            except Exception as exc:
                failed.append((i, ev.get("post_type"), str(exc)[:60]))

        assert not failed, f"一致性校验失败: {failed}"
