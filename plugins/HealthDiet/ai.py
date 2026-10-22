from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any

from ncatbot.utils import get_log

try:
    from common.utils.AiUtil import AiUtil
except ModuleNotFoundError:  # 直接导入仓库源码时，plugins 根目录尚未加入 sys.path
    from plugins.common.utils.AiUtil import AiUtil


LOG = get_log("HealthDiet")


class AIParseError(ValueError):
    """AI output is missing or does not match the required schema."""


def _json_payload(text: str) -> dict[str, Any]:
    cleaned = str(text or "")
    cleaned = cleaned.replace("\ufeff", "").replace("\u200b", "").strip()
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.IGNORECASE | re.DOTALL).strip()

    candidates = [cleaned]
    candidates.extend(match.group(1).strip() for match in re.finditer(r"```(?:json)?\s*([\s\S]*?)```", cleaned, re.IGNORECASE))

    decoder = json.JSONDecoder()
    for candidate in candidates:
        if not candidate:
            continue
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            value = None
        if isinstance(value, dict):
            return value
        if isinstance(value, list) and candidate.lstrip().startswith("["):
            return {"_array": value}
        if candidate.lstrip().startswith("["):
            continue

        array_start = candidate.find("[")
        object_start = candidate.find("{")
        if array_start >= 0 and (object_start < 0 or array_start < object_start):
            try:
                value, _ = decoder.raw_decode(candidate[array_start:])
            except json.JSONDecodeError:
                pass
            else:
                if isinstance(value, list):
                    return {"_array": value}

        start = candidate.find("{")
        if start >= 0:
            try:
                value, _ = decoder.raw_decode(candidate[start:])
            except json.JSONDecodeError:
                pass
            else:
                if isinstance(value, dict):
                    return value

    if not cleaned:
        raise AIParseError("AI 没有返回结构化数据")
    raise AIParseError("AI 返回的数据不是有效 JSON，可能包含额外说明或已被截断")


async def _chat_json(
    system: str,
    user: str,
    *,
    max_tokens: int | None = None,
    request_timeout: float | None = None,
    retries: int = 2,
) -> dict[str, Any]:
    try:
        response = await AiUtil.search_deepseek(
            user,
            system,
            max_tokens=max_tokens,
            temperature=0.1,
            request_timeout=request_timeout,
            retries=retries,
        )
    except Exception as exc:
        raise AIParseError(f"AI 请求失败：{exc}") from exc
    if isinstance(response, dict):
        response_text = response.get("content", "")
    else:
        response_text = response or ""
    if not response_text:
        raise AIParseError("统一 AI 服务没有返回结果")
    try:
        return _json_payload(str(response_text))
    except AIParseError:
        LOG.warning("[HealthDiet] AI 结构化响应解析失败，原文前500字符：%r", str(response_text)[:500])
        raise


async def analyze_assessment(
    *,
    local_date: str,
    daily: dict[str, Any],
    macro_lines: list[str],
    weekly: dict[str, Any],
    foods: list[str],
    profile: dict[str, Any] | None,
    group: bool,
    current_progress: dict[str, Any] | None = None,
) -> str:
    """Generate readable advice after deterministic assessment calculations."""
    if group:
        profile_context = "群聊模式：不要提及或推算身高、体重、年龄、职业、每日消耗、目标热量、热量差或减重速度。不要输出目标数值。"
    elif profile:
        profile_context = (
            f"个人档案：性别={profile.get('sex') or '未知'}，年龄={profile.get('age') or '未知'}，"
            f"身高={profile.get('height_cm') or '未知'}cm，体重={profile.get('weight_kg') or '未知'}kg，"
            f"职业/活动={profile.get('occupation') or '未知'} / {profile.get('activity_description') or '未知'}，"
            f"每日消耗={profile.get('tdee_kcal') or '未知'}kcal，目标摄入={profile.get('target_kcal') or '未知'}kcal。"
        )
    else:
        profile_context = "尚未建立完整健康档案，不要猜测用户的身体数据或目标热量。"

    prompt = """
你是健康饮食分析助手。请根据给定的结构化记录，用中文给出简洁但具体的饮食建议。
只返回普通纯文本，不要 JSON、不要 markdown 表格、不要思考过程。
必须包含四部分，每部分单独一行或多行：
1. 今日总体判断：摄入是否均衡，指出最重要的问题。
2. 营养缺口：明确指出蛋白质、脂肪、碳水、蔬菜水果或膳食纤维中需要关注的项目；不要为了凑齐项目而虚构缺口。
3. 今天剩余时间怎么吃：给出 1-3 个可执行的食物组合，并说明优先补什么；如果当天已接近充足，就建议清淡或无需强行加餐。
4. 注意事项：指出估算误差或需要用户留意的地方。
建议必须基于已提供的食物和营养数据，不要编造用户没有吃过的食物，不要做疾病诊断，不要把估算当作医疗结论。
""".strip()
    progress_context = ""
    if current_progress and not group:
        progress_context = (
            f"当前时间阶段：{current_progress.get('stage_label')}；截至当前阶段参考摄入约 "
            f"{current_progress.get('stage_target_kcal')}kcal，而不是全天目标；"
            f"阶段判断：{current_progress.get('status')}。\n"
        )
    elif current_progress and group:
        progress_context = "群聊模式：只能说当前时段是否适合继续记录或进食，不得输出阶段目标或全天目标数值。\n"
    user = (
        f"记录日期：{local_date}\n"
        f"{profile_context}\n"
        f"{progress_context}"
        f"当日摄入：热量={daily.get('calories_kcal', 0)}kcal，蛋白质={daily.get('protein_g', 0)}g，"
        f"脂肪={daily.get('fat_g', 0)}g，碳水={daily.get('carbs_g', 0)}g。\n"
        f"营养判断：{'；'.join(macro_lines) or '暂无足够数据'}\n"
        f"近7天：记录天数={weekly.get('recorded_days', 0)}/7，平均摄入={weekly.get('average_calories', 0)}kcal/天，"
        f"合计={weekly.get('total_calories', 0)}kcal。\n"
        f"今日食物明细：{'；'.join(foods) or '暂无饮食记录'}"
    )
    try:
        response = await AiUtil.search_deepseek(
            user,
            prompt,
            max_tokens=None,
            temperature=0.4,
            request_timeout=None,
            retries=2,
        )
    except Exception as exc:
        raise AIParseError(f"AI 分析请求失败：{exc}") from exc
    content = response.get("content", "") if isinstance(response, dict) else str(response or "")
    content = str(content).strip()
    if not content:
        raise AIParseError("AI 分析没有返回内容")
    return content


PROFILE_SYSTEM = """
你是健康档案信息抽取器。只输出 JSON，不要 markdown，不要解释。
字段：display_name（字符串，可空）、sex（男或女，无法判断为空）、age（数字或 null）、
height_cm（数字或 null）、weight_kg（数字或 null）、occupation（字符串）、
activity_level（sedentary/light/moderate/high）、activity_description（字符串）、
commute_km（每天往返公里数，数字）、goal（字符串）、target_kcal（用户明确给出的目标热量，数字或 null）。
斤换算为 kg 时除以 2；不要猜测缺失的年龄、性别、身高或体重。
""".strip()


MEAL_SYSTEM = """
你是饮食记录抽取器。只返回一个完整合法 JSON，不要 markdown、解释或思考过程。
格式：{"meal_type":"breakfast|lunch|dinner|snack|other","items":[{"name":"食物","quantity":1,"unit":"份","grams":0,"calories_kcal":0,"protein_g":0,"fat_g":0,"carbs_g":0,"confidence":"medium","assumptions":""}]}。
每种食物必须单独一项；数字字段必须是数字。多个餐次也放在同一个 items 数组。无糖饮品仍按实际原料估算热量。
蛋白粉、牛奶、奶粉、乳清蛋白、代餐粉等必须识别为独立食物；“一勺蛋白粉用500g牛奶冲泡”必须拆成蛋白粉和牛奶两项，不能返回空 items。蛋白粉缺少品牌时按一勺约30g估算，牛奶按用户给出的克数估算。
如果用户提供包装营养标签（例如每100g多少千焦、蛋白质、脂肪、碳水），必须按实际食用克数换算，不得返回空 items。维生素 C 泡腾片也要作为独立食物记录；没有营养标签时热量可暂按 0 估算并注明不确定。
""".strip()


MEAL_RETRY_SYSTEM = """
你是饮食记录复核器。上一次识别结果为空或无效，请重新完整读取用户原文，逐项识别所有吃过或喝过的东西。
只返回一个完整合法 JSON，不要 markdown、解释或思考过程。
格式：{"meal_type":"breakfast|lunch|dinner|snack|other","items":[{"name":"食物","quantity":1,"unit":"份","grams":0,"calories_kcal":0,"protein_g":0,"fat_g":0,"carbs_g":0,"confidence":"medium","assumptions":""}]}。
不要因为食物品牌、生僻名称、包装营养标签或营养素含量很小而丢弃项目。每个食物、饮料、保健品或泡腾片都必须独立放入 items。
如果原文有“每100g/每份”的营养标签，按实际食用量换算；千焦除以4.184换算为千卡。无法确定的数值可估算并在 assumptions 说明，但不能返回空 items。
""".strip()


def _float(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        match = re.search(r"[-+]?\d+(?:\.\d+)?", str(value).replace(",", ""))
        return float(match.group()) if match else default


def _meal_type(text: str, current: str, current_time: datetime | None = None) -> str:
    if any(word in text for word in ("早餐", "早饭", "早上")):
        return "breakfast"
    if any(word in text for word in ("午餐", "午饭", "中午")):
        return "lunch"
    if any(word in text for word in ("零食", "加餐", "下午茶", "下午", "夜宵", "中间")):
        return "snack"
    if any(word in text for word in ("晚餐", "晚饭", "晚上")):
        return "dinner"
    if current_time is not None:
        hour = current_time.hour + current_time.minute / 60
        if 6 <= hour < 11:
            return "breakfast"
        if 11 <= hour < 15:
            return "lunch"
        if 15 <= hour < 18:
            return "snack"
        if 18 <= hour < 22:
            return "dinner"
        return "snack"
    if current in {"breakfast", "lunch", "dinner", "snack"}:
        return current
    return "other"


async def parse_profile(description: str) -> tuple[dict[str, Any], list[str]]:
    raw = await _chat_json(PROFILE_SYSTEM, description)
    profile = {
        "display_name": str(raw.get("display_name") or "").strip(),
        "sex": str(raw.get("sex") or "").strip(),
        "age": _float(raw.get("age"), 0),
        "height_cm": _float(raw.get("height_cm"), 0),
        "weight_kg": _float(raw.get("weight_kg"), 0),
        "occupation": str(raw.get("occupation") or "").strip(),
        "activity_level": str(raw.get("activity_level") or "sedentary").strip(),
        "activity_description": str(raw.get("activity_description") or "").strip(),
        "commute_km": _float(raw.get("commute_km"), 0),
        "goal": str(raw.get("goal") or "维持").strip(),
        "target_kcal": _float(raw.get("target_kcal"), 0),
    }
    missing = []
    for key, label in (("sex", "性别"), ("age", "年龄"), ("height_cm", "身高"), ("weight_kg", "体重")):
        if not profile[key]:
            missing.append(label)
    return profile, missing


_MEAL_MARKER = re.compile(
    r"上午加餐|早上加餐|中午加餐|下午加餐|晚上(?:又)?(?:加餐|零食)|下午茶|早餐|早饭|早上|午餐|午饭|中午|晚餐|晚饭|"
    r"晚上(?!\s*(?:又)?\s*(?:加餐|零食))|夜宵|加餐|零食|中间|下午"
)


def _meal_segments(description: str) -> list[str]:
    matches = list(_MEAL_MARKER.finditer(description))
    if len(matches) <= 1:
        return [description]
    return [description[match.start() : matches[index + 1].start() if index + 1 < len(matches) else None].strip() for index, match in enumerate(matches)]


def _normalize_meal_response(raw: dict[str, Any], description: str, current_time: datetime | None) -> dict[str, Any]:
    array_payload = raw.get("_array")
    if isinstance(array_payload, list) and array_payload and all(
        isinstance(group, dict) and isinstance(group.get("items"), list) for group in array_payload
    ):
        raw_groups = [(str(group.get("meal_type") or ""), group["items"]) for group in array_payload]
    elif isinstance(array_payload, list):
        raw_groups = [("", array_payload)]
    else:
        items = raw.get("items")
        if items is None:
            items = raw.get("foods")
        if items is None:
            items = raw.get("food_items")
        raw_groups = [(str(raw.get("meal_type") or ""), items)]
    if not raw_groups or any(not isinstance(items, list) for _, items in raw_groups):
        raise AIParseError("没有识别到可记录的食物")
    if not any(items for _, items in raw_groups):
        raise AIParseError("AI 返回了空食物明细")
    groups = []
    for declared_type, items in raw_groups:
        normalized = []
        for item in items:
            if not isinstance(item, dict):
                raise AIParseError("AI 返回了无效的食物明细")
            name = str(item.get("name") or item.get("food") or item.get("food_name") or "").strip()
            if not name:
                raise AIParseError("AI 返回了无效的食物明细")
            normalized.append(
                {
                    "name": name,
                    "quantity": max(0.0, _float(item.get("quantity"), 1)),
                    "unit": str(item.get("unit") or "份").strip(),
                    "grams": max(0.0, _float(item.get("grams", item.get("weight_g")), 0)),
                    "calories_kcal": max(0.0, _float(item.get("calories_kcal", item.get("calories", item.get("kcal"))), 0)),
                    "protein_g": max(0.0, _float(item.get("protein_g", item.get("protein")), 0)),
                    "fat_g": max(0.0, _float(item.get("fat_g", item.get("fat")), 0)),
                    "carbs_g": max(
                        0.0,
                        _float(
                            item.get(
                                "carbs_g",
                                item.get("carbohydrates_g", item.get("carbohydrates", item.get("carbs"))),
                            ),
                            0,
                        ),
                    ),
                    "confidence": str(item.get("confidence") or "medium"),
                    "assumptions": str(item.get("assumptions") or "").strip(),
                }
            )
        groups.append({"meal_type": _meal_type(description, declared_type, current_time), "items": normalized})
    items = [item for group in groups for item in group["items"]]
    meal_types = {str(group["meal_type"]) for group in groups}
    return {"meal_type": next(iter(meal_types)) if len(meal_types) == 1 else "other", "items": items, "meals": groups}


async def _parse_meal_segment(description: str, record_date: date, current_time: datetime | None) -> dict[str, Any]:
    user_message = f"记录日期：{record_date.isoformat()}\n用户描述：{description}"
    last_error: AIParseError | None = None
    for attempt, system in enumerate((MEAL_SYSTEM, MEAL_RETRY_SYSTEM), start=1):
        try:
            raw = await _chat_json(
                system,
                user_message,
                max_tokens=None,
                request_timeout=None,
                retries=1,
            )
            return _normalize_meal_response(raw, description, current_time)
        except AIParseError as exc:
            last_error = exc
            if attempt == 1:
                LOG.warning("饮食第1次 AI 解析为空或无效，将重新解析：%s", exc)
    raise last_error or AIParseError("没有识别到可记录的食物")


async def parse_meal(description: str, record_date: date, current_time: datetime | None = None) -> dict[str, Any]:
    meals = [await _parse_meal_segment(segment, record_date, current_time) for segment in _meal_segments(description)]
    items = [item for meal in meals for item in meal["items"]]
    groups = [group for meal in meals for group in meal["meals"]]
    meal_types = {str(group["meal_type"]) for group in groups}
    return {
        "meal_type": next(iter(meal_types)) if len(meal_types) == 1 else "other",
        "items": items,
        "meals": groups,
    }
