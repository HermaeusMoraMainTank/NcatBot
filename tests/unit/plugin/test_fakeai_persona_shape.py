"""人设减重后的配套：语感样本、真实记忆锚点、回复形态与随机退避。"""

from __future__ import annotations

import importlib
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "plugins"))

fakeai = importlib.import_module("FakeAi.FakeAi")
style_mod = importlib.import_module("FakeAi.style_corpus")


def _corpus(tmp_path: Path) -> Path:
    path = tmp_path / "corpus.json"
    path.write_text(
        json.dumps(
            {
                "items": [
                    {"text": "今晚打零式吗 差个奶", "tags": ["game"]},
                    {"text": "楼下肠粉店又没开", "tags": ["food"]},
                    {"text": "我毕设还没动", "tags": ["work"]},
                    {"text": "草 这也太离谱了", "tags": ["mood"]},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def test_style_corpus_pick_is_bounded_and_deduped(tmp_path):
    corpus = style_mod.StyleCorpus(_corpus(tmp_path))
    assert corpus.reload(force=True) == 4
    picked = corpus.pick("今晚打零式", limit=2)
    assert len(picked) == 2
    assert len(set(picked)) == 2


def test_style_corpus_prompt_block_mentions_no_copying(tmp_path):
    corpus = style_mod.StyleCorpus(_corpus(tmp_path))
    corpus.reload(force=True)
    block = corpus.prompt_block("晚上吃啥", limit=3)
    assert "别原样复述" in block
    assert block.count("\n- ") == 3


def test_style_corpus_missing_file_is_silent(tmp_path):
    corpus = style_mod.StyleCorpus(tmp_path / "nope.json")
    assert corpus.reload(force=True) == 0
    assert corpus.prompt_block("随便") == ""


def test_shape_reply_varies_repeated_question(monkeypatch):
    gid = 424242
    fakeai._recent_bot_texts[gid] = ["？"]
    result = fakeai.AgentResult(
        actions=[fakeai.AgentAction(kind="text", messages=["？"])]
    )
    monkeypatch.setattr(fakeai.random, "random", lambda: 1.0)
    try:
        fakeai._shape_reply(gid, result)
        assert result.actions[0].messages[0] != "？"
    finally:
        fakeai._recent_bot_texts.pop(gid, None)


def test_shape_reply_splits_long_sentence(monkeypatch):
    gid = 434343
    result = fakeai.AgentResult(
        actions=[fakeai.AgentAction(kind="text", messages=["今天外卖还没到 饿死了"])]
    )
    # 第一次 random 决定分条，第二次决定要不要重复字
    values = iter([0.1, 0.9])
    monkeypatch.setattr(fakeai.random, "random", lambda: next(values, 0.9))
    try:
        fakeai._shape_reply(gid, result)
        messages = result.actions[0].messages
        assert len(messages) == 2
        assert messages[0] == "今天外卖还没到"
        assert messages[1] == "饿死了"
    finally:
        fakeai._recent_bot_texts.pop(gid, None)


def test_random_silence_backoff_and_reset():
    gid = 464646
    fakeai._random_skip.pop(gid, None)
    fakeai._random_skip_at.pop(gid, None)
    fakeai._random_backoff_until.pop(gid, None)
    fakeai._note_random_silence(gid)
    assert gid not in fakeai._random_backoff_until
    fakeai._note_random_silence(gid)
    assert fakeai._random_backoff_until[gid] > 0
    fakeai._note_reply(gid)
    assert gid not in fakeai._random_backoff_until
    fakeai._reply_stamps.pop(gid, None)


def test_persona_has_dynamic_slots():
    text = Path("plugins/FakeAi/prompts/lanqing_agent.yml").read_text(encoding="utf-8")
    assert "{style_corpus}" in text
    assert "{user_anchor}" in text
    assert len(text) < 4000


@pytest.mark.asyncio
async def test_recent_anchor_skips_current_conversation(monkeypatch):
    import time as _time

    now = _time.time()

    async def fake_search(_group_id, limit=40):
        return [
            {"user_id": "10001", "content": "刚发的", "ts": now - 60},
            {"user_id": "10001", "content": "上次说的事", "ts": now - 3 * 86400},
        ]

    monkeypatch.setattr(fakeai.memory_manager, "search_group_messages", fake_search)
    line = await fakeai._recent_anchor(1, "10001")
    assert "上次说的事" in line
    assert "3 天前" in line
    assert "刚发的" not in line


@pytest.mark.asyncio
async def test_recent_anchor_without_history_is_empty(monkeypatch):
    async def fake_search(*_args, **_kwargs):
        return []

    monkeypatch.setattr(fakeai.memory_manager, "search_group_messages", fake_search)
    assert await fakeai._recent_anchor(1, "10001") == ""


@pytest.mark.asyncio
async def test_answer_ai_injects_corpus_and_anchor(monkeypatch, tmp_path):
    import time as _time

    captured = {}

    async def fake_run_agent(prompt, keyword, **kwargs):
        captured["prompt"] = prompt
        captured["keyword"] = keyword
        return fakeai.AgentResult(actions=[])

    async def noop(*_args, **_kwargs):
        return None

    async def empty(*_args, **_kwargs):
        return ""

    async def fake_search(*_args, **_kwargs):
        return [
            {
                "user_id": "123",
                "name": "阿伟",
                "content": "上次说的零式攻略",
                "ts": _time.time() - 3 * 86400,
            }
        ]

    monkeypatch.setattr(fakeai, "run_agent", fake_run_agent)
    monkeypatch.setattr(fakeai.memory_manager, "init_db", noop)
    monkeypatch.setattr(fakeai.memory_manager, "get_memory_capsule", empty)
    monkeypatch.setattr(fakeai.memory_manager, "get_user_impressions_text", empty)
    monkeypatch.setattr(fakeai.memory_manager, "search_group_messages", fake_search)
    monkeypatch.setattr(fakeai, "DIRECT_CHAT_ENABLED", False)
    monkeypatch.setattr(fakeai, "STYLE_CORPUS_ENABLED", True)
    monkeypatch.setattr(fakeai, "STYLE_CORPUS_COUNT", 2)
    monkeypatch.setattr(fakeai.style_corpus, "path", _corpus(tmp_path))
    fakeai.style_corpus.reload(force=True)

    cache = fakeai.ReplyCache()
    cache.add_reply(
        json.dumps(
            {
                "name": "阿伟",
                "id": "123",
                "content": "今晚打零式吗 差个奶",
                "ts": _time.time(),
            },
            ensure_ascii=False,
        )
    )
    await fakeai.answer_ai(
        853963912,
        {853963912: cache},
        "123",
        "active",
        sender_name="阿伟",
        wake_label="有人叫了你的名字",
    )

    keyword = captured["keyword"]
    assert "【群里平时怎么说话" in keyword
    assert "【和这个人的上次】" in keyword
    for placeholder in (
        "{style_corpus}",
        "{user_anchor}",
        "{history_new}",
        "{user_impressions}",
    ):
        assert placeholder not in keyword
        assert placeholder not in captured["prompt"]
    assert len(captured["prompt"]) < 3500


def test_user_rate_limit_hourly(monkeypatch):
    gid, uid = 777001, "10001"
    fakeai._user_reply_stamps.pop((gid, uid), None)
    monkeypatch.setattr(fakeai, "USER_REPLY_PER_HOUR", 2)
    monkeypatch.setattr(fakeai, "USER_REPLY_PER_DAY", 0)
    assert fakeai._user_budget_ok(gid, uid) is True
    fakeai._note_user_reply(gid, uid)
    fakeai._note_user_reply(gid, uid)
    assert fakeai._user_budget_ok(gid, uid) is False


def test_user_rate_limit_daily(monkeypatch):
    gid, uid = 777002, "10002"
    fakeai._user_reply_stamps.pop((gid, uid), None)
    monkeypatch.setattr(fakeai, "USER_REPLY_PER_HOUR", 0)
    monkeypatch.setattr(fakeai, "USER_REPLY_PER_DAY", 2)
    fakeai._note_user_reply(gid, uid)
    fakeai._note_user_reply(gid, uid)
    assert fakeai._user_budget_ok(gid, uid) is False


def test_user_rate_limit_zero_means_unlimited(monkeypatch):
    gid, uid = 777003, "10003"
    fakeai._user_reply_stamps.pop((gid, uid), None)
    monkeypatch.setattr(fakeai, "USER_REPLY_PER_HOUR", 0)
    monkeypatch.setattr(fakeai, "USER_REPLY_PER_DAY", 0)
    for _ in range(50):
        fakeai._note_user_reply(gid, uid)
    assert fakeai._user_budget_ok(gid, uid) is True


def test_parse_direct_reply_strips_prefixes_and_limits():
    content = "蓝晴：行吧\n- 我继续改代码\n1. 晚安\n（这是心理活动）"
    assert fakeai._parse_direct_reply(content) == ["行吧", "我继续改代码", "晚安"]


def test_direct_chat_ok_only_for_casual_text():
    assert fakeai._direct_chat_ok("passive", "今晚打零式吗", [], "") is True
    assert fakeai._direct_chat_ok("passive", "帮我占卜一下", [], "") is False
    assert fakeai._direct_chat_ok("passive", "今晚打零式吗", ["x"], "") is False


@pytest.mark.asyncio
async def test_direct_chat_returns_agent_result(monkeypatch):
    async def fake_completion(*_args, **_kwargs):
        return {
            "content": "行吧\n我继续改代码",
            "usage": {"total_tokens": 12},
            "model": "deepseek-flash",
        }

    monkeypatch.setattr(fakeai.AiUtil, "chat_completion", fake_completion)
    result = await fakeai._direct_chat(
        {"system": "你是谁\n【工具】\n要调用 send_message"}, "历史消息"
    )
    assert result is not None
    assert [a.messages for a in result.actions] == [["行吧", "我继续改代码"]]
    assert result.model == "deepseek-flash"


@pytest.mark.asyncio
async def test_answer_ai_uses_direct_chat_for_casual(monkeypatch):
    import time as _time

    called = {"direct": 0, "agent": 0}

    async def fake_direct(_yaml, _keyword, **_kwargs):
        called["direct"] += 1
        return fakeai.AgentResult(
            actions=[fakeai.AgentAction(kind="text", messages=["行吧"])]
        )

    async def fake_agent(*_args, **_kwargs):
        called["agent"] += 1
        return fakeai.AgentResult(
            actions=[fakeai.AgentAction(kind="text", messages=["agent"])]
        )

    async def noop(*_args, **_kwargs):
        return None

    async def empty(*_args, **_kwargs):
        return ""

    monkeypatch.setattr(fakeai, "_direct_chat", fake_direct)
    monkeypatch.setattr(fakeai, "run_agent", fake_agent)
    monkeypatch.setattr(fakeai, "_build_skill_bridge", lambda *a, **k: (None, ""))
    monkeypatch.setattr(fakeai, "DIRECT_CHAT_ENABLED", True)
    monkeypatch.setattr(fakeai.memory_manager, "init_db", noop)
    monkeypatch.setattr(fakeai.memory_manager, "get_memory_capsule", empty)
    monkeypatch.setattr(fakeai.memory_manager, "get_user_impressions_text", empty)
    monkeypatch.setattr(fakeai.memory_manager, "search_group_messages", empty)

    cache = fakeai.ReplyCache()
    cache.add_reply(
        json.dumps(
            {
                "name": "阿伟",
                "id": "123",
                "content": "今晚打零式吗 差个奶",
                "ts": _time.time(),
            },
            ensure_ascii=False,
        )
    )
    await fakeai.answer_ai(
        853963912,
        {853963912: cache},
        "123",
        "active",
        sender_name="阿伟",
        wake_label="有人叫了你的名字",
    )
    assert called["direct"] == 1
    assert called["agent"] == 0


@pytest.mark.asyncio
async def test_direct_chat_uses_planner(monkeypatch):
    calls = []

    async def fake_completion(messages, **_kwargs):
        calls.append(messages)
        if len(calls) == 1:
            return {
                "content": "接住他加班的累，先附和，别讲道理",
                "usage": {"total_tokens": 8},
                "model": "deepseek-flash",
            }
        return {
            "content": "又加班啊\n躺会儿吧",
            "usage": {"total_tokens": 12},
            "model": "deepseek-flash",
        }

    monkeypatch.setattr(fakeai.AiUtil, "chat_completion", fake_completion)
    result = await fakeai._direct_chat(
        {"system": "你是谁\n【工具】\n要调用 send_message"},
        "历史消息",
        planner=True,
    )
    assert result is not None
    assert [a.messages for a in result.actions] == [["又加班啊", "躺会儿吧"]]
    assert "【回复方向】接住他加班的累" in calls[1][-1]["content"]
    assert result.mood_hint == "sad"


def test_parse_plan_splits_direction_and_mood():
    direction, mood = fakeai._parse_plan("方向：先接住疲惫，认真安慰\n表情：sad")
    assert direction == "先接住疲惫，认真安慰"
    assert mood == "sad"


def test_parse_plan_strips_inline_mood():
    direction, mood = fakeai._parse_plan("先接住疲惫，配 happy 表情")
    assert "表情" not in direction
    assert mood == "happy"
