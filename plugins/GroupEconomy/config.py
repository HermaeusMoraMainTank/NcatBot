DEFAULT_CONFIG = {
    "checkin_min_reward": 10,
    "checkin_max_reward": 30,
    "gift_min_amount": 1,
    "gift_max_amount": 30,
    "gift_wallet_reserve": 0,
    "gift_unlimited_users": ["273421673"],
    "gift_favor_min": -5,
    "gift_favor_max": 5,
    "background_url": "https://pic.re/image",
    "background_timeout": 10,
    "background_cache_hours": 24,
    "ranking_limit": 10,
}

LEVEL_THRESHOLDS = (0, 10, 20, 50, 100, 200, 350, 550, 750, 1000, 1200)


def level_for_score(score: int) -> int:
    level = 0
    for index, threshold in enumerate(LEVEL_THRESHOLDS):
        if score >= threshold:
            level = index
        else:
            break
    return level
