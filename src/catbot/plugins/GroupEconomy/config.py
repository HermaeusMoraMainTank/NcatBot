DEFAULT_CONFIG = {
    "checkin_min_reward": 30,
    "checkin_max_reward": 60,
    "gift_min_amount": 30,
    "gift_max_amount": 60,
    "gift_wallet_reserve": 0,
    "gift_unlimited_users": ["273421673"],
    "gift_favor_min": -2,
    "gift_favor_max": 2,
    "background_url": "https://pic.re/image",
    "background_timeout": 10,
    "background_cache_hours": 24,
    "ranking_limit": 10,
}

XP_PER_CHECKIN = 30
MAX_LEVEL = 30


def xp_for_level(level: int) -> int:
    """Return cumulative XP required to reach a level.

    The per-level requirement is 100 + 25 * level + 5 * level**2,
    so the gap between levels grows over time while early levels remain quick.
    """
    level = max(0, min(int(level), MAX_LEVEL))
    return sum(100 + 25 * current + 5 * current * current for current in range(level))


def level_for_xp(xp: int) -> int:
    xp = max(0, int(xp))
    for level in range(MAX_LEVEL, -1, -1):
        if xp >= xp_for_level(level):
            return level
    return 0
