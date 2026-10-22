from datetime import datetime, timedelta
import random

from plugins.McFish.mcfish_rules import (
    ACCESSORY_SLOT_UNLOCKS,
    ACCESSORY_UPGRADE_COSTS,
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
    hook_skill_bonus,
    hook_skill_from_reply,
    king_length_for_hook,
    fish_zone_weights,
    FISH_KING_PROBABILITIES,
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
    path_progress,
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
    assert [rod_unlock_level(name) for name in ("木竿", "铁竿", "金竿", "钻石竿", "下界合金竿", "三叉戟")] == [0, 3, 8, 15, 25, 35]


def test_rod_drop_limit_allows_only_the_next_tier():
    """MCF-01: 鱼竿只能钓到自身、低级或紧邻的下一级鱼竿。"""
    assert rod_drop_allowed("木竿", "木竿")
    assert rod_drop_allowed("木竿", "铁竿")
    assert not rod_drop_allowed("木竿", "金竿")
    assert not rod_drop_allowed("铁竿", "钻石竿")
    assert rod_drop_allowed("金竿", "木竿")
    assert POLE_FRAGMENT_COSTS == {
        "木竿": 12,
        "铁竿": 24,
        "金竿": 56,
        "钻石竿": 120,
        "下界合金竿": 240,
    }


def test_fish_king_probability_table_and_zone_rotation_are_deterministic():
    assert fish_king_probabilities("月影巨鲤", "木竿") == (0.03, 0.20)
    assert fish_king_probabilities("月影巨鲤", "三叉戟") == (0.20, 0.60)
    assert fish_king_probabilities("河湾巨鲤", "金竿", prerequisite_caught=False) == (0.0, 0.0)
    assert fish_king_probabilities("河湾巨鲤", "金竿", prerequisite_caught=True) == (0.18, 0.50)
    assert daily_fishing_zone(datetime(2026, 9, 7)) != daily_fishing_zone(datetime(2026, 9, 8))
    assert set(fish_zone_weights("deep", "木竿")) >= {"鳕鱼", "深海鱼"}
    assert all(len(table) == 6 for table in FISH_KING_PROBABILITIES.values())
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
    assert fishing_xp("鳕鱼") < fishing_xp("墨鱼") < fishing_xp("金鳞鱼")
    assert fishing_xp("幽光鳐") > fishing_xp("金鳞鱼")
    assert fishing_xp("月影巨鲤") >= fishing_xp("金鳞鱼") * 6
    assert fishing_xp("风暴海龙") > fishing_xp("月影巨鲤")
    assert fishing_xp("未知物品") == 0


def test_bite_and_hook_skills_are_separate():
    assert bite_skill_from_reply("2") == "mooch_post"
    assert bite_skill_from_reply("3") == "give_up"
    assert hook_skill_from_reply("1") == "precision_hookset"
    assert hook_skill_from_reply("4") == "burst_reel"
    assert hook_skill_bonus("burst_reel") > hook_skill_bonus("precision_hookset")


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
    for _ in range(2):
        path_progress(state, "河湾巨鲤", "humble_bait")
    assert state["king_paths"]["河湾巨鲤"]["guaranteed"] is True


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
    attempts = scheduled_attempts(
        start,
        [timedelta(seconds=fishing_wait_seconds(10))] * 5,
        include_start=False,
    )
    assert attempts == [start + timedelta(minutes=index) for index in range(1, 6)]


def test_fishing_wait_varies_by_result_tier_and_chinese_counts_parse():
    fast = fishing_wait_for_result("鳕鱼", rng=random.Random(1))
    rare = fishing_wait_for_result("云潮鲨", rng=random.Random(1))
    king = fishing_wait_for_result("风暴海龙", rng=random.Random(1))
    assert 30 <= fast <= 50
    assert rare > fast
    assert king > rare
    assert fishing_wait_for_result("鳕鱼", induce=20, rng=random.Random(1)) >= 30
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
