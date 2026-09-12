"""
好感度系统（B 方案）：有界分数 + 关系等级 + 负向完整表达

分数区间：-50 ~ +100（共 150 点跨度）
- 负向 -50~0：黑名单 / 厌恶 / 反感 / 冷淡 / 略烦
- 正向  0~100：陌生人 ~ 挚友
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

FAVOR_MIN = -50.0
FAVOR_MAX = 100.0
FAVOR_DEFAULT = 50.0  # 新用户中性起点（点头之交偏上）

# 可达满分（100）的核心关系用户
MAX_SCORE_USERS: frozenset[int] = frozenset({273421673, 635773721})

# 仅该用户可在「查印象」等界面看到 relation_tag
RELATION_TAG_VIEWER = 273421673

# 特殊关系：分数下限（与展示用 relation_tag 无关）
RELATION_FLOORS: dict[int, float] = {
    273421673: 95.0,
    635773721: 95.0,
}

# 迁移时写入的默认关系标签（可后续由 AI 或人工覆盖）
DEFAULT_RELATION_TAGS: dict[int, str] = {
    635773721: "闺蜜",
}

# 一次性纠偏（旧 VIP 误顶满的非核心用户）
REBALANCE_SCORE_TARGETS: dict[int, float] = {
    541518108: 88.5,
}


def can_reach_max_score(user_id: Optional[int]) -> bool:
    return user_id in MAX_SCORE_USERS if user_id is not None else False


def score_cap(user_id: Optional[int]) -> float:
    return FAVOR_MAX if can_reach_max_score(user_id) else 99.0


@dataclass(frozen=True)
class FavorTier:
    name: str
    emoji: str
    min_score: float
    max_score: float
    tone_hint: str


# 负向等级（min_score 含，max_score 不含；最高档 max 为 inf）
NEGATIVE_TIERS: tuple[FavorTier, ...] = (
    FavorTier("黑名单", "🖤", -50.0, -35.0, "几乎不想理，能躲就躲"),
    FavorTier("厌恶", "💢", -35.0, -25.0, "明显排斥，回复极少"),
    FavorTier("反感", "😒", -25.0, -15.0, "冷淡带刺，敷衍为主"),
    FavorTier("冷淡", "🌧", -15.0, -5.0, "不太想聊，偶尔回一句"),
    FavorTier("略烦", "😑", -5.0, 0.0, "略感厌烦，态度偏冷"),
)

POSITIVE_TIERS: tuple[FavorTier, ...] = (
    FavorTier("陌生人", "💙", 0.0, 20.0, "客气但疏离"),
    FavorTier("点头之交", "🤝", 20.0, 40.0, "正常群友，不冷不热"),
    FavorTier("熟人", "😊", 40.0, 60.0, "愿意多聊几句"),
    FavorTier("好友", "💗", 60.0, 80.0, "话多、会接梗"),
    FavorTier("亲密", "💖", 80.0, 95.0, "主动关心、语气亲近"),
    FavorTier("挚友", "✨", 95.0, 100.0, "最亲近的人"),
)


def clamp_score(score: float, user_id: Optional[int] = None) -> float:
    score = max(FAVOR_MIN, min(FAVOR_MAX, score))
    cap = score_cap(user_id)
    if score > cap:
        score = cap
    return score


def get_tier(score: float) -> FavorTier:
    score = clamp_score(score)
    if score < 0:
        for tier in NEGATIVE_TIERS:
            if tier.min_score <= score < tier.max_score:
                return tier
        return NEGATIVE_TIERS[0]
    for tier in POSITIVE_TIERS:
        if tier.min_score <= score < tier.max_score:
            return tier
    return POSITIVE_TIERS[-1]


def format_favorability(score: float, relation_label: Optional[str] = None) -> str:
    """展示用：等级 + 分数（一位小数）"""
    tier = get_tier(score)
    label = f"{tier.emoji} {tier.name}"
    if relation_label:
        label = f"{label}({relation_label})"
    return f"{label} {score:.1f}"


def format_favorability_short(score: float) -> str:
    tier = get_tier(score)
    return f"{tier.emoji}{score:.1f}·{tier.name}"


def can_view_relation_tag(viewer_id: Optional[int]) -> bool:
    return viewer_id == RELATION_TAG_VIEWER if viewer_id is not None else False


def normalize_relation_tag(tag: Optional[str]) -> Optional[str]:
    if not tag:
        return None
    tag = str(tag).strip()
    if not tag or tag in ("无", "未知", "空", "none", "null"):
        return None
    return tag[:12]


def get_relation_floor(user_id: int) -> Optional[float]:
    return RELATION_FLOORS.get(user_id)


def apply_favor_change(
    current: float,
    raw_delta: int,
    user_id: Optional[int] = None,
) -> float:
    """应用 AI 输出的单次变化（-3~+3），边际递减 + 有界。"""
    current = clamp_score(current, user_id)
    raw_delta = max(-3, min(3, int(raw_delta)))

    if raw_delta > 0:
        room = score_cap(user_id) - current
        room = max(0.0, room)
        effective = raw_delta * (room / FAVOR_MAX) ** 1.5
    elif raw_delta < 0:
        room = current - FAVOR_MIN
        effective = raw_delta * (room / (FAVOR_MAX - FAVOR_MIN)) ** 1.5
    else:
        effective = 0.0

    new_score = clamp_score(current + effective, user_id)

    floor = get_relation_floor(user_id) if user_id else None
    if floor is not None and new_score < floor:
        new_score = floor

    return round(clamp_score(new_score, user_id), 1)


def finalize_migrated_score(score: float, user_id: int) -> float:
    """迁移后的最终分数（含关系下限 / 满分限制）。"""
    if can_reach_max_score(user_id):
        score = max(score, RELATION_FLOORS.get(user_id, 95.0))
        score = min(100.0, score)
    else:
        score = min(99.0, score)
        floor = get_relation_floor(user_id)
        if floor is not None and score < floor:
            score = floor
    return round(clamp_score(score, user_id), 1)


def migrate_old_score(old: int | float) -> float:
    """旧整数好感度 → 新分数。分段映射，拉开 100~300 主流区间。"""
    old = int(old)

    if old < 0:
        # 旧系统负值可至 -400；映射到 -50~-5，保留相对差距
        return round(max(FAVOR_MIN, old * 0.125), 1)

    if old <= 50:
        return round(15.0 + old * 0.4, 1)  # 0→15, 50→35
    if old <= 150:
        return round(35.0 + (old - 50) * 0.25, 1)  # 150→60
    if old <= 300:
        return round(60.0 + (old - 150) * 0.13, 1)  # 300→79.5
    if old <= 1000:
        return round(80.0 + (old - 300) * 0.014, 1)  # 1000→90
    if old <= 3000:
        return round(90.0 + (old - 1000) * 0.002, 1)  # 3000→94
    if old <= 6000:
        return round(94.0 + (old - 3000) * 0.001, 1)  # 6000→97
    # 6000+ 才接近满分（仍由 finalize 决定是否真到 100）
    return round(min(99.0, 97.0 + (old - 6000) * 0.0008), 1)


def get_reply_probability(score: float) -> float:
    """根据好感度计算回复概率。负向指数衰减；正向略有梯度。"""
    score = clamp_score(score)

    if score >= 60:
        return 1.0
    if score >= 40:
        return 0.95
    if score >= 20:
        return 0.88
    if score >= 0:
        return 0.78

    min_prob = 0.03
    k = 0.06  # 负向区间 -50~-5 内快速下降
    probability = min_prob + (1.0 - min_prob) * math.exp(k * score)
    return max(min_prob, probability)


def tier_tone_for_prompt(score: float) -> str:
    """供 AI prompt 使用的态度描述。"""
    return get_tier(score).tone_hint
