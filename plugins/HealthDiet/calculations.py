from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

ACTIVITY_FACTORS = {
    "sedentary": (1.20, "久坐"),
    "light": (1.375, "轻度活动"),
    "moderate": (1.55, "中等活动"),
    "high": (1.725, "高强度活动"),
}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_sex(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"男", "男性", "male", "man", "m"}:
        return "男"
    if text in {"女", "女性", "female", "woman", "f"}:
        return "女"
    return ""


def normalize_activity(value: Any) -> tuple[str, float, str]:
    text = str(value or "").strip().lower()
    aliases = {
        "久坐": "sedentary", "sedentary": "sedentary", "低": "sedentary",
        "轻度": "light", "light": "light",
        "中等": "moderate", "moderate": "moderate",
        "高": "high", "high": "high",
    }
    key = aliases.get(text, "sedentary")
    factor, label = ACTIVITY_FACTORS[key]
    return key, factor, label


def calculate_energy(profile: dict[str, Any], deficit_kcal: float = 500) -> dict[str, Any]:
    """Calculate BMR/TDEE after AI has extracted a structured profile."""
    sex = normalize_sex(profile.get("sex"))
    age = _number(profile.get("age"))
    height = _number(profile.get("height_cm"))
    weight = _number(profile.get("weight_kg"))
    if not sex or age <= 0 or height <= 0 or weight <= 0:
        raise ValueError("性别、年龄、身高和体重都是必填项")

    _, factor, activity_label = normalize_activity(profile.get("activity_level"))
    sex_offset = 5 if sex == "男" else -161
    bmr = 10 * weight + 6.25 * height - 5 * age + sex_offset
    commute_km = max(0.0, min(200.0, _number(profile.get("commute_km"))))
    # 电动车通勤按低强度活动保守估算，避免把通勤重复乘入活动系数。
    commute_add = commute_km * 6
    tdee = bmr * factor + commute_add

    goal = str(profile.get("goal") or "维持").strip()
    requested_target = _number(profile.get("target_kcal"))
    if requested_target > 0:
        target = requested_target
    elif any(word in goal for word in ("减", "瘦", "脂")):
        target = tdee - max(0.0, deficit_kcal)
    else:
        target = tdee

    return {
        "bmr_kcal": round(max(0.0, bmr)),
        "tdee_kcal": round(max(0.0, tdee)),
        "target_kcal": round(max(0.0, target)),
        "activity_label": activity_label,
        "commute_add_kcal": round(commute_add),
    }


def sum_items(items: list[dict[str, Any]]) -> dict[str, float]:
    fields = ("calories_kcal", "protein_g", "fat_g", "carbs_g")
    return {field: round(sum(_number(item.get(field)) for item in items), 1) for field in fields}


def macro_percentages(totals: dict[str, Any]) -> dict[str, float]:
    protein = max(0.0, _number(totals.get("protein_g"))) * 4
    fat = max(0.0, _number(totals.get("fat_g"))) * 9
    carbs = max(0.0, _number(totals.get("carbs_g"))) * 4
    energy = protein + fat + carbs
    if energy <= 0:
        return {"protein_pct": 0.0, "fat_pct": 0.0, "carbs_pct": 0.0}
    return {
        "protein_pct": round(protein / energy * 100, 1),
        "fat_pct": round(fat / energy * 100, 1),
        "carbs_pct": round(carbs / energy * 100, 1),
    }


def macro_assessment(totals: dict[str, Any]) -> list[str]:
    percentages = macro_percentages(totals)
    checks = (
        ("蛋白质", percentages["protein_pct"], 15, 35),
        ("脂肪", percentages["fat_pct"], 20, 35),
        ("碳水", percentages["carbs_pct"], 45, 65),
    )
    result = []
    for label, value, low, high in checks:
        status = "偏低" if value < low else "偏高" if value > high else "基本合适"
        result.append(f"{label}{status}（{value:g}%）")
    return result


def intake_progress(now: datetime, target_kcal: float) -> dict[str, Any]:
    """Estimate how much of today's target is normally expected by this time."""
    hour = now.hour + now.minute / 60
    if hour < 6:
        ratio, label = 0.0, "早餐前"
    elif hour < 11:
        ratio, label = 0.25, "早餐后、午餐前"
    elif hour < 15:
        ratio, label = 0.60, "午餐后"
    elif hour < 18:
        ratio, label = 0.65, "晚餐前"
    elif hour < 22:
        ratio, label = 0.90, "晚餐后"
    else:
        ratio, label = 1.0, "全天结束"
    target = max(0.0, _number(target_kcal))
    return {
        "stage_label": label,
        "stage_ratio": ratio,
        "stage_target_kcal": round(target * ratio),
        "target_kcal": round(target),
    }


def current_intake_assessment(actual_kcal: float, target_kcal: float, now: datetime) -> dict[str, Any]:
    """Compare today's intake with the portion of the target expected so far."""
    progress = intake_progress(now, target_kcal)
    actual = max(0.0, _number(actual_kcal))
    stage_target = progress["stage_target_kcal"]
    target = progress["target_kcal"]
    tolerance = target * 0.15
    if not target or progress["stage_ratio"] == 0:
        status = "当前尚未进入主要进餐时段，暂不评价全天热量缺口"
    elif actual < stage_target - tolerance:
        status = "按当前时段摄入偏低，但当天尚未结束，不能直接判定为全天严重缺口"
    elif actual > stage_target + tolerance:
        status = "按当前时段摄入偏高，后续可适当清淡"
    else:
        status = "按当前时段摄入基本合适，继续记录当天后续饮食"
    return {**progress, "actual_kcal": round(actual, 1), "status": status}


def weekly_summary(daily_rows: list[dict[str, Any]], profile: dict[str, Any] | None) -> dict[str, Any]:
    recorded = [row for row in daily_rows if _number(row.get("calories_kcal")) > 0]
    total = sum(_number(row.get("calories_kcal")) for row in recorded)
    days = len(recorded)
    average = total / days if days else 0.0
    tdee = _number((profile or {}).get("tdee_kcal"))
    average_deficit = tdee - average if tdee and days else None
    weekly_jin = average_deficit * 7 / 3850 if average_deficit is not None else None
    return {
        "recorded_days": days,
        "total_calories": round(total, 1),
        "average_calories": round(average, 1),
        "average_deficit": round(average_deficit, 1) if average_deficit is not None else None,
        "weekly_jin": round(weekly_jin, 2) if weekly_jin is not None else None,
    }


def date_range(days: int = 7, end: date | None = None) -> list[str]:
    end = end or datetime.now(UTC).date()
    return [(end - timedelta(days=offset)).isoformat() for offset in range(days - 1, -1, -1)]
