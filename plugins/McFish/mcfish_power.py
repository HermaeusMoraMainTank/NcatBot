"""Fishing power (FP) for McFish.

FP is the single number players compare: level + rod + equipment + jewelry +
trinkets + talents + temporary buffs.  Phase P0 only computes and displays it;
later phases let the drop pipeline and spot gates read the same value.
"""

from __future__ import annotations

# 旧竿名 → FP。P1 改名为 铜/锡/铁/猩红/幻光/星河/灿阳 后，数值保持不变。
ROD_POWER = {
    "木竿": 0,
    "铜竿": 8,
    "锡竿": 16,
    "铁竿": 26,
    "猩红竿": 38,
    "幻光竿": 50,
    "星河竿": 62,
    "灿阳竿": 74,
    "三叉戟": 90,
}

AXOLOTL_POWER = 20

EQUIPMENT_SLOTS = ("head", "body", "legs")
JEWELRY_SLOTS = ("necklace", "ring1", "ring2")

# 天赋在 P6 实装；这里先定义数据结构与 FP 贡献。
TALENT_SENSE_POWER = 4       # 钓感：每级 +4 FP
TALENT_ROD_MASTERY = 0.05    # 竿术：每级 +5% 鱼竿力

# 装备/首饰在后续阶段写入 {slot: {"name": ..., "fp": N, ...}}。
EQUIPMENT_GROUP = "equipment"
JEWELRY_GROUP = "jewelry"


def level_power(state: dict) -> int:
    return max(1, int(state.get("fish_level", 1) or 1))


def rod_power(state: dict) -> int:
    equip = state.get("equip") or {}
    name = str(equip.get("name", ""))
    if name == "美西螈":
        return AXOLOTL_POWER
    return int(ROD_POWER.get(name, 0))


def _slot_power(state: dict, group: str, slots: tuple[str, ...]) -> int:
    total = 0
    items = state.get(group) or {}
    if not isinstance(items, dict):
        return 0
    for slot in slots:
        item = items.get(slot)
        if isinstance(item, dict):
            fp = item.get("fp")
            if fp is None:
                fp = round(int(item.get("level", 0)) * 0.8)
            total += max(0, int(fp))
    return total


def equipment_power(state: dict) -> int:
    return _slot_power(state, EQUIPMENT_GROUP, EQUIPMENT_SLOTS)


def jewelry_power(state: dict) -> int:
    return _slot_power(state, JEWELRY_GROUP, JEWELRY_SLOTS)


def trinket_power(state: dict) -> int:
    """Flat FP from the original four trinket slots (none grant FP yet)."""
    total = 0
    for name in state.get("accessories") or []:
        item = (state.get("trinket_power") or {}).get(name)
        if item:
            total += max(0, int(item))
    return total


def talent_power(state: dict) -> int:
    levels = (state.get("talents") or {}).get("levels") or {}
    sense = max(0, int(levels.get("sense", 0)))
    mastery = max(0, int(levels.get("rod_mastery", 0)))
    return sense * TALENT_SENSE_POWER + int(rod_power(state) * mastery * TALENT_ROD_MASTERY)


def temporary_power(state: dict) -> int:
    return max(0, int(state.get("fp_bonus", 0)))


def power_breakdown(state: dict) -> list[tuple[str, int]]:
    return [
        ("等级", level_power(state)),
        ("鱼竿", rod_power(state)),
        ("装备", equipment_power(state)),
        ("首饰", jewelry_power(state)),
        ("饰品", trinket_power(state)),
        ("天赋", talent_power(state)),
        ("临时", temporary_power(state)),
    ]


def fishing_power(state: dict) -> int:
    return sum(value for _, value in power_breakdown(state))


def power_summary(state: dict) -> str:
    """One-line breakdown used by panels and messages."""
    parts = [f"{label} {value}" for label, value in power_breakdown(state) if value]
    return f"{fishing_power(state)}（{' + '.join(parts) or '0'}）"
