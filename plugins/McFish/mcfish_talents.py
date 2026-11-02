"""Fishing talent tree for McFish (P6).

25 points at level 100: one per five levels plus five at max level.  Effects
use the same keys as equipment set bonuses so both can be merged into one
passive-effect dictionary.
"""

from __future__ import annotations

from .mcfish_effects import format_effect

TALENTS = {
    "keen": {"label": "敏锐触觉", "branch": "A", "max": 5, "unlock": 1, "requires": None,
             "effects": {"wait": 0.04}},
    "steady": {"label": "稳线", "branch": "A", "max": 3, "unlock": 1, "requires": ("keen", 2),
               "effects": {"air": 0.03}},
    "light_cast": {"label": "轻巧抛竿", "branch": "A", "max": 3, "unlock": 1, "requires": ("steady", 2),
                   "effects": {"bait_save": 0.05}},
    "advantage": {"label": "垂钓者的优势", "branch": "B", "max": 4, "unlock": 20, "requires": None,
                  "effects": {"rarity": 0.05}},
    "treasure": {"label": "寻宝直觉", "branch": "B", "max": 3, "unlock": 20, "requires": ("advantage", 2),
                 "effects": {"valuable": 0.08}},
    "deep_eye": {"label": "深海之眼", "branch": "B", "max": 3, "unlock": 20, "requires": ("treasure", 2),
                 "effects": {"hidden": 0.10}},
    "trader": {"label": "老练鱼贩", "branch": "C", "max": 5, "unlock": 30, "requires": None,
               "effects": {"fish_price": 0.02}},
    "collector": {"label": "碎片收集者", "branch": "C", "max": 4, "unlock": 30, "requires": ("trader", 2),
                  "effects": {"fragment": 2}},
    "material_expert": {"label": "材料专家", "branch": "C", "max": 3, "unlock": 30, "requires": ("collector", 2),
                        "effects": {"material": 0.06}},
    "stamina": {"label": "耐力", "branch": "D", "max": 4, "unlock": 40, "requires": None,
                "effects": {"daily": 5}},
    "cooking": {"label": "鱼类料理", "branch": "D", "max": 3, "unlock": 40, "requires": ("stamina", 2),
                "effects": {"food": 0.25}},
    "handy": {"label": "巧手", "branch": "D", "max": 3, "unlock": 40, "requires": ("cooking", 2),
              "effects": {"fees": 0.06}},
    "sense": {"label": "钓感", "branch": "E", "max": 5, "unlock": 50, "requires": None,
              "effects": {"fp": 4}},
    "rod_mastery": {"label": "竿术", "branch": "E", "max": 4, "unlock": 50, "requires": ("sense", 3),
                    "effects": {"rod_power": 0.05}},
}

BRANCH_LABELS = {"A": "手感", "B": "眼力", "C": "收获", "D": "体力", "E": "钓鱼力"}
MAX_POINTS = 25
RESET_BASE = 20000
RESET_STEP = 10000
RESET_CAP = 70000


def levels(state: dict) -> dict[str, int]:
    return (state.get("talents") or {}).get("levels") or {}


def total_points(level: int) -> int:
    level = max(1, int(level))
    return level // 5 + (5 if level >= 100 else 0)


def used_points(state: dict) -> int:
    return sum(max(0, int(value)) for value in levels(state).values())


def free_points(state: dict) -> int:
    level = int(state.get("fish_level", 1))
    return max(0, total_points(level) - used_points(state))


def talent_by_label(text: str) -> str | None:
    text = text.strip()
    if text in TALENTS:
        return text
    for key, node in TALENTS.items():
        if node["label"] == text:
            return key
    return None


def can_learn(state: dict, key: str) -> tuple[bool, str]:
    node = TALENTS.get(key)
    if not node:
        return False, "未知天赋"
    level = int(state.get("fish_level", 1))
    if level < int(node["unlock"]):
        return False, f"需要等级 {node['unlock']}"
    current = int(levels(state).get(key, 0))
    if current >= int(node["max"]):
        return False, "已满级"
    requires = node.get("requires")
    if requires and int(levels(state).get(requires[0], 0)) < int(requires[1]):
        return False, f"需要 {TALENTS[requires[0]]['label']} {requires[1]} 级"
    if free_points(state) <= 0:
        return False, "天赋点 0"
    return True, ""


def learn(state: dict, key: str) -> tuple[bool, str]:
    ok, reason = can_learn(state, key)
    if not ok:
        return False, reason
    bucket = state.setdefault("talents", {})
    node_levels = bucket.setdefault("levels", {})
    node_levels[key] = int(node_levels.get(key, 0)) + 1
    return True, ""


def effects(state: dict) -> dict[str, float]:
    result: dict[str, float] = {}
    node_levels = levels(state)
    for key, node in TALENTS.items():
        rank = int(node_levels.get(key, 0))
        if rank <= 0:
            continue
        for effect_key, value in node["effects"].items():
            if effect_key in ("fp", "rod_power"):
                continue
            result[effect_key] = result.get(effect_key, 0.0) + float(value) * rank
    return result


def reset_cost(state: dict) -> int:
    count = int((state.get("talents") or {}).get("reset_count", 0))
    return min(RESET_CAP, RESET_BASE + RESET_STEP * count)


def reset(state: dict) -> None:
    bucket = state.setdefault("talents", {})
    bucket["levels"] = {}
    bucket["reset_count"] = int(bucket.get("reset_count", 0)) + 1


def sanitize(state: dict) -> int:
    """清理不满足前置或超过上限的加点，并把点数退回可用池。

    点数是从 ``levels`` 推导出来的，所以删掉非法等级本身就会退还点数，
    不会吞掉玩家已经获得的技能点。返回被退还的点数。
    """
    bucket = state.get("talents") or {}
    node_levels = bucket.get("levels") or {}
    if not node_levels:
        return 0
    refunded = 0
    for key, node in TALENTS.items():
        rank = int(node_levels.get(key, 0))
        if rank > int(node["max"]):
            refunded += rank - int(node["max"])
            node_levels[key] = int(node["max"])
    changed = True
    while changed:
        changed = False
        for key, node in TALENTS.items():
            rank = int(node_levels.get(key, 0))
            if rank <= 0:
                continue
            requires = node.get("requires")
            if not requires:
                continue
            need_key, need_rank = requires
            if int(node_levels.get(need_key, 0)) < int(need_rank):
                refunded += rank
                node_levels.pop(key, None)
                changed = True
    for key in [key for key, value in node_levels.items() if int(value) <= 0]:
        node_levels.pop(key)
    return refunded


def node_status(state: dict, key: str) -> str:
    """Return one of: full / ready / no_points / level_lock / prereq_lock."""
    node = TALENTS[key]
    node_levels = levels(state)
    rank = int(node_levels.get(key, 0))
    if rank >= int(node["max"]):
        return "full"
    if int(state.get("fish_level", 1)) < int(node["unlock"]):
        return "level_lock"
    requires = node.get("requires")
    if requires and int(node_levels.get(requires[0], 0)) < int(requires[1]):
        return "prereq_lock"
    if free_points(state) <= 0:
        return "no_points"
    return "ready"


def tree_lines(state: dict) -> list[str]:
    """Render each branch as an indented tree with prerequisites and lock state."""
    node_levels = levels(state)
    points = free_points(state)
    total = total_points(int(state.get("fish_level", 1)))
    lines = [f"钓鱼天赋 可用 {points} / 共 {total}（等级 {int(state.get('fish_level', 1))}）"]
    for branch in ("A", "B", "C", "D", "E"):
        lines.append("")
        lines.append(f"{branch} {BRANCH_LABELS[branch]}")
        nodes = [key for key, node in TALENTS.items() if node["branch"] == branch]
        for depth, key in enumerate(nodes):
            node = TALENTS[key]
            rank = int(node_levels.get(key, 0))
            effect_text = "、".join(
                format_effect(effect_key, value)
                for effect_key, value in node["effects"].items()
            )
            status = node_status(state, key)
            if status == "full":
                state_text = "已满"
            elif status == "ready":
                state_text = "可加点"
            elif status == "no_points":
                state_text = "无点数"
            elif status == "level_lock":
                state_text = f"锁定：需要等级 {node['unlock']}"
            else:
                requires = node["requires"]
                state_text = f"锁定：需要 {TALENTS[requires[0]]['label']} {requires[1]} 级"
            indent = "    " * depth
            connector = "└ " if depth else ""
            require_note = ""
            if node.get("requires"):
                requires = node["requires"]
                require_note = f"（前置 {TALENTS[requires[0]]['label']} {requires[1]}）"
            lines.append(f"{indent}{connector}{node['label']} {rank}/{node['max']}  {effect_text}  [{state_text}]{require_note}")
    return lines


def summary_lines(state: dict) -> list[str]:
    node_levels = levels(state)
    points = free_points(state)
    total = total_points(int(state.get("fish_level", 1)))
    lines = [f"钓鱼天赋 可用 {points} / 共 {total}"]
    for branch in ("A", "B", "C", "D", "E"):
        nodes = [key for key, node in TALENTS.items() if node["branch"] == branch]
        lines.append("")
        lines.append(f"{branch} {BRANCH_LABELS[branch]}")
        for key in nodes:
            node = TALENTS[key]
            rank = int(node_levels.get(key, 0))
            effect_text = "、".join(
                format_effect(effect_key, value)
                for effect_key, value in node["effects"].items()
            )
            need = f"（{node['unlock']}级解锁）" if int(state.get("fish_level", 1)) < int(node["unlock"]) else ""
            lines.append(f"{node['label']} {rank}/{node['max']}：{effect_text}{need}")
    return lines
