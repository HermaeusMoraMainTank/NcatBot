from datetime import datetime, timedelta
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "plugins"))

from plugins.McFish.mcfish_rules import (
    ACCESSORY_SLOT_UNLOCKS,
    ACCESSORY_UPGRADE_COSTS,
    CHAIN_RULES,
    FISH_KINGS,
    FISH_KING_PROMPTS,
    EQUIPMENT,
    active_window,
    daily_attempt_limit,
    daily_fishing_zone,
    fish_king_probabilities,
    fish_sale_price,
    fishing_xp,
    fishing_wait_for_result,
    fishing_wait_seconds,
    format_duration,
    bite_skill_from_reply,
    hook_skill_from_reply,
    hook_skill_rate_multiplier,
    king_length_for_hook,
    fish_zone_weights,
    FISH_KING_PROBABILITIES,
    FISH_ZONE_WEIGHTS,
    HIDDEN_FISH,
    FISH_KINGS,
    FISH_KING_UNLOCKED_PROBABILITIES,
    POLE_FRAGMENT_COSTS,
    fragment_roll,
    fragment_jackpot,
    generate_week_windows,
    king_success_rate,
    king_sale_price,
    PERMANENT_ACCESSORY_EFFECTS,
    level_for_xp,
    parse_count_token,
    xp_required_for_level,
    king_bite_rate,
    hidden_fish_trigger,
    advance_path,
    consume_path,
    path_is_satisfied,
    scheduled_attempts,
    strategy_from_reply,
    treasure_sale_price,
    rod_unlock_level,
    rod_drop_allowed,
)


def test_fishing_levels_control_daily_attempts_and_rod_unlocks():
    assert level_for_xp(0) == 1
    assert level_for_xp(99) == 1
    assert level_for_xp(100) == 2
    assert xp_required_for_level(3) == 250
    assert xp_required_for_level(5) == 700
    assert level_for_xp(699) == 4
    assert level_for_xp(700) == 5
    assert level_for_xp(3249) == 10
    assert level_for_xp(3250) == 11
    assert [daily_attempt_limit(level) for level in (0, 5, 10, 20, 30)] == [50, 60, 70, 80, 100]
    assert [rod_unlock_level(name) for name in ("木竿", "铜竿", "锡竿", "铁竿", "猩红竿", "幻光竿", "星河竿", "灿阳竿", "三叉戟")] == [0, 3, 8, 15, 25, 35, 48, 55, 35]


def test_level_curve_b_extends_to_level_100_without_changing_early_levels():
    from McFish.mcfish_rules import MAX_FISHING_LEVEL

    assert MAX_FISHING_LEVEL == 100
    # 1–35 级完全沿用旧曲线
    for level in (1, 2, 10, 20, 35):
        steps = level - 1
        assert xp_required_for_level(level) == 100 * steps + 25 * steps * (steps - 1)
    assert xp_required_for_level(36) == xp_required_for_level(35) + 1200
    assert xp_required_for_level(37) == xp_required_for_level(36) + 1240
    assert xp_required_for_level(60) == 73450
    assert xp_required_for_level(61) == 73450 + 2600
    assert xp_required_for_level(62) == xp_required_for_level(61) + 2660
    assert xp_required_for_level(100) == 224250
    # 曲线单调，且 100 级后不再继续升级
    assert all(
        xp_required_for_level(level) < xp_required_for_level(level + 1)
        for level in range(1, 100)
    )
    assert level_for_xp(224249) == 99
    assert level_for_xp(224250) == 100
    assert level_for_xp(10**9) == 100


def test_sell_tax_rises_with_zone_and_falls_back_to_twenty_percent():
    from McFish.McFish import McFish

    plugin = McFish.__new__(McFish)
    plugin.get_config = lambda key, default=None: 0.8 if key == "sell_tax" else default

    assert abs(plugin._sale_tax_factor("岩石凶鲨") - 0.80) < 1e-9
    assert abs(plugin._sale_tax_factor("粉角皮科鱼") - 0.55) < 1e-9
    assert abs(plugin._sale_tax_factor("诱钓") - 0.80) < 1e-9


def test_recorded_catch_zone_wins_over_species_lowest_tax():
    from McFish.McFish import McFish

    plugin = McFish.__new__(McFish)
    plugin.get_config = lambda key, default=None: 0.8 if key == "sell_tax" else default
    state = {"item_zones": {"灰色沙丘曳尾鱼": "deep"}}

    assert abs(plugin._sale_tax_factor("灰色沙丘曳尾鱼", state) - 0.78) < 1e-9
    assert abs(plugin._sale_tax_factor("灰色沙丘曳尾鱼") - 0.60) < 1e-9


def test_resident_and_prerequisite_king_magnet_multiplier_is_capped():
    from McFish.mcfish_rules import capped_king_sale_price, king_sale_price

    assert capped_king_sale_price(8000) == 8000
    assert capped_king_sale_price(8000, gold_magnet=True) == 24000
    assert capped_king_sale_price(8000, gold_magnet=10.0) == 24000
    assert capped_king_sale_price(8000, gold_magnet=30.0) == 40000
    assert capped_king_sale_price(8000, gold_magnet=100.0) == 64000
    assert king_sale_price(8000, gold_magnet=10.0) == 80000
    assert king_sale_price(8000, gold_magnet=30.0) == 240000
    assert king_sale_price(8000, gold_magnet=100.0) == 800000


def test_every_fish_and_king_has_a_local_wiki_icon():
    from pathlib import Path

    from McFish.mcfish_resident_kings import RESIDENT_KINGS

    root = Path(__file__).resolve().parents[3] / "plugins" / "McFish" / "resources"
    # 限时鱼王 8 条是本项目自设的链式 Boss，wiki 没有对应鱼种；
    # 其余普通鱼、隐藏鱼与常驻鱼王都必须使用 wiki 同名图标。
    names = (
        set(name for zone in FISH_ZONE_WEIGHTS.values() for name in zone)
        | set(HIDDEN_FISH)
        | set(RESIDENT_KINGS)
    )
    missing = sorted(name for name in names if not (root / f"{name}.png").is_file())
    assert missing == []


def test_rod_drop_limit_allows_only_the_next_tier():
    """MCF-01: 鱼竿只能钓到自身、低级或紧邻的下一级鱼竿。"""
    assert rod_drop_allowed("木竿", "木竿")
    assert rod_drop_allowed("木竿", "铜竿")
    assert not rod_drop_allowed("木竿", "锡竿")
    assert not rod_drop_allowed("铜竿", "铁竿")
    assert rod_drop_allowed("锡竿", "木竿")
    assert POLE_FRAGMENT_COSTS == {
        "木竿": 12,
        "铜竿": 24,
        "锡竿": 56,
        "铁竿": 120,
        "猩红竿": 240,
        "幻光竿": 360,
        "星河竿": 500,
        "灿阳竿": 680,
    }


def test_fish_king_probability_table_and_zone_rotation_are_deterministic():
    assert fish_king_probabilities("月影巨鲤", "木竿") == (0.015, 0.20)
    assert fish_king_probabilities("月影巨鲤", "三叉戟") == (0.11, 0.66)
    assert fish_king_probabilities("河湾巨鲤", "锡竿", prerequisite_caught=False) == (0.0, 0.0)
    assert fish_king_probabilities("河湾巨鲤", "锡竿", prerequisite_caught=True) == (0.055, 0.50)
    assert daily_fishing_zone(datetime(2026, 9, 7)) != daily_fishing_zone(datetime(2026, 9, 8))
    assert set(fish_zone_weights("deep", "木竿")) >= {"追踪鱼", "粉色珊瑚蝾螈"}
    assert all(len(table) == 9 for table in FISH_KING_PROBABILITIES.values())
    assert set(FISH_KING_UNLOCKED_PROBABILITIES) == {"河湾巨鲤", "深海灯笼鱼", "遗迹金鳞鱼", "风暴海龙"}


def test_fish_king_prompts_hide_identity_until_capture():
    assert set(FISH_KING_PROMPTS) == set(FISH_KINGS)
    for king_name, prompts in FISH_KING_PROMPTS.items():
        assert set(prompts) == {"trigger", "disappear", "success"}
        assert king_name not in prompts["trigger"]
        assert king_name not in prompts["disappear"]
        assert "{length}" in prompts["success"]
        assert "{price}" in prompts["success"]


def test_fishing_xp_differs_by_fish_and_king_is_much_higher():
    assert fishing_xp("岩石凶鲨") < fishing_xp("刃鳍鱼") < fishing_xp("粉角皮科鱼")
    assert fishing_xp("灰色沙丘曳尾鱼") > fishing_xp("银色飞镖")
    assert fishing_xp("月影巨鲤") >= fishing_xp("银色飞镖") * 4
    assert fishing_xp("风暴海龙") > fishing_xp("月影巨鲤")
    assert fishing_xp("未知物品") == 0


def test_bite_and_hook_skills_are_separate():
    assert bite_skill_from_reply("2") == "mooch_post"
    assert bite_skill_from_reply("3") == "give_up"
    assert hook_skill_from_reply("1") == "precision_hookset"
    assert hook_skill_from_reply("4") == "burst_reel"
    assert hook_skill_rate_multiplier("burst_reel") > hook_skill_rate_multiplier("precision_hookset")


def test_hook_skills_balance_success_rate_against_king_size():
    # 成功率倍率与平均体型系数相乘后，四个技能的期望收益差距应控制在 10% 以内；
    # 1/2 期望略高，3/4 略低但会推进鱼王路径，形成"收益 vs 保证上钩"的取舍。
    expectations = {
        "precision_hookset": (1.00, 0.775),
        "powerful_hookset": (1.10, 0.70),
        "patient_control": (1.24, 0.60),
        "burst_reel": (1.45, 0.50),
    }
    for skill, (multiplier, size_factor) in expectations.items():
        assert hook_skill_rate_multiplier(skill) == multiplier
        assert king_success_rate(0.4, rate_multiplier=multiplier) == pytest.approx(0.4 * multiplier)
    values = [multiplier * size for multiplier, size in expectations.values()]
    assert max(values) / min(values) < 1.10
    assert values[0] == max(values)
    assert values[1] > values[2] > values[3]


def test_hook_skills_trade_success_rate_against_king_size():
    floors = {
        "precision_hookset": 80,
        "powerful_hookset": 71,
        "patient_control": 58,
        "burst_reel": 45,
    }
    for skill, minimum in floors.items():
        rolls = [king_length_for_hook("月影巨鲤", skill, rng=random.Random(seed)) for seed in range(40)]
        assert min(rolls) >= minimum
        assert max(rolls) <= 110


def test_clock_duration_format_is_readable_for_short_and_long_windows():
    assert format_duration(53) == "53秒"
    assert format_duration(63 * 60) == "1小时3分"
    assert format_duration(24 * 60 * 60) == "24小时"


def test_equipment_has_fixed_cost_and_no_direct_fishing_drop():
    assert {name for name in EQUIPMENT} == {
        "财富浮标", "坚韧钓组", "黄金磁石", "魔法鱼饵", "幸运海星", "隐秘诱饵", "万能鱼饵"
    }
    assert [EQUIPMENT[name]["fragments"] for name in EQUIPMENT] == [120, 160, 220, 24, 300, 42, 50]
    assert all("drop_rate" not in item for item in EQUIPMENT.values())


def test_accessory_slot_and_upgrade_costs_match_the_fragment_curve():
    assert ACCESSORY_SLOT_UNLOCKS == {
        3: {"level": 10, "fragments": 80},
        4: {"level": 20, "fragments": 180},
    }
    assert ACCESSORY_UPGRADE_COSTS == {
        "财富浮标": (60, 120),
        "坚韧钓组": (80, 160),
        "黄金磁石": (110, 220),
        "幸运海星": (150, 300),
    }
    assert PERMANENT_ACCESSORY_EFFECTS["财富浮标"][3] == {
        "treasure_rate": 1.65,
        "king_clue_rate": 1.36,
    }
    assert PERMANENT_ACCESSORY_EFFECTS["幸运海星"][3]["fragment_points"] == 16
    assert [PERMANENT_ACCESSORY_EFFECTS["黄金磁石"][level]["fish_price"] for level in (1, 2, 3)] == [1.08, 1.12, 1.16]
    assert [PERMANENT_ACCESSORY_EFFECTS["黄金磁石"][level]["king_price"] for level in (1, 2, 3)] == [10.0, 30.0, 100.0]


def test_numeric_strategy_mapping_and_path_guarantee():
    assert strategy_from_reply("4") == "ambitious_bait"
    assert strategy_from_reply("雄心之饵") is None
    state = {}
    assert path_is_satisfied(state, "河湾巨鲤") is False
    advance_path(state, "河湾巨鲤", "humble_bait")
    assert path_is_satisfied(state, "河湾巨鲤") is False
    advance_path(state, "河湾巨鲤", "humble_bait")
    assert path_is_satisfied(state, "河湾巨鲤") is True
    consume_path(state, "河湾巨鲤")
    assert path_is_satisfied(state, "河湾巨鲤") is False


def test_path_ignores_skills_outside_the_king_path():
    state = {}
    advance_path(state, "河湾巨鲤", "ambitious_bait")
    assert state["king_paths"]["河湾巨鲤"]["counts"] == {"4": 1}
    assert path_is_satisfied(state, "河湾巨鲤") is False


def test_king_windows_stay_on_their_chain_preferred_days():
    # 鱼点改为玩家自选后，窗口不再要求落在该鱼点的轮换日；
    # 但仍应落在各链配置的星期内，并保持前置/后置窗口重叠。
    for week in range(8):
        monday = datetime(2026, 9, 7, 0, 0) + timedelta(days=7 * week)
        windows = generate_week_windows(monday, seed=monday.toordinal())
        for first, final in zip(windows[::2], windows[1::2]):
            chain = FISH_KINGS[first.king_name]["chain"]
            assert first.starts_at.weekday() in CHAIN_RULES[chain]["days"]
            assert final.starts_at.weekday() in CHAIN_RULES[chain]["days"]
            assert max(first.starts_at, final.starts_at) < min(first.ends_at, final.ends_at)


def test_windows_are_irregular_in_daytime_and_overlapping():
    monday = datetime(2026, 9, 7, 0, 0)
    windows = generate_week_windows(monday, seed=11)
    assert len(windows) == 8
    for window in windows:
        assert 9 <= window.starts_at.hour <= 20
        assert window.ends_at.hour < 21 or (window.ends_at.hour == 21 and window.ends_at.minute == 0)
    for first, final in zip(windows[::2], windows[1::2]):
        assert max(first.starts_at, final.starts_at) < min(first.ends_at, final.ends_at)
    assert len({window.starts_at.minute for window in windows}) > 1
    assert active_window(windows, windows[0].starts_at) == windows[0]


def test_batch_attempts_use_virtual_timestamp_per_rod():
    start = datetime(2026, 9, 7, 10, 13)
    attempts = scheduled_attempts(start, [timedelta(minutes=8)] * 4)
    assert attempts == [start, start + timedelta(minutes=8), start + timedelta(minutes=16), start + timedelta(minutes=24)]


def test_each_fishing_attempt_has_at_least_thirty_seconds_and_ordered_hidden_timestamp():
    assert fishing_wait_seconds(0) == 180
    assert fishing_wait_seconds(2) == 60
    assert fishing_wait_seconds(0, 0.85) == 153
    start = datetime(2026, 9, 7, 10, 13)
    assert fishing_wait_seconds(10) == 30
    attempts = scheduled_attempts(
        start,
        [timedelta(seconds=fishing_wait_seconds(10))] * 5,
        include_start=False,
    )
    assert attempts == [start + timedelta(seconds=30 * index) for index in range(1, 6)]


def test_fishing_wait_varies_by_result_tier_and_chinese_counts_parse():
    fast = fishing_wait_for_result("吸血鳗", rng=random.Random(1))
    rare = fishing_wait_for_result("刃鳍鱼", rng=random.Random(1))
    king = fishing_wait_for_result("风暴海龙", rng=random.Random(1))
    assert 30 <= fast <= 50
    assert rare > fast
    assert king > rare
    assert fishing_wait_for_result("刃鳍鱼", induce=20, rng=random.Random(1)) >= 30
    assert [parse_count_token(value) for value in ("十", "十一", "二十", "两", "10")] == [10, 11, 20, 2, 10]
    assert parse_count_token("很多") is None


def test_fragment_pity_after_twelve_misses():
    rng = random.Random(1)
    quantity, misses = fragment_roll(12, rng=rng)
    assert quantity in (1, 2)
    assert misses == 0


def test_fragment_jackpot_is_independent_and_awards_fixed_event():
    assert fragment_jackpot(rng=random.Random(1), rate=1.0) is True
    assert fragment_jackpot(rng=random.Random(1), rate=0.0) is False


def test_king_guarantee_keeps_failure_floor_and_tough_tackle_caps_at_95():
    assert king_success_rate(0.2, guaranteed=True) == 0.95
    assert king_success_rate(0.9, tough_tackle=True) == 0.95


def test_accessory_modifiers_are_scoped():
    assert king_bite_rate(0.10, wealth_bobber=True) == 0.12
    assert king_bite_rate(0.10, wealth_bobber=1.36) == 0.136
    assert king_bite_rate(0.10, hidden_lure=True, hidden_target=False) == 0.10
    assert king_success_rate(0.20, tough_tackle=20) == 0.40
    assert treasure_sale_price(100, gold_magnet=True) == 125
    assert treasure_sale_price(100, gold_magnet=1.50) == 150
    assert fish_sale_price(100, gold_magnet=1.16) == 115
    assert king_sale_price(100, gold_magnet=100.0) == 10000


def test_hidden_lure_requires_matching_zone():
    assert hidden_fish_trigger(zone="deep", hidden_lure=True, rng=random.Random(1)) is None
    assert hidden_fish_trigger(zone="unknown", hidden_lure=True, rng=random.Random(0)) is None


def test_rod_weights_are_keyed_by_fish_name():
    from McFish.mcfish_rules import DEFAULT_FISH_WEIGHT, FISH_ROD_WEIGHTS

    assert set(FISH_ROD_WEIGHTS) == {
        "木竿", "铜竿", "锡竿", "铁竿", "猩红竿", "幻光竿", "星河竿", "灿阳竿", "三叉戟"
    }
    assert FISH_ROD_WEIGHTS["木竿"]["岩石凶鲨"] == 12
    assert FISH_ROD_WEIGHTS["木竿"]["古老龙鱼"] == 0
    assert FISH_ROD_WEIGHTS["三叉戟"]["粉角皮科鱼"] == 5
    assert all(len(row) == 35 for row in FISH_ROD_WEIGHTS.values())
    assert set(fish_zone_weights("bay", "木竿")) == {"岩石凶鲨", "吸血鳗", "恶魔蠕虫", "橙色洞穴孔雀鱼", "蓝色洞穴孔雀鱼", "绿色水泡头", "黄色水泡头"}
    assert fish_zone_weights("bay", "木竿")["岩石凶鲨"] == 12
    assert DEFAULT_FISH_WEIGHT >= 1


def test_new_fish_without_rod_entry_falls_back_to_default_weight(monkeypatch):
    import McFish.mcfish_rules as rules

    monkeypatch.setitem(rules.FISH_ZONE_WEIGHTS, "bay", ("刃鳍鱼", "新鱼"))
    weights = rules.fish_zone_weights("bay", "木竿")
    assert weights["新鱼"] == rules.DEFAULT_FISH_WEIGHT


def test_high_tier_zone_never_returns_an_empty_weight_pool():
    # 木竿在时之洄流的全部鱼种上都是 0 权重；必须补保底权重，否则抽取/面板会崩。
    weights = fish_zone_weights("chrono", "木竿")
    assert weights and any(value > 0 for value in weights.values())
    from McFish.mcfish_rules import FISH_ZONE_WEIGHTS

    assert set(weights) == set(FISH_ZONE_WEIGHTS["chrono"])
