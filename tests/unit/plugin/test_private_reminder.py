from __future__ import annotations

import asyncio
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "plugins"))

from PrivateReminder.PrivateReminder import (
    PrivateReminder,
    format_remaining,
    parse_reminder_time,
)
from PrivateReminder.render import render_help, render_list, render_notice, render_reminder

TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 11, 4, 20, 0, tzinfo=TZ)


def _plugin() -> PrivateReminder:
    plugin = PrivateReminder.__new__(PrivateReminder)
    plugin.data = {"reminders": [], "next_id": 1}
    plugin._timezone = TZ
    plugin._reminder_lock = asyncio.Lock()
    plugin._now = lambda: NOW
    plugin.get_config = lambda key, default=None: default
    plugin._save_data = lambda: None
    plugin._reply = AsyncMock()
    plugin.image_dir = Path(tempfile.mkdtemp(prefix="private_reminder_test_"))
    return plugin


def _event(group_id: str | None = None):
    return SimpleNamespace(
        sender=SimpleNamespace(user_id="100"),
        group_id=group_id,
        raw_message="",
        reply=AsyncMock(),
    )


def test_reminder_time_parser_supports_common_formats():
    due, content = parse_reminder_time("21:30 收鱼", NOW)
    assert due == datetime(2026, 11, 4, 21, 30, tzinfo=TZ)
    assert content == "收鱼"

    due, content = parse_reminder_time("19:00 收鱼", NOW)
    assert due == datetime(2026, 11, 5, 19, 0, tzinfo=TZ)

    due, content = parse_reminder_time("明天 08:00 起床", NOW)
    assert due == datetime(2026, 11, 5, 8, 0, tzinfo=TZ)
    assert content == "起床"

    due, content = parse_reminder_time("11-05 08:00 收鱼", NOW)
    assert due == datetime(2026, 11, 5, 8, 0, tzinfo=TZ)

    due, content = parse_reminder_time("2026-11-05 08:00 收鱼", NOW)
    assert due == datetime(2026, 11, 5, 8, 0, tzinfo=TZ)

    due, content = parse_reminder_time("30分钟 喝水", NOW)
    assert due == NOW + timedelta(minutes=30)
    assert content == "喝水"

    due, content = parse_reminder_time("21:30", NOW)
    assert due == datetime(2026, 11, 4, 21, 30, tzinfo=TZ)
    assert content == "时间到了"


def test_reminder_time_parser_accepts_space_free_chinese_dates():
    expected = datetime(2026, 11, 5, 9, 0, tzinfo=TZ)
    for raw in ("明天9点", "明天9:00", "明天09:00"):
        due, content = parse_reminder_time(raw, NOW)
        assert due == expected, raw
        assert content == "时间到了"

    due, content = parse_reminder_time("明天9点30分", NOW)
    assert due == datetime(2026, 11, 5, 9, 30, tzinfo=TZ)
    assert content == "时间到了"

    due, content = parse_reminder_time("明天9点30分收鱼", NOW)
    assert due == datetime(2026, 11, 5, 9, 30, tzinfo=TZ)
    assert content == "收鱼"

    for raw in ("11月5日 9点30分", "11月5日9点30分"):
        due, content = parse_reminder_time(raw, NOW)
        assert due == datetime(2026, 11, 5, 9, 30, tzinfo=TZ), raw
        assert content == "时间到了"

    for raw in ("11月5号9点03", "11月5号 9点03"):
        due, content = parse_reminder_time(raw, NOW)
        assert due == datetime(2026, 11, 5, 9, 3, tzinfo=TZ), raw
        assert content == "时间到了"

    due, content = parse_reminder_time("2026年11月5日9点30分", NOW)
    assert due == datetime(2026, 11, 5, 9, 30, tzinfo=TZ)
    assert content == "时间到了"

    due, content = parse_reminder_time("明天９：００", NOW)
    assert due == datetime(2026, 11, 5, 9, 0, tzinfo=TZ)
    assert content == "时间到了"


def test_reminder_time_parser_accepts_half_hour_and_whole_hour_suffix():
    due, content = parse_reminder_time("9点半 收鱼", NOW)
    assert due == datetime(2026, 11, 5, 9, 30, tzinfo=TZ)
    assert content == "收鱼"

    due, content = parse_reminder_time("9点整", NOW)
    assert due == datetime(2026, 11, 5, 9, 0, tzinfo=TZ)
    assert content == "时间到了"


def test_reminder_time_parser_rolls_past_month_day_to_next_year():
    due, _content = parse_reminder_time("01-01 08:00 新年", NOW)
    assert due == datetime(2027, 1, 1, 8, 0, tzinfo=TZ)


def test_reminder_time_parser_rejects_invalid_or_past_times():
    with pytest.raises(ValueError):
        parse_reminder_time("25:00 收鱼", NOW)
    with pytest.raises(ValueError):
        parse_reminder_time("2026-11-03 08:00 收鱼", NOW)
    with pytest.raises(ValueError):
        parse_reminder_time("今天 19:00 收鱼", NOW)


def test_format_remaining_is_human_readable():
    assert format_remaining(NOW + timedelta(minutes=15), NOW) == "15分钟"
    assert format_remaining(NOW + timedelta(hours=2, minutes=5), NOW) == "2小时5分钟"
    assert format_remaining(NOW + timedelta(days=1, hours=3), NOW) == "1天3小时"


def test_create_reminder_persists_and_confirms():
    plugin = _plugin()
    event = _event()

    asyncio.run(plugin._create_reminder(event, "30分钟 喝水"))

    assert len(plugin.data["reminders"]) == 1
    item = plugin.data["reminders"][0]
    assert item["id"] == 1
    assert item["user_id"] == "100"
    assert item["content"] == "喝水"
    assert item["due_at"] == (NOW + timedelta(minutes=30)).isoformat()
    assert "已预约 #1" in plugin._reply.await_args.args[1]


def test_handle_message_routes_create_and_help():
    plugin = _plugin()
    event = _event()
    event.raw_message = "预约 30分钟 喝水"
    event.message = []

    asyncio.run(plugin.handle_message(event))

    assert len(plugin.data["reminders"]) == 1

    help_event = _event()
    help_event.raw_message = "预约"
    help_event.message = []
    asyncio.run(plugin.handle_message(help_event))
    assert "image" in help_event.reply.await_args.kwargs


def test_create_reminder_in_group_sends_private_confirmation():
    plugin = _plugin()
    plugin.api = SimpleNamespace(
        qq=SimpleNamespace(post_private_msg=AsyncMock())
    )
    event = _event(group_id="123")

    asyncio.run(plugin._create_reminder(event, "30分钟 喝水"))

    plugin.api.qq.post_private_msg.assert_awaited_once()
    assert "已私聊发送确认" in plugin._reply.await_args.args[1]


def test_list_and_cancel_reminders():
    plugin = _plugin()
    event = _event()
    asyncio.run(plugin._create_reminder(event, "30分钟 喝水"))
    asyncio.run(plugin._create_reminder(event, "2小时 休息"))

    entries = plugin._list_entries("100", NOW)
    assert [entry["content"] for entry in entries] == ["喝水", "休息"]
    event.reply.reset_mock()
    asyncio.run(plugin._list_reminders(event))
    assert "image" in event.reply.await_args.kwargs

    asyncio.run(plugin._cancel_reminder(event, "1"))
    assert [item["id"] for item in plugin.data["reminders"]] == [2]
    assert "已取消 #1" in plugin._reply.await_args.args[1]

    asyncio.run(plugin._cancel_reminder(event, "全部"))
    assert plugin.data["reminders"] == []


def test_due_reminder_is_sent_privately_and_removed():
    plugin = _plugin()
    plugin.api = SimpleNamespace(
        qq=SimpleNamespace(post_private_msg=AsyncMock())
    )
    plugin.data["reminders"] = [{
        "id": 7,
        "user_id": "100",
        "content": "收鱼",
        "due_at": (NOW - timedelta(minutes=1)).isoformat(),
        "attempts": 0,
    }]

    asyncio.run(plugin._check_reminders())

    plugin.api.qq.post_private_msg.assert_awaited_once()
    kwargs = plugin.api.qq.post_private_msg.await_args.kwargs
    assert kwargs["user_id"] == "100"
    assert "image" in kwargs
    assert plugin.data["reminders"] == []


def test_failed_private_delivery_is_kept_for_retry():
    plugin = _plugin()
    plugin.api = SimpleNamespace(
        qq=SimpleNamespace(post_private_msg=AsyncMock(side_effect=RuntimeError("offline")))
    )
    plugin.data["reminders"] = [{
        "id": 8,
        "user_id": "100",
        "content": "收鱼",
        "due_at": (NOW - timedelta(minutes=1)).isoformat(),
        "attempts": 0,
    }]

    asyncio.run(plugin._check_reminders())

    item = plugin.data["reminders"][0]
    assert item["attempts"] == 1
    assert item.get("next_attempt")
    assert not item.get("failed_at")


def test_reminder_limit_is_enforced():
    plugin = _plugin()
    plugin.data["reminders"] = [
        {
            "id": index,
            "user_id": "100",
            "content": "占位",
            "due_at": (NOW + timedelta(minutes=30)).isoformat(),
            "attempts": 0,
        }
        for index in range(1, 21)
    ]
    event = _event()

    asyncio.run(plugin._create_reminder(event, "30分钟 喝水"))

    assert len(plugin.data["reminders"]) == 20
    assert "最多同时保留 20 条预约" in plugin._reply.await_args.args[1]


def test_rendered_panels_are_png_images(tmp_path: Path):
    from PIL import Image as PILImage

    paths = [
        tmp_path / "notice.png",
        tmp_path / "help.png",
        tmp_path / "list.png",
        tmp_path / "reminder.png",
    ]
    render_notice(paths[0], "预约失败", ["时间格式无法识别，例如：预约 明天9点"])
    render_help(paths[1])
    render_list(
        paths[2],
        [{
            "id": 1,
            "due_text": "2026-11-05 21:30",
            "status_text": "待发送",
            "status_detail": "还有 1 时 30 分",
            "status_kind": "pending",
            "content": "收鱼",
        }],
        20,
    )
    render_reminder(paths[3], "收鱼", "2026-11-05 21:30")

    for path in paths:
        assert path.exists()
        with PILImage.open(path) as image:
            assert image.size[0] == 1240
            assert image.size[1] > 300
