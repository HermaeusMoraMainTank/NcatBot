from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image as PillowImage

from ncatbot.types import At

from plugins.HealthDiet import main as health_main
from plugins.HealthDiet.ai import (
    AIParseError,
    _json_payload,
    _meal_segments,
    analyze_assessment,
    parse_meal,
    parse_profile,
)
from plugins.HealthDiet.calculations import (
    calculate_energy,
    current_intake_assessment,
    intake_progress,
    macro_percentages,
    sum_items,
    weekly_summary,
)
from plugins.HealthDiet.main import HealthDiet
from plugins.HealthDiet.render import render_assessment, render_dashboard, render_meal
from plugins.HealthDiet.store import HealthStore


def _item(name: str, calories: float, protein: float, fat: float, carbs: float) -> dict:
    return {
        "name": name,
        "quantity": 1,
        "unit": "份",
        "grams": 100,
        "calories_kcal": calories,
        "protein_g": protein,
        "fat_g": fat,
        "carbs_g": carbs,
        "confidence": "high",
        "assumptions": "",
    }


def _profile() -> dict:
    return {
        "display_name": "测试用户",
        "sex": "男",
        "age": 29,
        "height_cm": 180,
        "weight_kg": 100,
        "occupation": "程序员",
        "activity_level": "sedentary",
        "activity_description": "每天久坐",
        "commute_km": 20,
        "goal": "减脂",
        "target_kcal": 2000,
        "bmr_kcal": 1985,
        "tdee_kcal": 2502,
    }


@pytest.mark.asyncio
async def test_ai_parsers_use_shared_fakeai_opencode_entrypoint(monkeypatch):
    calls = []

    async def fake_search(keyword, prompt, **kwargs):
        calls.append((keyword, prompt, kwargs))
        if "健康档案信息抽取器" in prompt:
            return {"content": '{"sex":"男","age":29,"height_cm":180,"weight_kg":100}'}
        return {
            "content": (
                '{"meal_type":"snack","items":[{"name":"薯片","quantity":1,'
                '"unit":"包","grams":75,"calories_kcal":400,"protein_g":5,'
                '"fat_g":25,"carbs_g":39,"confidence":"medium","assumptions":"按75g估算"}]}'
            )
        }

    monkeypatch.setattr("plugins.HealthDiet.ai.AiUtil.search_deepseek", fake_search)
    profile, missing = await parse_profile("男，29岁，180厘米，200斤")
    meal = await parse_meal("下午吃了一包薯片", date(2026, 9, 20))

    assert not missing
    assert profile["weight_kg"] == 100
    assert meal["items"][0]["name"] == "薯片"
    assert len(calls) == 2
    assert calls[0][2] == {
        "max_tokens": None,
        "temperature": 0.1,
        "request_timeout": None,
        "retries": 2,
    }
    assert all("api.ai" not in str(call) for call in calls)


@pytest.mark.asyncio
async def test_meal_without_explicit_time_uses_current_time_for_meal_type(monkeypatch):
    async def fake_search(*args, **kwargs):
        return {"content": '{"meal_type":"dinner","items":[{"name":"鸡蛋","calories_kcal":100}]}' }

    monkeypatch.setattr("plugins.HealthDiet.ai.AiUtil.search_deepseek", fake_search)
    meal = await parse_meal("吃了一个鸡蛋", date(2026, 9, 25), datetime(2026, 9, 25, 12, 30, tzinfo=UTC))

    assert meal["meal_type"] == "lunch"
    assert meal["meals"][0]["meal_type"] == "lunch"


def test_current_intake_assessment_uses_stage_target_for_today():
    afternoon = datetime(2026, 9, 25, 16, 0, tzinfo=UTC)
    assert intake_progress(afternoon, 2000)["stage_target_kcal"] == 1300
    assessment = current_intake_assessment(900, 2000, afternoon)

    assert assessment["stage_label"] == "晚餐前"
    assert "不能直接判定为全天严重缺口" in assessment["status"]


def test_current_intake_assessment_does_not_call_zero_before_breakfast_a_deficit():
    assessment = current_intake_assessment(0, 2000, datetime(2026, 9, 25, 5, 30, tzinfo=UTC))

    assert assessment["stage_target_kcal"] == 0
    assert "暂不评价全天热量缺口" in assessment["status"]


def test_meal_segments_keep_evening_snack_as_one_segment():
    assert _meal_segments("晚上加餐了一份用500g牛奶冲泡的蛋白粉") == ["晚上加餐了一份用500g牛奶冲泡的蛋白粉"]


def test_meal_segments_keep_evening_again_snack_together():
    description = "晚上又加餐了一包辣条 16g 每100g 1867kj 8蛋白质 24.6脂肪 48.3碳水 然后加了一颗维生素c泡腾片"
    assert _meal_segments(description) == [description]


@pytest.mark.asyncio
async def test_shared_ai_empty_response_is_parse_error(monkeypatch):
    async def fake_search(*args, **kwargs):
        return None

    monkeypatch.setattr("plugins.HealthDiet.ai.AiUtil.search_deepseek", fake_search)

    with pytest.raises(AIParseError, match="统一 AI 服务没有返回结果"):
        await parse_meal("早餐吃了一个鸡蛋", date(2026, 9, 20))


def test_json_payload_accepts_wrapped_json():
    """HD-01: 饮食 AI 返回代码块、思考标签或额外说明时仍提取完整对象。"""
    payload = _json_payload(
        '\ufeff<think>先分析食物。</think>结果如下：\n'
        '```json\n{"items":[{"name":"薯片"}]}\n```\n以上为估算。'
    )

    assert payload["items"][0]["name"] == "薯片"


def test_json_payload_accepts_top_level_array_but_rejects_truncated_json():
    """HD-02: 多餐数组可提取，截断 JSON 不得被当作有效食物结果。"""
    payload = _json_payload('[{"meal_type":"snack","items":[{"name":"薯片"}]}]')
    assert payload["_array"][0]["items"][0]["name"] == "薯片"

    with pytest.raises(AIParseError, match="有效 JSON"):
        _json_payload('{"items":[{"name":"薯片"}')


@pytest.mark.asyncio
async def test_parse_meal_accepts_common_food_field_aliases(monkeypatch):
    """HD-03: 食物字段常见别名归一化后仍分别保留每个项目。"""
    async def fake_search(*args, **kwargs):
        return {
            "content": (
                '{"meal_type":"snack","foods":['
                '{"food":"薯片","quantity":"1包","calories":"约520 kcal",'
                '"protein":"6g","fat":"34g","carbs":"48g"},'
                '{"food_name":"无糖拿铁","calories_kcal":90,"carbs_g":8}'
                ']}'
            )
        }

    monkeypatch.setattr("plugins.HealthDiet.ai.AiUtil.search_deepseek", fake_search)
    meal = await parse_meal("下午吃了一包薯片和一杯无糖拿铁", date(2026, 9, 20))

    assert [item["name"] for item in meal["items"]] == ["薯片", "无糖拿铁"]
    assert meal["items"][0]["calories_kcal"] == 520
    assert meal["items"][0]["carbs_g"] == 48


@pytest.mark.asyncio
async def test_parse_meal_flattens_multiple_meal_groups(monkeypatch):
    """HD-04: AI 返回早餐、加餐、午餐数组时合并食物明细后入账。"""
    async def fake_search(*args, **kwargs):
        return {
            "content": (
                '[{"meal_type":"breakfast","items":[{"name":"鸡蛋","quantity":2,'
                '"calories_kcal":140}]},{"meal_type":"snack","items":['
                '{"name":"薯片","quantity":1,"calories_kcal":520}]}]'
            )
        }

    monkeypatch.setattr("plugins.HealthDiet.ai.AiUtil.search_deepseek", fake_search)
    meal = await parse_meal("吃了鸡蛋和薯片", date(2026, 9, 20))

    assert meal["meal_type"] == "other"
    assert [item["name"] for item in meal["items"]] == ["鸡蛋", "薯片"]
    assert [group["meal_type"] for group in meal["meals"]] == ["breakfast", "snack"]


@pytest.mark.asyncio
async def test_parse_meal_splits_multiple_meal_descriptions(monkeypatch):
    """HD-05: 一条消息包含多个餐次时分段请求并合并结果。"""
    calls = []

    async def fake_search(keyword, prompt, **kwargs):
        calls.append(keyword)
        name = "鸡蛋" if "早上" in keyword else "薯片"
        return {"content": f'{{"meal_type":"snack","items":[{{"name":"{name}","calories_kcal":100}}]}}'}

    monkeypatch.setattr("plugins.HealthDiet.ai.AiUtil.search_deepseek", fake_search)
    meal = await parse_meal("今天早上吃了鸡蛋，下午吃了薯片", date(2026, 9, 20))

    assert len(calls) == 2
    assert [item["name"] for item in meal["items"]] == ["鸡蛋", "薯片"]


@pytest.mark.asyncio
async def test_parse_meal_infers_type_when_ai_returns_other(monkeypatch):
    """HD-06: AI 返回默认 other 时，仍根据用户描述识别餐次。"""
    async def fake_search(*args, **kwargs):
        return {"content": '{"meal_type":"other","items":[{"name":"鸡蛋","calories_kcal":100}]}' }

    monkeypatch.setattr("plugins.HealthDiet.ai.AiUtil.search_deepseek", fake_search)
    meal = await parse_meal("早餐吃了一个鸡蛋", date(2026, 9, 20))

    assert meal["meal_type"] == "breakfast"
    assert meal["meals"][0]["meal_type"] == "breakfast"


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_parse_meal_retries_ai_when_first_result_is_empty(monkeypatch):
    calls = []

    async def fake_search(keyword, prompt, **kwargs):
        calls.append((keyword, prompt, kwargs))
        if len(calls) == 1:
            return {"content": '{"meal_type":"dinner","items":[]}' }
        return {
            "content": (
                '{"meal_type":"snack","items":['
                '{"name":"辣条","quantity":1,"unit":"包","grams":16,'
                '"calories_kcal":71.4,"protein_g":1.3,"fat_g":3.9,"carbs_g":7.7},'
                '{"name":"维生素C泡腾片","quantity":1,"unit":"颗","grams":0,'
                '"calories_kcal":0,"protein_g":0,"fat_g":0,"carbs_g":0}'
                ']}'
            )
        }

    monkeypatch.setattr("plugins.HealthDiet.ai.AiUtil.search_deepseek", fake_search)
    description = "晚上又加餐了一包辣条 16g 每100g 1867kj 8蛋白质 24.6脂肪 48.3碳水 然后加了一颗维生素c泡腾片"
    meal = await parse_meal(description, date(2026, 9, 22))

    assert len(calls) == 2
    assert "复核器" in calls[1][1]
    assert [item["name"] for item in meal["items"]] == ["辣条", "维生素C泡腾片"]


@pytest.mark.asyncio
async def test_shared_ai_uses_explicit_timeout_without_sdk_retry(monkeypatch):
    import importlib

    ai_util = importlib.import_module("plugins.common.utils.AiUtil")
    client_kwargs = {}
    completion_kwargs = {}

    class FakeCompletions:
        async def create(self, **kwargs):
            completion_kwargs.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
                usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            )

    class FakeClient:
        def __init__(self, **kwargs):
            client_kwargs.update(kwargs)
            self.chat = SimpleNamespace(completions=FakeCompletions())

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr(ai_util, "AsyncOpenAI", FakeClient)
    result = await ai_util.AiUtil.search_deepseek(
        "测试",
        "只返回 ok",
        request_timeout=61.0,
        retries=1,
    )

    assert result["content"] == "ok"
    assert client_kwargs["timeout"] == 61.0
    assert client_kwargs["max_retries"] == 0

    client_kwargs.clear()
    completion_kwargs.clear()
    await ai_util.AiUtil.search_deepseek("测试", "只返回 ok", retries=1)
    assert "max_tokens" not in completion_kwargs
    assert completion_kwargs["timeout"] is None
    assert client_kwargs["timeout"] is None


def test_calculate_energy_uses_weight_and_commute():
    result = calculate_energy({**_profile(), "target_kcal": 0})

    assert result["bmr_kcal"] == 1985
    assert result["tdee_kcal"] == 2502
    assert result["target_kcal"] == 2002
    assert result["commute_add_kcal"] == 120


def test_sum_items_and_macro_percentages():
    totals = sum_items([_item("鸡蛋", 140, 12, 10, 1), _item("包子", 280, 12, 10, 35)])

    assert totals == {"calories_kcal": 420.0, "protein_g": 24.0, "fat_g": 20.0, "carbs_g": 36.0}
    percentages = macro_percentages(totals)
    assert percentages["protein_pct"] == pytest.approx(22.9, abs=0.1)
    assert percentages["fat_pct"] == pytest.approx(42.9, abs=0.1)
    assert percentages["carbs_pct"] == pytest.approx(34.3, abs=0.1)


def test_weekly_summary_uses_recorded_days_only():
    rows = [
        {"local_date": "2026-09-18", "calories_kcal": 2000},
        {"local_date": "2026-09-19", "calories_kcal": 1000},
        {"local_date": "2026-09-20", "calories_kcal": 0},
    ]

    result = weekly_summary(rows, {"tdee_kcal": 2500})

    assert result["recorded_days"] == 2
    assert result["total_calories"] == 3000
    assert result["average_calories"] == 1500
    assert result["average_deficit"] == 1000


def test_store_persists_profile_and_separate_food_items(tmp_path: Path):
    db_path = tmp_path / "health.db"
    store = HealthStore(db_path)
    profile = _profile()
    meal_items = [_item("薯片", 520, 6, 34, 48), _item("无糖拿铁", 90, 6, 4, 8)]

    store.save_profile("u1", profile, "2026-09-20")
    meal_id = store.add_meal("u1", "2026-09-20", "snack", "下午吃了薯片和拿铁", meal_items)
    store.close()

    reopened = HealthStore(db_path)
    assert reopened.profile("u1")["target_kcal"] == 2000
    meal = reopened.meals_for_date("u1", "2026-09-20")[0]
    assert meal["id"] == meal_id
    assert [item["name"] for item in meal["items"]] == ["薯片", "无糖拿铁"]
    assert reopened.totals_for_date("u1", "2026-09-20")["calories_kcal"] == 610.0
    reopened.close()


def test_store_permanently_bans_clearly_abnormal_user(tmp_path: Path):
    store = HealthStore(tmp_path / "health.db")
    abnormal = {**_profile(), "height_cm": 130, "weight_kg": 210}
    store.save_profile("abnormal", abnormal, "2026-09-20")
    store.add_meal(
        "abnormal",
        "2026-09-20",
        "dinner",
        "吃了很多面包",
        [_item("面包", 50001, 100, 100, 100)],
    )

    reasons = store.audit_abnormal_users()

    assert "abnormal" in reasons
    assert store.is_banned("abnormal")
    assert not store.permanent_ban("abnormal", "second reason")
    store.close()


def test_store_detects_abnormal_text_and_reasonable_but_fake_ai_values(tmp_path: Path):
    store = HealthStore(tmp_path / "health.db")

    assert "铀" in (store.meal_abnormal_reason("五斤铀矿", [_item("铀矿", 0, 0, 0, 0)]) or "")
    assert "猪小仙" in (store.meal_abnormal_reason("烤猪小仙100只", [_item("烤猪小仙", 1500, 20, 10, 10)]) or "")
    assert "热量" in (store.meal_abnormal_reason("晚餐", [_item("未知食物", 10001, 10, 10, 10)]) or "")
    assert store.meal_abnormal_reason("早餐两个鸡蛋", [_item("鸡蛋", 140, 12, 10, 1)]) is None
    assert "BMI" in (store.profile_abnormal_reason({"height_cm": 130, "weight_kg": 210}) or "")
    assert store.profile_abnormal_reason({"height_cm": 180, "weight_kg": 100}) is None
    store.close()


def test_store_can_unban_member(tmp_path: Path):
    store = HealthStore(tmp_path / "health.db")
    assert store.permanent_ban("member", "管理员手动封禁")
    assert store.is_banned("member")
    assert store.unban("member")
    assert not store.is_banned("member")
    assert not store.unban("member")
    store.close()


@pytest.mark.asyncio
async def test_permanently_banned_user_is_blocked_before_ai(monkeypatch):
    notices = []

    class FakeStore:
        def is_banned(self, user_id):
            return True

    plugin = HealthDiet.__new__(HealthDiet)
    plugin.store = FakeStore()
    plugin._user_id = lambda event: str(event.sender.user_id)

    async def fake_notice(event, title, lines):
        notices.append((title, lines))

    async def fail_parse(*args, **kwargs):
        raise AssertionError("封禁用户不应调用 AI")

    plugin._notice = fake_notice
    monkeypatch.setattr(health_main, "parse_meal", fail_parse)
    event = SimpleNamespace(sender=SimpleNamespace(user_id="abnormal"), raw_message="健康记录 早餐吃了鸡蛋")

    await plugin.handle_message(event)

    assert notices and notices[0][0] == "健康功能已停用"


@pytest.mark.asyncio
async def test_health_toggle_commands_are_only_available_to_admin(monkeypatch):
    notices = []

    plugin = HealthDiet.__new__(HealthDiet)
    plugin.data = {"health_enabled": True}
    plugin._user_id = lambda event: str(event.sender.user_id)

    async def fake_notice(event, title, lines):
        notices.append((title, lines))

    plugin._notice = fake_notice
    admin_event = SimpleNamespace(sender=SimpleNamespace(user_id="273421673"), raw_message="健康禁用")
    await plugin.handle_message(admin_event)

    assert plugin.data["health_enabled"] is False
    assert notices[-1][0] == "健康功能已禁用"

    user_event = SimpleNamespace(sender=SimpleNamespace(user_id="other"), raw_message="健康解禁")
    await plugin.handle_message(user_event)

    assert plugin.data["health_enabled"] is False
    assert notices[-1][0] == "健康功能管理"

    admin_event.raw_message = "健康解禁"
    await plugin.handle_message(admin_event)

    assert plugin.data["health_enabled"] is True
    assert notices[-1][0] == "健康功能已启用"


@pytest.mark.asyncio
async def test_health_member_ban_supports_qq_and_at(monkeypatch):
    notices = []
    bans = []
    unbans = []

    class FakeStore:
        def permanent_ban(self, user_id, reason):
            bans.append((user_id, reason))
            return True

        def unban(self, user_id):
            unbans.append(user_id)
            return True

    plugin = HealthDiet.__new__(HealthDiet)
    plugin.store = FakeStore()
    plugin._user_id = lambda event: str(event.sender.user_id)

    async def fake_notice(event, title, lines):
        notices.append((title, lines))

    plugin._notice = fake_notice
    await plugin.handle_message(
        SimpleNamespace(
            sender=SimpleNamespace(user_id="273421673"),
            raw_message="健康封禁 12345678 违规数据",
            message=[],
        )
    )
    await plugin.handle_message(
        SimpleNamespace(
            sender=SimpleNamespace(user_id="273421673"),
            raw_message="健康封禁",
            message=[At(user_id="87654321")],
        )
    )
    await plugin.handle_message(
        SimpleNamespace(
            sender=SimpleNamespace(user_id="273421673"),
            raw_message="健康解禁 12345678",
            message=[],
        )
    )
    await plugin.handle_message(
        SimpleNamespace(
            sender=SimpleNamespace(user_id="273421673"),
            raw_message="健康解禁",
            message=[At(user_id="87654321")],
        )
    )

    assert bans == [("12345678", "违规数据"), ("87654321", "管理员手动封禁")]
    assert unbans == ["12345678", "87654321"]
    assert [notice[0] for notice in notices] == ["成员已封禁", "成员已封禁", "成员已解禁", "成员已解禁"]


def test_meal_date_parser_supports_yesterday(monkeypatch):
    plugin = HealthDiet.__new__(HealthDiet)
    plugin._timezone = UTC
    plugin._today = lambda: date(2026, 10, 21)
    plugin._now = lambda: datetime(2026, 10, 21, 12, 30, tzinfo=UTC)

    record_date, description, current_time = plugin._meal_date_and_description("昨天 早餐吃了鸡蛋")

    assert record_date == date(2026, 10, 20)
    assert description == "早餐吃了鸡蛋"
    assert current_time == datetime(2026, 10, 20, 12, 30, tzinfo=UTC)


@pytest.mark.asyncio
async def test_disabled_health_command_is_blocked_before_store_or_ai(monkeypatch):
    notices = []

    class FailStore:
        def is_banned(self, user_id):
            raise AssertionError("禁用状态不应访问封禁表")

    plugin = HealthDiet.__new__(HealthDiet)
    plugin.data = {"health_enabled": False}
    plugin.store = FailStore()
    plugin._user_id = lambda event: str(event.sender.user_id)

    async def fake_notice(event, title, lines):
        notices.append((title, lines))

    plugin._notice = fake_notice
    event = SimpleNamespace(sender=SimpleNamespace(user_id="user"), raw_message="健康记录 早餐吃了鸡蛋")
    await plugin.handle_message(event)

    assert notices and notices[0][0] == "健康功能已禁用"


@pytest.mark.asyncio
async def test_obvious_abnormal_description_is_rejected_before_ai(monkeypatch):
    calls = []
    notices = []

    class FakeStore:
        def is_banned(self, user_id):
            return False

        def meal_abnormal_reason(self, description, items):
            return "饮食描述包含明显异常内容：铀"

        def permanent_ban(self, user_id, reason):
            calls.append((user_id, reason))
            return True

    plugin = HealthDiet.__new__(HealthDiet)
    plugin.store = FakeStore()
    plugin._user_id = lambda event: str(event.sender.user_id)
    plugin._today = lambda: date(2026, 9, 21)

    async def fake_notice(event, title, lines):
        notices.append((title, lines))

    async def fail_parse(*args, **kwargs):
        raise AssertionError("明显异常原文不应调用 AI")

    plugin._notice = fake_notice
    monkeypatch.setattr(health_main, "parse_meal", fail_parse)
    event = SimpleNamespace(sender=SimpleNamespace(user_id="u-abnormal"), raw_message="健康记录 五斤铀矿")

    await plugin.handle_message(event)

    assert calls == [("u-abnormal", "饮食描述包含明显异常内容：铀")]
    assert notices and notices[0][0] == "健康功能已停用"


def test_group_meal_render_shows_actual_values_but_hides_target(monkeypatch, tmp_path: Path):
    captured = {}

    def fake_render_card(**kwargs):
        captured.update(kwargs)
        return kwargs["output_path"]

    monkeypatch.setattr("plugins.HealthDiet.render.render_card", fake_render_card)
    render_meal(
        nickname="测试用户",
        sex="男",
        meal={"meal_type": "snack", "items": [_item("薯片", 520, 6, 34, 48)], "totals": sum_items([_item("薯片", 520, 6, 34, 48)])},
        daily={"calories_kcal": 940, "protein_g": 30, "fat_g": 44, "carbs_g": 56},
        profile=_profile(),
        group=True,
        output_path=tmp_path / "meal.png",
    )

    text = "\n".join(captured["sections"][0][1] + captured["sections"][1][1] + captured["sections"][2][1])
    assert "薯片" in text and "520 kcal" in text and "蛋白质 6.0g" in text
    assert "940 / **** kcal" in text
    assert "2000" not in text


def test_group_assessment_and_dashboard_hide_goal_fields(monkeypatch, tmp_path: Path):
    captured = []

    def fake_render_card(**kwargs):
        captured.append(kwargs)
        return kwargs["output_path"]

    monkeypatch.setattr("plugins.HealthDiet.render.render_card", fake_render_card)
    daily = {"calories_kcal": 940, "protein_g": 30, "fat_g": 44, "carbs_g": 56}
    weekly = {"recorded_days": 2, "total_calories": 3000, "average_calories": 1500}
    render_assessment(
        nickname="测试用户",
        sex="男",
        local_date="2026-09-20",
        daily=daily,
        profile=_profile(),
        macro_lines=["蛋白质基本合适（30%）"],
        weekly=weekly,
        analysis_lines=["营养缺口：建议补充蔬菜和优质蛋白。"],
        group=True,
        output_path=tmp_path / "assessment.png",
    )
    render_dashboard(
        nickname="测试用户",
        profile=_profile(),
        daily=daily,
        weekly=weekly,
        foods=["9月20日：薯片、无糖拿铁"],
        macro_line="近 7 天实际平均比例：蛋白质 30% · 脂肪 40% · 碳水 30%",
        group=True,
        output_path=tmp_path / "dashboard.png",
    )

    for card in captured:
        text = str(card["stats"]) + "\n" + "\n".join(line for _, lines in card["sections"] for line in lines)
        assert "****" in text
        assert "2000" not in text
        assert "2502" not in text
    dashboard_text = "\n".join(line for _, lines in captured[1]["sections"] for line in lines)
    assert "9月20日：薯片、无糖拿铁" in dashboard_text
    assert "940" in dashboard_text and "3,000" in dashboard_text


@pytest.mark.asyncio
async def test_analyze_assessment_uses_one_text_ai_call(monkeypatch):
    calls = []

    async def fake_search(keyword, prompt, **kwargs):
        calls.append((keyword, prompt, kwargs))
        return {"content": "今日总体判断：蛋白质偏低。\n今天剩余时间：补充鸡胸肉和蔬菜。"}

    monkeypatch.setattr("plugins.HealthDiet.ai.AiUtil.search_deepseek", fake_search)
    result = await analyze_assessment(
        local_date="2026-09-20",
        daily={"calories_kcal": 940, "protein_g": 30, "fat_g": 44, "carbs_g": 56},
        macro_lines=["蛋白质偏低（12%）"],
        weekly={"recorded_days": 2, "total_calories": 3000, "average_calories": 1500},
        foods=["早餐：水煮蛋 2个（140kcal）"],
        profile=_profile(),
        group=False,
    )

    assert "鸡胸肉" in result
    assert len(calls) == 1
    assert "目标摄入=2000" in calls[0][0]
    assert calls[0][2]["max_tokens"] is None
    assert calls[0][2]["request_timeout"] is None


@pytest.mark.asyncio
async def test_group_analysis_prompt_does_not_include_private_profile(monkeypatch):
    calls = []

    async def fake_search(keyword, prompt, **kwargs):
        calls.append((keyword, prompt))
        return {"content": "营养建议：补充蔬菜和优质蛋白。"}

    monkeypatch.setattr("plugins.HealthDiet.ai.AiUtil.search_deepseek", fake_search)
    await analyze_assessment(
        local_date="2026-09-20",
        daily={"calories_kcal": 940, "protein_g": 30, "fat_g": 44, "carbs_g": 56},
        macro_lines=["蛋白质偏低（12%）"],
        weekly={"recorded_days": 2, "total_calories": 3000, "average_calories": 1500},
        foods=["早餐：水煮蛋 2个（140kcal）"],
        profile=_profile(),
        group=True,
    )

    assert len(calls) == 1
    assert "群聊模式" in calls[0][0]
    assert "2000" not in calls[0][0]
    assert "身高=180" not in calls[0][0]


def test_render_card_outputs_png(tmp_path: Path):
    output = tmp_path / "notice.png"
    from plugins.HealthDiet.render import render_notice

    render_notice("测试", ["图片输出"], output)

    with PillowImage.open(output) as image:
        assert image.format == "PNG"
        assert image.width == 1240
        assert image.height > 400


@pytest.mark.asyncio
async def test_dashboard_macro_line_uses_seven_day_rows(monkeypatch, tmp_path: Path):
    captured = {}
    today = "2026-09-20"
    rows = [
        {"local_date": f"2026-09-{day:02d}", "calories_kcal": 0, "protein_g": 0, "fat_g": 0, "carbs_g": 0}
        for day in range(14, 21)
    ]
    rows[-2] = {"local_date": "2026-09-19", "calories_kcal": 1000, "protein_g": 100, "fat_g": 10, "carbs_g": 200}
    rows[-1] = {"local_date": today, "calories_kcal": 940, "protein_g": 30, "fat_g": 44, "carbs_g": 56}

    class FakeStore:
        def profile(self, user_id):
            return _profile()

        def totals_for_date(self, user_id, local_date):
            return rows[-1]

        def daily_rows(self, user_id, dates):
            return rows

        def meals_for_date(self, user_id, local_date):
            if local_date == "2026-09-19":
                return [{"meal_type": "other", "raw_text": "下午吃了两包薯片", "items": [{"name": "薯片", "quantity": 1, "unit": "包"}, {"name": "薯片", "quantity": 1, "unit": "包"}]}]
            if local_date == today:
                return [
                    {"meal_type": "breakfast", "items": [{"name": "水煮蛋", "quantity": 2, "unit": "个"}]},
                    {"meal_type": "snack", "items": [{"name": "无糖拿铁", "quantity": 1, "unit": "杯"}]},
                ]
            return []

    def fake_render_dashboard(**kwargs):
        captured.update(kwargs)
        return tmp_path / "dashboard.png"

    plugin = HealthDiet.__new__(HealthDiet)
    plugin.store = FakeStore()
    plugin._today = lambda: date.fromisoformat(today)
    plugin._nickname = lambda event: "测试用户"
    plugin._font_path = lambda: ""
    plugin._image_path = lambda prefix: tmp_path / f"{prefix}.png"
    async def fake_send_image(event, path):
        return None

    plugin._send_image = fake_send_image
    monkeypatch.setattr(health_main, "render_dashboard", fake_render_dashboard)

    event = SimpleNamespace(sender=SimpleNamespace(user_id="u1", card="测试用户"), group_id="g1")
    await plugin._handle_dashboard(event)

    assert "蛋白质 25.6%" in captured["macro_line"]
    assert "蛋白质 16.2%" not in captured["macro_line"]
    assert captured["foods"] == [
        "9月19日",
        "  零食/加餐：薯片 1包、薯片 1包",
        "9月20日",
        "  早餐：水煮蛋 2个",
        "  零食/加餐：无糖拿铁 1杯",
    ]
