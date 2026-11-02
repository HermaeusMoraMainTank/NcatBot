"""常驻鱼王（P7）的规则与钓鱼链路测试。"""

from __future__ import annotations

import asyncio
import copy
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "plugins"))

from McFish import mcfish_resident_kings as K
from McFish import mcfish_rules as R
from McFish import mcfish_workshop as W
from McFish.McFish import McFish

ZONES = ("bay", "deep", "ruin", "storm", "magma", "frost", "glow", "astral", "chrono")


def test_resident_kings_cover_all_nine_zones_with_rising_thresholds():
    assert len(K.RESIDENT_KINGS) == 9
    assert len(K.RESIDENT_KING_ZONES) == 9
    assert set(K.RESIDENT_KING_ZONES) == set(ZONES)
    thresholds = [K.RESIDENT_KINGS[K.RESIDENT_KING_ZONES[zone]]["fp"] for zone in ZONES]
    assert thresholds == sorted(thresholds)
    assert thresholds[0] > 0
    assert all(0 < K.RESIDENT_KINGS[name]["bite"] < 0.03 for name in K.RESIDENT_KINGS)
    assert all(0 < K.RESIDENT_KINGS[name]["hook"] < 0.95 for name in K.RESIDENT_KINGS)


def test_resident_kings_are_separate_from_timed_kings_and_workshop_materials():
    for name in K.RESIDENT_KING_NAMES:
        assert name not in R.FISH_KINGS
        assert R.fishing_xp(name) == K.RESIDENT_KING_XP[name]
        assert name not in W.TRIDENT["items"]
        assert name not in W.ORACLE["items"]


def test_resident_king_messages_have_length_and_price_slots():
    for name, entry in K.RESIDENT_KINGS.items():
        assert "{length}" in entry["success"] and "{price}" in entry["success"]
        assert name in entry["success"]
        assert entry["trigger"] and entry["disappear"]


def test_bite_chance_only_never_exceeds_certainty_and_scales_with_bonuses():
    base = K.bite_chance("宝石蟹")
    assert base == K.RESIDENT_KINGS["宝石蟹"]["bite"]
    assert K.bite_chance("宝石蟹", bait_bonus=2.0) == base * 2
    assert K.bite_chance("宝石蟹", gear_bonus=0.5) == base * 1.5
    assert K.bite_chance("宝石蟹", clue_multiplier=1.2) == base * 1.2
    assert K.bite_chance("宝石蟹", bait_bonus=1e6, gear_bonus=1e6, clue_multiplier=1e6) == 1.0
    assert K.bite_chance("不存在") == 0.0


def test_bait_multiplier_takes_the_strongest_active_bait():
    assert K.bait_multiplier(None) == 1.0
    assert K.bait_multiplier({"隐秘诱饵": 1}) == 2.0
    assert K.bait_multiplier({"万能鱼饵": 2}) == 1.5
    assert K.bait_multiplier({"隐秘诱饵": 1, "万能鱼饵": 1}) == 2.0
    assert K.bait_multiplier({"魔法鱼饵": 3}) == 1.0


def test_hook_chance_and_price_follow_tackle_and_length():
    entry = K.RESIDENT_KINGS["裂缝灯鱼"]
    assert K.hook_chance("裂缝灯鱼") == entry["hook"]
    assert K.hook_chance("裂缝灯鱼", tough_points=10) == min(0.95, entry["hook"] + 0.10)
    assert K.hook_chance("裂缝灯鱼", tough_points=100) == 0.95
    low, high = entry["length"]
    assert K.price_for("裂缝灯鱼", (low + high) // 2) == entry["base_price"]
    assert K.price_for("裂缝灯鱼", high) > entry["base_price"]
    assert K.price_for("裂缝灯鱼", low) < entry["base_price"]
    assert K.roll_length("裂缝灯鱼") in range(low, high + 1)


def test_resident_kings_are_sellable_and_grantable():
    import importlib

    module = importlib.import_module("McFish.McFish")
    plugin = McFish.__new__(McFish)
    plugin._load_original_data()
    for name, entry in K.RESIDENT_KINGS.items():
        assert module.KING_INFO[name] is entry
        assert plugin._prices[name] == int(entry["base_price"])
        assert name in plugin._grantable_item_names()


def _prepare(monkeypatch, *, level: int, zone: str = "bay", extra: dict | None = None):
    module = importlib.import_module("McFish.McFish")
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    # 常驻鱼王的成功/脱钩由 helper 决定，其余随机分支固定为 0.5，避免测试顺序影响。
    monkeypatch.setattr(module.random, "random", lambda: 0.5)
    plugin = McFish.__new__(McFish)
    plugin.get_config = lambda key, default=None: 50 if key == "fish_limit" else default
    plugin._item_image = lambda _name: None
    plugin._original_drop = lambda _state, _boundaries=None, **_kwargs: ("鲑鱼", 1)
    plugin._fish_windows = list
    state = {
        "fish_today": 0,
        "fish_xp": 0,
        "fish_level": level,
        "equip": {"name": "木竿", "durable": 5, "maintenance": 0, "induce": 0, "favor": 0},
        "pack": {},
        "fishing_zone": zone,
    }
    state.update(copy.deepcopy(extra or {}))
    stored = copy.deepcopy(state)

    plugin._state = lambda _uid: copy.deepcopy(stored)
    plugin._save = lambda _uid, value: (stored.clear(), stored.update(copy.deepcopy(value)))
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())
    return module, plugin, event, state, stored


def test_resident_king_catch_is_single_stage_and_respects_daily_cap(monkeypatch):
    module, plugin, event, state, stored = _prepare(monkeypatch, level=30)
    monkeypatch.setattr(module, "resident_king_bite_chance", lambda *a, **k: 1.0)
    monkeypatch.setattr(module, "resident_king_hook_chance", lambda *a, **k: 1.0)
    monkeypatch.setattr(module, "resident_king_length", lambda _name: 40)
    # 常驻鱼王不需要回复：整个链路不应该出现第二次 ask/choose。
    plugin._choose = AsyncMock(side_effect=AssertionError("常驻鱼王不应触发对话"))

    asyncio.run(plugin._fish(event, state, "进行1次钓鱼"))

    text = event.reply.await_args.kwargs["text"]
    assert "宝石蟹" in text
    assert stored["pack"]["宝石蟹"] == 1
    assert stored["resident_king_today"] == {"宝石蟹": 1}
    assert stored["king_stats"]["宝石蟹"]["caught"] == 1
    assert "鱼王鳞片" not in stored["pack"]

    # 同一天第二次咬钩被每日上限挡掉。
    monkeypatch.setattr(module, "resident_king_bite_chance", lambda *a, **k: (_ for _ in ()).throw(AssertionError("达到每日上限后不应再判定")))
    stored["fish_today"] = 0
    stored["equip"]["durable"] = 5
    asyncio.run(plugin._fish(event, stored, "进行1次钓鱼"))
    assert stored["pack"]["宝石蟹"] == 1


def test_resident_king_requires_fishing_power(monkeypatch):
    module, plugin, event, state, stored = _prepare(monkeypatch, level=1)
    calls = []
    monkeypatch.setattr(module, "resident_king_bite_chance", lambda *a, **k: calls.append(1) or 1.0)

    asyncio.run(plugin._fish(event, state, "进行1次钓鱼"))

    assert calls == []
    assert "宝石蟹" not in stored["pack"]


def test_resident_king_escape_is_recorded_without_prompt(monkeypatch):
    module, plugin, event, state, stored = _prepare(monkeypatch, level=30)
    monkeypatch.setattr(module, "resident_king_bite_chance", lambda *a, **k: 1.0)
    monkeypatch.setattr(module, "resident_king_hook_chance", lambda *a, **k: 0.0)
    plugin._choose = AsyncMock(side_effect=AssertionError("常驻鱼王不应触发对话"))

    asyncio.run(plugin._fish(event, state, "进行1次钓鱼"))

    text = event.reply.await_args.kwargs["text"]
    assert "脱钩" in text
    assert stored["king_stats"]["宝石蟹"]["bitten"] == 1
    assert stored["king_stats"]["宝石蟹"]["escaped"] == 1
    assert stored["resident_king_today"] == {}
    assert "宝石蟹" not in stored["pack"]
