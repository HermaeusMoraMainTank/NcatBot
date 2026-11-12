"""CS2 开箱规则与存储（CU 系列）。"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "plugins"))

from CaseOpening.store_cs2 import CaseStore
from common.case_data import (
    SNAPSHOT_DIR,
    load_cs_snapshot,
)
from common.case_data.rules import (
    RARE_ODDS,
    TIER_ODDS,
    TradeupInput,
    collection_weights,
    market_hash_name,
    next_tier,
    roll_float,
    tradeup_float,
    wear_for_float,
    wear_probabilities,
)


# CU-01 磨损区间左闭右开，边界归下一档
def test_wear_bands():
    assert wear_for_float(0.0) == "Factory New"
    assert wear_for_float(0.069) == "Factory New"
    assert wear_for_float(0.07) == "Minimal Wear"
    assert wear_for_float(0.15) == "Field-Tested"
    assert wear_for_float(0.38) == "Well-Worn"
    assert wear_for_float(0.45) == "Battle-Scarred"
    assert wear_for_float(1.0) == "Battle-Scarred"


# CU-02 磨损概率之和为 1，且与 float 区间一致
def test_wear_probabilities_sum_to_one():
    probs = wear_probabilities(0.06, 0.8)
    assert pytest.approx(sum(probs.values()), abs=1e-9) == 1.0
    narrow = wear_probabilities(0.10, 0.12)
    assert list(narrow) == ["Minimal Wear"]


# CU-03 市场名拼装：StatTrak / ★ 前缀
def test_market_hash_name_prefixes():
    assert market_hash_name("AK-47 | Redline", "Field-Tested") == "AK-47 | Redline (Field-Tested)"
    assert (
        market_hash_name("AK-47 | Redline", "Field-Tested", stattrak=True)
        == "StatTrak™ AK-47 | Redline (Field-Tested)"
    )
    assert market_hash_name("★ Karambit | Fade", "Factory New") == "★ Karambit | Fade (Factory New)"
    assert (
        market_hash_name("★ Karambit | Doppler", "Factory New", stattrak=True)
        == "★ StatTrak™ Karambit | Doppler (Factory New)"
    )


# CU-04 汰换 float 公式：均值按目标区间线性映射
def test_tradeup_float_mapping():
    inputs = [TradeupInput(0.30, "Mil-Spec Grade", False, "c1") for _ in range(10)]
    assert tradeup_float(inputs, 0.0, 0.5) == pytest.approx(0.15)
    assert tradeup_float([TradeupInput(0.1, "Mil-Spec Grade", False, "c1")] * 10, 0.2, 0.6) == pytest.approx(0.24)


# CU-05 收藏品权重只统计"有下一档"的收藏品
def test_collection_weights_skip_ineligible():
    inputs = [
        TradeupInput(0.2, "Mil-Spec Grade", False, "a"),
        TradeupInput(0.2, "Mil-Spec Grade", False, "a"),
        TradeupInput(0.2, "Mil-Spec Grade", False, "b"),
    ]
    assert collection_weights(inputs, {"a": 3, "b": 0}) == {"a": 2}


# CU-06 档位顺序：隐秘没有下一档
def test_next_tier_chain():
    assert next_tier("Mil-Spec Grade") == "Restricted"
    assert next_tier("Restricted") == "Classified"
    assert next_tier("Classified") == "Covert"
    assert next_tier("Covert") is None


# CU-07 快照：10 个箱、稀有池逐箱不同、钥匙映射正确
def test_snapshot_cases():
    cases, items, _ = load_cs_snapshot(SNAPSHOT_DIR / "cs2.json.gz")
    assert len(cases) == 52
    assert "CS:GO Weapon Case" in cases and "Huntsman Weapon Case" in cases
    souvenirs = [c for c in cases.values() if c.tier_odds]
    assert len(souvenirs) == 10
    assert all(c.key_name is None for c in souvenirs)
    assert all("Cobblestone" in c.name for c in souvenirs)
    assert abs(sum(souvenirs[0].tier_odds.values()) - 1.0) < 1e-9
    kilowatt = cases["Kilowatt Case"]
    assert kilowatt.key_name == "Kilowatt Case Key"
    assert {tier: len(ids) for tier, ids in kilowatt.tiers.items()} == {
        "Mil-Spec Grade": 7,
        "Restricted": 5,
        "Classified": 3,
        "Covert": 2,
    }
    assert len(kilowatt.rare) == 13
    assert len(cases["Fracture Case"].rare) == 52
    assert all(skin.rarity_name for skin in items.values())


# CU-08 开箱概率：2 万次抽样接近公示值
def test_roll_distribution_matches_valve_odds():
    from CaseOpening.cs2 import Cs2Case

    cases, items, _ = load_cs_snapshot(SNAPSHOT_DIR / "cs2.json.gz")
    plugin = Cs2Case.__new__(Cs2Case)
    plugin.cases, plugin.items = cases, items
    plugin._rng = random.Random(20261106)
    plugin.prices = _NullPrices()
    plugin.get_config = lambda key, default=None: {"sell_fee": 0.15, "coin_per_cny": 10}.get(key, default)

    crate = cases["Kilowatt Case"]
    counts: dict[str, int] = {}
    total = 20000
    for _ in range(total):
        rolled = plugin._roll_case(crate)
        counts[rolled["tier"]] = counts.get(rolled["tier"], 0) + 1

    for tier, expected in TIER_ODDS.items():
        observed = counts.get(tier, 0) / total
        assert abs(observed - expected) < 0.02, (tier, observed, expected)
    observed_rare = counts.get("Extraordinary", 0) / total
    assert abs(observed_rare - RARE_ODDS) < 0.002


class _NullPrices:
    def sell_price(self, name, fee):
        return None

    def buy_price(self, name):
        return None


# CU-09 存储：物品 / 流水 / 统计
def test_store_inventory_and_stats(tmp_path: Path):
    store = CaseStore(tmp_path / "cs.db")
    item_id = store.add_item(
        "100",
        "9",
        skin_id="skin-1",
        name="AK-47 | Redline",
        wear="Field-Tested",
        float_value=0.22,
        stattrak=False,
        rarity="Covert",
        color="#eb4b4b",
        market_hash_name="AK-47 | Redline (Field-Tested)",
        price_cny=100.0,
    )
    assert store.count("100") == 1
    assert store.item("100", item_id)["name"] == "AK-47 | Redline"
    assert store.item("200", item_id) is None

    store.log_open("100", "Kilowatt Case", 191, "AK-47 | Redline", "Covert", 100.0)
    store.log_market("100", "AK-47 | Redline", "sell", 850)
    stats = store.stats("100")
    assert stats["opens"] == 1 and stats["spent"] == 191 and stats["earned"] == 850
    assert stats["best"]["item_name"] == "AK-47 | Redline"

    assert store.remove_items("100", [item_id]) == 1
    assert store.count("100") == 0
    store.close()


# CU-10 连续两次打开的结果互不影响（RNG 独立）
def test_roll_float_within_range():
    for _ in range(200):
        value = roll_float(0.06, 0.8, random.Random(_))
        assert 0.06 <= value <= 0.8


# CU-11 箱子解析：序号、中文全名、中文别名都可用
def test_case_resolution_by_number_and_chinese():
    from CaseOpening.cs2 import Cs2Case

    cases, _, _ = load_cs_snapshot(SNAPSHOT_DIR / "cs2.json.gz")
    plugin = Cs2Case.__new__(Cs2Case)
    plugin.cases = cases

    first = next(iter(cases))
    assert plugin._resolve_case("1").name == first
    assert plugin._resolve_case("千瓦").name == "Kilowatt Case"
    assert plugin._resolve_case("千瓦武器箱").name == "Kilowatt Case"
    assert plugin._resolve_case("Kilowatt Case").name == "Kilowatt Case"
    assert plugin._resolve_case("不存在的箱子") is None


# CU-12 箱子中文名与图片字段齐全
def test_case_chinese_name_and_image():
    cases, _, _ = load_cs_snapshot(SNAPSHOT_DIR / "cs2.json.gz")
    for crate in cases.values():
        assert crate.cn_name and crate.cn_name != crate.name
        assert crate.image and crate.image.startswith("https://")


# CU-13 中文名：箱子与皮肤都有中文名（来自 ByMykel zh-CN 数据）
def test_chinese_names_present():
    cases, items, _ = load_cs_snapshot(SNAPSHOT_DIR / "cs2.json.gz")
    assert all(case.cn_name for case in cases.values())
    assert all(item.name_cn for item in items.values())
    kilowatt = cases["Kilowatt Case"]
    first = items[kilowatt.items_of("Covert")[0]]
    assert first.name_cn and first.name_cn != first.name


# CU-14 汰换输入：空格/逗号/区间/稀有度自动选
def test_tradeup_arg_parsing():
    from CaseOpening.cs2 import Cs2Case

    plugin = Cs2Case.__new__(Cs2Case)
    assert plugin._parse_tradeup_arg("1 2 3") == ([1, 2, 3], None)
    assert plugin._parse_tradeup_arg("1,2, 3") == ([1, 2, 3], None)
    assert plugin._parse_tradeup_arg("5-14") == (list(range(5, 15)), None)
    assert plugin._parse_tradeup_arg("军规") == (None, "Mil-Spec Grade")
    assert plugin._parse_tradeup_arg("受限") == (None, "Restricted")
    assert plugin._parse_tradeup_arg("") == (None, None)
