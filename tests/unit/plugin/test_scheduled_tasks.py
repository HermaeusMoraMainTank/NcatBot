from __future__ import annotations

import asyncio
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "plugins"))

from ScheduledTasks.ScheduledTasks import (
    EVENING_IMAGE_PATH,
    IMAGE_PATH,
    JIABAOYU_MUSIC_URL,
    MUSIC_URL,
    ScheduledTasks,
)


def test_scheduled_tasks_have_both_image_resources():
    assert IMAGE_PATH.is_file()
    assert EVENING_IMAGE_PATH.is_file()


def test_on_load_registers_noon_and_evening_image_tasks():
    plugin = ScheduledTasks.__new__(ScheduledTasks)
    plugin.init_defaults = lambda _defaults: None
    plugin.config = {"group_list": []}
    plugin._main_loop = None
    plugin._scheduled_futures = set()
    plugin._music_last_sent = {}
    plugin.add_scheduled_task = lambda name, time_text, callback: registrations.append(
        (name, time_text, callback)
    )
    registrations = []

    asyncio.run(plugin.on_load())

    assert [(name, time_text) for name, time_text, _callback in registrations] == [
        ("send_noon_image", "11:00"),
        ("send_evening_image", "18:00"),
    ]


def test_evening_image_is_sent_to_configured_groups(monkeypatch):
    plugin = _plugin()
    sent = []

    async def send_group_image(group_id, image_path):
        sent.append((group_id, image_path))

    plugin.api.qq.send_group_image = send_group_image
    delays = []
    monkeypatch.setattr(
        "ScheduledTasks.ScheduledTasks.random.uniform",
        lambda *args: delays.append(args) or 0,
    )
    monkeypatch.setattr("ScheduledTasks.ScheduledTasks.asyncio.sleep", AsyncMock())

    asyncio.run(plugin._send_evening_image())

    assert sent == [
        ("853963912", str(EVENING_IMAGE_PATH)),
        ("719518427", str(EVENING_IMAGE_PATH)),
    ]
    assert delays == [(0, 60 * 60)]


def _event(text: str = "", *, group_id: str = "853963912", segments=None):
    return SimpleNamespace(
        group_id=group_id,
        raw_message=text,
        message=segments if segments is not None else [SimpleNamespace(text=text)],
    )


def _plugin():
    plugin = ScheduledTasks.__new__(ScheduledTasks)
    plugin.get_config = lambda key, default=None: default
    plugin._group_list = lambda: ["853963912", "719518427"]
    plugin.api = SimpleNamespace(qq=SimpleNamespace(post_group_msg=AsyncMock()))
    plugin._music_last_sent = {}
    return plugin


def test_music_trigger_uses_similarity_and_sends_record():
    plugin = _plugin()
    event = _event("哒哒哒哒哒啦哒😋，所以说是恋恋胜利啦😝")

    asyncio.run(plugin.handle_music_trigger(event))

    plugin.api.qq.post_group_msg.assert_awaited_once()
    call = plugin.api.qq.post_group_msg.await_args.kwargs
    assert call["group_id"] == "853963912"
    assert list(call["rtf"])[0].file == MUSIC_URL


def test_music_trigger_matches_repeated_full_lyric_in_any_group():
    plugin = _plugin()
    event = _event(
        "哒哒哒哒哒啦哒😋，所以说\n是恋恋胜利啦😝\n"
        "哒哒哒哒哒啦哒😆，就好像是陷入恋爱啦😚\n" * 2
        + "爱😍love😋heart❤恋🥰魔法💥wink😉chuuuuuu😘"
        "love😋爱😍heart❤恋🥰魔法winkchuuuuuu",
        group_id="1064163905",
    )

    asyncio.run(plugin.handle_music_trigger(event))

    plugin.api.qq.post_group_msg.assert_awaited_once()


def test_music_trigger_has_no_group_restriction():
    plugin = _plugin()
    event = _event("哒哒哒哒哒啦哒，所以说是恋恋胜利啦", group_id="9999999999")

    asyncio.run(plugin.handle_music_trigger(event))

    plugin.api.qq.post_group_msg.assert_awaited_once()


def test_jiabaoyu_keywords_send_record_in_any_group():
    plugin = _plugin()
    event = _event("贾宝玉，请回答我。", group_id="123456789")

    asyncio.run(plugin.handle_music_trigger(event))

    plugin.api.qq.post_group_msg.assert_awaited_once()
    call = plugin.api.qq.post_group_msg.await_args.kwargs
    assert call["group_id"] == "123456789"
    assert list(call["rtf"])[0].file == JIABAOYU_MUSIC_URL


def test_jiabaoyu_keywords_require_both_words():
    plugin = _plugin()

    asyncio.run(plugin.handle_music_trigger(_event("贾宝玉")))
    asyncio.run(plugin.handle_music_trigger(_event("回答我")))

    plugin.api.qq.post_group_msg.assert_not_awaited()


def test_music_cooldown_is_independent_per_song():
    plugin = _plugin()

    asyncio.run(plugin.handle_music_trigger(_event("小石DISCO歌词哒哒哒哒哒啦哒所以说是恋恋胜利啦")))
    asyncio.run(plugin.handle_music_trigger(_event("贾宝玉回答我")))

    assert plugin.api.qq.post_group_msg.await_count == 2


def test_music_trigger_accepts_qq_music_json_share():
    plugin = _plugin()
    payload = {"meta": {"music": {"title": "小石DISCO(feat). 本墨置子)"}}}
    event = _event(
        segments=[SimpleNamespace(text=None, data=json.dumps(payload, ensure_ascii=False))]
    )

    asyncio.run(plugin.handle_music_trigger(event))

    plugin.api.qq.post_group_msg.assert_awaited_once()


def test_music_trigger_ignores_unrelated_text_and_cools_down():
    plugin = _plugin()
    asyncio.run(plugin.handle_music_trigger(_event("今天吃什么")))
    assert not plugin.api.qq.post_group_msg.await_args_list

    event = _event("爱love😋loveheart恋魔法winkchuuuuuu😘")
    asyncio.run(plugin.handle_music_trigger(event))
    asyncio.run(plugin.handle_music_trigger(event))
    assert plugin.api.qq.post_group_msg.await_count == 1
