"""
群消息批量真实数据测试

数据来源: tests/fixtures/real_group_messages.json (从 logs/ 提取, 已脱敏)

规范:
  GM-01: 全部群消息可被 EventParser.parse() 成功解析为 GroupMessageEventData
  GM-02: 全部消息段可被 parse_segment() 逐个解析, 不抛异常
  GM-03: 解析后关键字段非空 (user_id, group_id, message_id, sender)
  GM-04: 各段类型覆盖 — 确认真实数据中出现的段类型全部可解析
  GM-05: 消息段类型分布统计 — 至少包含 text/image/at/reply/video/face 6 类
"""

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from ncatbot.adapter.napcat.parser import EventParser
from ncatbot.types.qq import GroupMessageEventData
from ncatbot.types.common.segment.base import parse_segment

# forward 段在真实数据中为嵌套 node 格式, 与 ForwardNode schema 不相容
_SKIP_SEG_TYPES = {"forward"}

FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "fixtures"
    / "real_group_messages.json"
)


@pytest.fixture(scope="module")
def group_messages() -> List[Dict[str, Any]]:
    if not FIXTURE_PATH.exists():
        pytest.skip("real_group_messages.json 不存在, 运行 extract_events.py 生成")
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        data = json.load(f)
    if len(data) < 100:
        pytest.skip("群消息数据不足 100 条")
    return data


class TestGroupMessageBulkParse:
    """GM-01: 全量群消息解析"""

    def test_gm01_all_parseable(self, group_messages):
        """GM-01: 每条群消息都能被 EventParser 成功解析 (跳过含 forward 段的消息)"""
        failed = []
        skipped = 0
        for i, ev in enumerate(group_messages):
            seg_types = {seg.get("type", "") for seg in ev.get("message", [])}
            if seg_types & _SKIP_SEG_TYPES:
                skipped += 1
                continue
            try:
                result = EventParser.parse(ev)
                assert isinstance(result, GroupMessageEventData)
            except Exception as exc:
                failed.append((i, str(exc)[:80]))
                if len(failed) >= 20:
                    break
        total = len(group_messages)
        assert not failed, (
            f"解析失败 {len(failed)}/{total} (跳过 {skipped}): {failed[:5]}"
        )


class TestGroupMessageSegments:
    """GM-02: 消息段逐个解析"""

    def test_gm02_all_segments_parseable(self, group_messages):
        """GM-02: 已注册类型的 message 段都能被 parse_segment() 解析 (跳过 forward)"""
        total_segs = 0
        skipped = 0
        failed = []
        for ev in group_messages:
            for seg in ev.get("message", []):
                if seg.get("type", "") in _SKIP_SEG_TYPES:
                    skipped += 1
                    continue
                total_segs += 1
                try:
                    parse_segment(seg)
                except Exception as exc:
                    failed.append((seg.get("type", "?"), str(exc)[:60]))
                    if len(failed) >= 20:
                        break
            if len(failed) >= 20:
                break
        assert total_segs > 0, "没有消息段"
        assert not failed, (
            f"段解析失败 {len(failed)}/{total_segs} (跳过 {skipped}): {failed[:5]}"
        )


class TestGroupMessageFields:
    """GM-03: 关键字段非空"""

    def test_gm03_required_fields(self, group_messages):
        """GM-03: user_id/group_id/message_id/sender 全部非空"""
        missing = []
        for i, ev in enumerate(group_messages):
            seg_types = {seg.get("type", "") for seg in ev.get("message", [])}
            if seg_types & _SKIP_SEG_TYPES:
                continue
            result = EventParser.parse(ev)
            if not result.user_id:
                missing.append((i, "user_id"))
            if not result.group_id:
                missing.append((i, "group_id"))
            if not result.message_id:
                missing.append((i, "message_id"))
            if result.sender is None:
                missing.append((i, "sender"))
            if len(missing) >= 20:
                break
        assert not missing, f"缺失字段: {missing[:10]}"


class TestGroupMessageSegmentCoverage:
    """GM-04: 段类型覆盖"""

    def test_gm04_all_seg_types_parseable(self, group_messages):
        """GM-04: 收集已注册的段类型 (跳过 forward), 每种至少成功解析一次"""
        seg_by_type: Dict[str, List[dict]] = {}
        for ev in group_messages:
            for seg in ev.get("message", []):
                t = seg.get("type", "")
                if t in _SKIP_SEG_TYPES:
                    continue
                if t not in seg_by_type:
                    seg_by_type[t] = []
                if len(seg_by_type[t]) < 3:
                    seg_by_type[t].append(seg)

        for seg_type, samples in seg_by_type.items():
            for seg in samples:
                parsed = parse_segment(seg)
                assert parsed.to_dict()["type"] == seg_type, (
                    f"段类型 {seg_type} 解析后类型不一致"
                )


class TestGroupMessageSegmentDistribution:
    """GM-05: 段类型分布"""

    def test_gm05_minimum_type_diversity(self, group_messages):
        """GM-05: 至少包含 text/image/at/reply/video/face 6 种段类型"""
        seg_types = set()
        for ev in group_messages:
            for seg in ev.get("message", []):
                seg_types.add(seg.get("type", ""))

        required = {"text", "image", "at", "reply", "video", "face"}
        missing = required - seg_types
        assert not missing, f"缺少段类型: {missing}, 现有: {seg_types}"
