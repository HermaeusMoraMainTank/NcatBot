from __future__ import annotations

import asyncio
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ncatbot.core import registrar
from ncatbot.plugin import NcatBotPlugin
from ncatbot.types import At, Image, MessageArray
from ncatbot.utils import get_log
from common.utils.CommonUtil import CommonUtil

from .ai import AIParseError, analyze_assessment, parse_meal, parse_profile
from .calculations import (
    calculate_energy,
    current_intake_assessment,
    date_range,
    macro_assessment,
    macro_percentages,
    sum_items,
    weekly_summary,
)
from .render import (
    render_assessment,
    render_dashboard,
    render_help,
    render_meal,
    render_notice,
    render_profile,
)
from .store import HealthStore

LOG = get_log("HealthDiet")
HEALTH_ADMIN_ID = "273421673"
HEALTH_ENABLED_KEY = "health_enabled"

_MEAL_LABELS = {
    "breakfast": "早餐",
    "lunch": "午餐",
    "dinner": "晚餐",
    "snack": "零食/加餐",
    "other": "其他",
}
_MEAL_ORDER = ("breakfast", "lunch", "dinner", "snack", "other")


def _legacy_meal_type(meal: dict[str, Any]) -> str:
    meal_type = str(meal.get("meal_type") or "other")
    if meal_type != "other":
        return meal_type
    text = str(meal.get("raw_text") or "")
    matches = [
        meal_type
        for meal_type, words in (
            ("breakfast", ("早餐", "早饭", "早上")),
            ("lunch", ("午餐", "午饭", "中午")),
            ("dinner", ("晚餐", "晚饭", "晚上")),
            ("snack", ("零食", "加餐", "下午茶", "下午", "夜宵", "中间")),
        )
        if any(word in text for word in words)
    ]
    return matches[0] if len(matches) == 1 else meal_type


class HealthDiet(NcatBotPlugin):
    name = "HealthDiet"
    version = "1.0.0"
    description = "AI 健康饮食记录、热量评估和图片面板"

    async def on_load(self) -> None:
        self.init_defaults(
            {
                "timezone": "Asia/Shanghai",
                "deficit_kcal": 500,
                "font_path": "",
            }
        )
        self.data.setdefault(HEALTH_ENABLED_KEY, True)
        self.image_dir = Path(self.workspace) / "images"
        self.image_dir.mkdir(parents=True, exist_ok=True)
        CommonUtil.cleanup_old_files(self.image_dir, max_age_seconds=3600)
        CommonUtil.cleanup_backup_files([self.workspace], keep_seconds=86400)
        self.add_scheduled_task(
            "healthdiet_temp_cleanup",
            "1h",
            callback=lambda: CommonUtil.cleanup_old_files(
                self.image_dir, max_age_seconds=3600
            ),
        )
        self.store = HealthStore(Path(self.workspace) / "health_diet.db")
        abnormal_users = self.store.audit_abnormal_users()
        if abnormal_users:
            LOG.warning("健康功能已永久封禁异常用户：%s", ", ".join(sorted(abnormal_users)))
        self._pending_profiles: dict[str, dict[str, Any]] = {}
        try:
            self._timezone = ZoneInfo(str(self.get_config("timezone", "Asia/Shanghai")))
        except (KeyError, ValueError):
            self._timezone = ZoneInfo("UTC")

    async def on_close(self) -> None:
        if getattr(self, "store", None) is not None:
            self.store.close()

    def _now(self) -> datetime:
        return datetime.now(self._timezone)

    def _today(self) -> date:
        return self._now().date()

    def _font_path(self) -> str:
        return str(self.get_config("font_path", "") or "").strip()

    def _user_id(self, event: Any) -> str:
        return str(event.sender.user_id)

    def _nickname(self, event: Any) -> str:
        sender = event.sender
        return str(getattr(sender, "card", None) or getattr(sender, "nickname", None) or self._user_id(event))

    @staticmethod
    def _is_group(event: Any) -> bool:
        return getattr(event, "group_id", None) is not None

    def _image_path(self, prefix: str) -> Path:
        return self.image_dir / f"{prefix}_{time.time_ns()}.png"

    async def _send_image(self, event: Any, path: Path) -> None:
        try:
            await event.reply(
                rtf=MessageArray([Image(file=str(path))]),
                at_sender=False,
            )
        finally:
            CommonUtil.cleanup_paths([path])

    async def _notice(self, event: Any, title: str, lines: list[str]) -> None:
        path = await asyncio.to_thread(
            render_notice,
            title,
            lines,
            self._image_path("notice"),
            self._font_path(),
        )
        await self._send_image(event, path)

    async def _help(self, event: Any) -> None:
        path = await asyncio.to_thread(render_help, self._image_path("help"), self._font_path())
        await self._send_image(event, path)

    def _health_enabled(self) -> bool:
        return bool(getattr(self, "data", {}).get(HEALTH_ENABLED_KEY, True))

    def _set_health_enabled(self, enabled: bool) -> None:
        self.data[HEALTH_ENABLED_KEY] = enabled
        save_data = getattr(self, "_save_data", None)
        if save_data is not None:
            save_data()

    async def _handle_admin_toggle(self, event: Any, enabled: bool) -> None:
        if self._user_id(event) != HEALTH_ADMIN_ID:
            await self._notice(event, "健康功能管理", ["你没有权限执行此指令。"])
            return
        self._set_health_enabled(enabled)
        title = "健康功能已启用" if enabled else "健康功能已禁用"
        await self._notice(event, title, ["只有管理员可以修改健康功能状态。"])

    def _member_target_and_tail(self, event: Any, text: str, prefix: str) -> tuple[str | None, str]:
        rest = text[len(prefix):].strip()
        at_ids: list[str] = []
        for segment in getattr(event, "message", None) or []:
            if isinstance(segment, At):
                at_ids.append(str(segment.user_id))
            elif isinstance(segment, dict) and segment.get("type") == "at":
                user_id = (segment.get("data") or {}).get("qq")
                if user_id is not None:
                    at_ids.append(str(user_id))
        at_ids.extend(re.findall(r"\[CQ:at,qq=(\d+)\]", rest))
        rest = re.sub(r"\[CQ:at,qq=\d+\]", "", rest).strip()
        if at_ids:
            return at_ids[0], rest
        match = re.fullmatch(r"(\d+)(?:\s+(.+))?", rest)
        if not match:
            return None, rest
        return match.group(1), (match.group(2) or "").strip()

    async def _handle_member_ban(self, event: Any, target_id: str, reason: str) -> None:
        if self._user_id(event) != HEALTH_ADMIN_ID:
            await self._notice(event, "健康成员管理", ["你没有权限执行此指令。"])
            return
        reason = reason or "管理员手动封禁"
        changed = self.store.permanent_ban(target_id, reason)
        if changed:
            await self._notice(event, "成员已封禁", [f"QQ：{target_id}", f"原因：{reason}"])
        else:
            await self._notice(event, "成员已处于封禁状态", [f"QQ：{target_id}"])

    async def _handle_member_unban(self, event: Any, target_id: str) -> None:
        if self._user_id(event) != HEALTH_ADMIN_ID:
            await self._notice(event, "健康成员管理", ["你没有权限执行此指令。"])
            return
        changed = self.store.unban(target_id)
        title = "成员已解禁" if changed else "成员未处于封禁状态"
        await self._notice(event, title, [f"QQ：{target_id}"])

    async def _handle_member_command(self, event: Any, text: str) -> bool:
        has_at_target = any(
            isinstance(segment, At)
            or (isinstance(segment, dict) and segment.get("type") == "at")
            for segment in getattr(event, "message", None) or []
        )
        if text == "健康解禁" and not has_at_target:
            return False
        if not (text == "健康封禁" or text.startswith(("健康封禁", "健康解禁"))):
            return False
        prefix = "健康封禁" if text.startswith("健康封禁") else "健康解禁"
        target_id, tail = self._member_target_and_tail(event, text, prefix)
        if not target_id:
            usage = "健康封禁 QQ号 [原因]，也可以直接艾特成员。" if prefix == "健康封禁" else "健康解禁 QQ号，也可以直接艾特成员。"
            await self._notice(event, "健康成员管理", [f"用法：{usage}"])
            return True
        if prefix == "健康解禁" and tail:
            await self._notice(event, "健康成员管理", ["用法：健康解禁 QQ号，也可以直接艾特成员。"])
            return True
        if prefix == "健康封禁":
            await self._handle_member_ban(event, target_id, tail)
        else:
            await self._handle_member_unban(event, target_id)
        return True

    def _meal_date_and_description(self, description: str) -> tuple[date, str, datetime | None]:
        value = description.strip()
        today = self._today()
        current_time = self._now() if hasattr(self, "_timezone") else None
        for prefix, offset in (("昨天", -1), ("今天", 0)):
            if value == prefix:
                return today + timedelta(days=offset), "", current_time and current_time + timedelta(days=offset)
            if value.startswith(prefix) and value[len(prefix):].startswith((" ", "　")):
                return (
                    today + timedelta(days=offset),
                    value[len(prefix):].strip(),
                    current_time and current_time + timedelta(days=offset),
                )
        return today, value, current_time

    async def _handle_profile(self, event: Any, description: str) -> None:
        if self._is_group(event):
            await self._notice(event, "健康档案", ["健康档案包含隐私信息，请私聊机器人设置或修改。", "群聊不会接收或复述身高、体重、年龄等档案原文。"])
            return
        if not description:
            await self._notice(event, "健康档案", ["请补充身高、体重、年龄、性别和日常活动。", "示例：健康档案 我身高180厘米，体重200斤，男，29岁，是 Java 程序员，每天久坐。"])
            return
        try:
            profile, missing = await parse_profile(description)
            if missing:
                await self._notice(event, "健康档案信息不完整", [f"还缺少：{'、'.join(missing)}。", "请在私聊中补充完整后重新发送。"])
                return
            reason = self.store.profile_abnormal_reason(profile)
            if reason:
                self.store.permanent_ban(self._user_id(event), reason)
                LOG.warning("健康档案异常，永久封禁用户 %s：%s", self._user_id(event), reason)
                await self._notice(event, "健康功能已停用", ["检测到明显异常的健康档案，该用户已被永久禁止使用健康功能。", "本次档案没有保存。"])
                return
            energy = calculate_energy(profile, float(self.get_config("deficit_kcal", 500) or 500))
            profile.update(energy)
            profile["display_name"] = profile.get("display_name") or self._nickname(event)
            self._pending_profiles[self._user_id(event)] = profile
            path = await asyncio.to_thread(
                render_profile,
                profile,
                self._nickname(event),
                self._image_path("profile_pending"),
                self._font_path(),
                True,
            )
            await self._send_image(event, path)
        except (AIParseError, ValueError) as exc:
            await self._notice(event, "档案解析失败", [str(exc), "请稍后重试，或把身高、体重、年龄和性别写得更明确。"])
        except Exception:
            LOG.exception("处理健康档案失败")
            await self._notice(event, "暂时无法处理", ["健康档案处理失败，请稍后重试。"])

    async def _confirm_profile(self, event: Any) -> None:
        uid = self._user_id(event)
        if self._is_group(event):
            await self._notice(event, "健康档案", ["档案只能在私聊中确认和保存。"])
            return
        profile = self._pending_profiles.get(uid)
        if not profile:
            await self._notice(event, "健康档案", ["没有等待确认的档案，请先发送健康档案描述。"])
            return
        self.store.save_profile(uid, profile, self._today().isoformat())
        self._pending_profiles.pop(uid, None)
        path = await asyncio.to_thread(
            render_profile,
            profile,
            self._nickname(event),
            self._image_path("profile_saved"),
            self._font_path(),
            False,
        )
        await self._send_image(event, path)

    async def _handle_meal(self, event: Any, description: str) -> None:
        record_date, description, current_time = self._meal_date_and_description(description)
        if not description:
            await self._notice(event, "健康记录", ["请补充吃了什么。", "示例：健康记录 早餐吃了两个水煮蛋和一个牛肉包子。", "记录昨天：健康记录 昨天 早餐吃了两个水煮蛋。"])
            return
        uid = self._user_id(event)
        reason = self.store.meal_abnormal_reason(description, [])
        if reason:
            self.store.permanent_ban(uid, reason)
            LOG.warning("饮食原文预检拦截，永久封禁用户 %s：%s", uid, reason)
            await self._notice(event, "健康功能已停用", ["检测到明显异常的饮食数据，该用户已被永久禁止使用健康功能。", "本次记录没有写入数据库。"])
            return
        try:
            parsed = await parse_meal(description, record_date, current_time=current_time)
            reason = self.store.meal_abnormal_reason(description, parsed["items"])
            if reason:
                self.store.permanent_ban(uid, reason)
                LOG.warning("AI 饮食结果校验拦截，永久封禁用户 %s：%s", uid, reason)
                await self._notice(event, "健康功能已停用", ["检测到明显异常的饮食数据，该用户已被永久禁止使用健康功能。", "本次记录没有写入数据库。"])
                return
            for meal in parsed.get("meals") or [parsed]:
                self.store.add_meal(uid, record_date.isoformat(), meal["meal_type"], description, meal["items"])
            daily = self.store.totals_for_date(uid, record_date.isoformat())
            profile = self.store.profile(uid)
            path = await asyncio.to_thread(
                render_meal,
                nickname=self._nickname(event),
                sex=str((profile or {}).get("sex") or ""),
                meal={**parsed, "totals": self._sum_items(parsed["items"])},
                daily=daily,
                profile=profile,
                group=self._is_group(event),
                daily_label="今日累计" if record_date == self._today() else f"{record_date.isoformat()}累计",
                output_path=self._image_path("meal"),
                font_path=self._font_path(),
            )
            await self._send_image(event, path)
        except AIParseError as exc:
            LOG.warning("饮食 AI 解析失败，未写入数据库：用户 %s，原因：%s", uid, exc)
            await self._notice(event, "饮食没有入账", [str(exc), "请补充食物名称、数量、重量或规格后重试。"])
        except Exception:
            LOG.exception("处理饮食记录失败")
            await self._notice(event, "饮食没有入账", ["本次记录没有写入，请稍后重试。"])

    @staticmethod
    def _sum_items(items: list[dict[str, Any]]) -> dict[str, float]:
        return {
            key: round(sum(float(item.get(key) or 0) for item in items), 1)
            for key in ("calories_kcal", "protein_g", "fat_g", "carbs_g")
        }

    def _requested_date(self, suffix: str) -> date | None:
        suffix = suffix.strip()
        if not suffix or suffix == "今天":
            return self._today()
        if suffix == "昨天":
            return self._today() - timedelta(days=1)
        try:
            return date.fromisoformat(suffix)
        except ValueError:
            return None

    async def _handle_assessment(self, event: Any, suffix: str) -> None:
        target_date = self._requested_date(suffix)
        if target_date is None:
            await self._notice(event, "饮食评估", ["日期格式不正确。", "可使用：健康评估、健康评估 昨天，或健康评估 YYYY-MM-DD。"])
            return
        uid = self._user_id(event)
        profile = self.store.profile(uid)
        daily = self.store.totals_for_date(uid, target_date.isoformat())
        rows = self.store.daily_rows(uid, date_range(7, target_date))
        weekly = weekly_summary(rows, profile)
        is_today = target_date == self._today()
        current_progress = None
        if is_today and profile and profile.get("target_kcal"):
            current_progress = current_intake_assessment(
                daily.get("calories_kcal", 0),
                profile.get("target_kcal", 0),
                self._now(),
            )
            macro_lines = macro_assessment(daily)
            if not self._is_group(event):
                macro_lines.append(
                    f"当前阶段：{current_progress['stage_label']}，阶段参考摄入约 {current_progress['stage_target_kcal']:g} kcal；{current_progress['status']}"
                )
        else:
            macro_lines = macro_assessment(daily)
        food_lines = []
        for meal in self.store.meals_for_date(uid, target_date.isoformat()):
            meal_type = _legacy_meal_type(meal)
            item_lines = []
            for item in meal["items"]:
                quantity = item.get("quantity")
                unit = str(item.get("unit") or "").strip()
                if quantity is None or not unit:
                    quantity_text = ""
                else:
                    try:
                        quantity_text = f" {float(quantity):g}{unit}"
                    except (TypeError, ValueError):
                        quantity_text = f" {quantity}{unit}"
                item_lines.append(
                    f"{item.get('name') or '未命名食物'}{quantity_text}"
                    f"（{float(item.get('calories_kcal') or 0):g}kcal，蛋白质{float(item.get('protein_g') or 0):g}g，"
                    f"脂肪{float(item.get('fat_g') or 0):g}g，碳水{float(item.get('carbs_g') or 0):g}g）"
                )
            if item_lines:
                food_lines.append(f"{_MEAL_LABELS.get(meal_type, meal_type)}：{'、'.join(item_lines)}")
        try:
            analysis = await analyze_assessment(
                local_date=target_date.isoformat(),
                daily=daily,
                macro_lines=macro_lines,
                weekly=weekly,
                foods=food_lines,
                profile=profile,
                group=self._is_group(event),
                current_progress=current_progress,
            )
            analysis_lines = [line.strip() for line in analysis.splitlines() if line.strip()]
        except AIParseError as exc:
            LOG.warning("健康评估 AI 分析不可用：%s", exc)
            analysis_lines = ["AI 详细分析暂时不可用，以上数字评估仍然有效。"]
        path = await asyncio.to_thread(
            render_assessment,
            nickname=self._nickname(event),
            sex=str((profile or {}).get("sex") or ""),
            local_date=target_date.isoformat(),
            daily=daily,
            profile=profile,
            macro_lines=macro_lines,
            analysis_lines=analysis_lines,
            weekly=weekly,
            group=self._is_group(event),
            current_progress=current_progress,
            output_path=self._image_path("assessment"),
            font_path=self._font_path(),
        )
        await self._send_image(event, path)

    async def _handle_dashboard(self, event: Any) -> None:
        uid = self._user_id(event)
        today = self._today()
        profile = self.store.profile(uid)
        daily = self.store.totals_for_date(uid, today.isoformat())
        dates = date_range(7, today)
        rows = self.store.daily_rows(uid, dates)
        weekly = weekly_summary(rows, profile)
        foods: list[str] = []
        for local_date in dates:
            meals_by_type: dict[str, list[str]] = {}
            for meal in self.store.meals_for_date(uid, local_date):
                meal_type = _legacy_meal_type(meal)
                meal_foods = meals_by_type.setdefault(meal_type, [])
                for item in meal["items"]:
                    name = str(item.get("name") or "")
                    if not name:
                        continue
                    quantity = item.get("quantity")
                    unit = str(item.get("unit") or "").strip()
                    if quantity is None or not unit:
                        meal_foods.append(name)
                    else:
                        try:
                            quantity_text = f"{float(quantity):g}"
                        except (TypeError, ValueError):
                            quantity_text = str(quantity)
                        meal_foods.append(f"{name} {quantity_text}{unit}")
            if meals_by_type:
                month, day = local_date[5:].split("-")
                foods.append(f"{int(month)}月{int(day)}日")
                ordered_types = [meal_type for meal_type in _MEAL_ORDER if meal_type in meals_by_type]
                ordered_types.extend(meal_type for meal_type in meals_by_type if meal_type not in _MEAL_ORDER)
                for meal_type in ordered_types:
                    foods.append(f"  {_MEAL_LABELS.get(meal_type, meal_type)}：{'、'.join(meals_by_type[meal_type])}")
        weekly_totals = sum_items(rows)
        percentages = macro_percentages(weekly_totals)
        macro_line = f"近 7 天实际平均比例：蛋白质 {percentages['protein_pct']:g}% · 脂肪 {percentages['fat_pct']:g}% · 碳水 {percentages['carbs_pct']:g}%"
        path = await asyncio.to_thread(
            render_dashboard,
            nickname=self._nickname(event),
            profile=profile,
            daily=daily,
            weekly=weekly,
            foods=foods,
            macro_line=macro_line,
            group=self._is_group(event),
            output_path=self._image_path("dashboard"),
            font_path=self._font_path(),
        )
        await self._send_image(event, path)

    async def _handle_undo(self, event: Any) -> None:
        meal = self.store.last_meal(self._user_id(event))
        if not meal:
            await self._notice(event, "健康撤销", ["暂无可以撤销的饮食记录。"])
            return
        self.store.delete_meal(self._user_id(event), int(meal["id"]))
        totals = self.store.totals_for_date(self._user_id(event), str(meal["local_date"]))
        await self._notice(event, "最近记录已撤销", [f"已撤销：{', '.join(str(item['name']) for item in meal['items'])}", f"{meal['local_date']} 剩余实际摄入：{totals['calories_kcal']:g} kcal。"])

    async def _handle_edit(self, event: Any, description: str) -> None:
        if not description:
            await self._notice(event, "健康修改", ["请提供新的饮食描述，例如：健康修改 牛肉包子改为大号。"])
            return
        old = self.store.last_meal(self._user_id(event))
        if not old:
            await self._notice(event, "健康修改", ["暂无可以修改的饮食记录。"])
            return
        uid = self._user_id(event)
        reason = self.store.meal_abnormal_reason(description, [])
        if reason:
            self.store.permanent_ban(uid, reason)
            LOG.warning("饮食修改原文预检拦截，永久封禁用户 %s：%s", uid, reason)
            await self._notice(event, "健康功能已停用", ["检测到明显异常的饮食数据，该用户已被永久禁止使用健康功能。", "原记录未修改。"])
            return
        try:
            parsed = await parse_meal(description, date.fromisoformat(old["local_date"]), current_time=self._now())
            reason = self.store.meal_abnormal_reason(description, parsed["items"])
            if reason:
                self.store.permanent_ban(uid, reason)
                LOG.warning("AI 修改结果校验拦截，永久封禁用户 %s：%s", uid, reason)
                await self._notice(event, "健康功能已停用", ["检测到明显异常的饮食数据，该用户已被永久禁止使用健康功能。", "原记录未修改。"])
                return
            self.store.delete_meal(self._user_id(event), int(old["id"]))
            for meal in parsed.get("meals") or [parsed]:
                self.store.add_meal(self._user_id(event), old["local_date"], meal["meal_type"], description, meal["items"])
            daily = self.store.totals_for_date(self._user_id(event), old["local_date"])
            profile = self.store.profile(self._user_id(event))
            path = await asyncio.to_thread(
                render_meal,
                nickname=self._nickname(event),
                sex=str((profile or {}).get("sex") or ""),
                meal={**parsed, "totals": self._sum_items(parsed["items"])},
                daily=daily,
                profile=profile,
                group=self._is_group(event),
                output_path=self._image_path("meal_edit"),
                font_path=self._font_path(),
            )
            await self._send_image(event, path)
        except AIParseError as exc:
            await self._notice(event, "健康修改失败", [str(exc), "原记录未修改。"])
        except Exception:
            LOG.exception("修改饮食记录失败")
            await self._notice(event, "健康修改失败", ["原记录未修改，请稍后重试。"])

    @registrar.qq.on_message(priority=20)
    async def handle_message(self, event: Any) -> None:
        text = str(getattr(event, "raw_message", "") or "").strip()
        uid = self._user_id(event)
        if await self._handle_member_command(event, text):
            return
        is_health_command = text in {"健康", "健康帮助", "确认档案", "重新填写", "健康面板", "健康撤销"} or text.startswith(
            ("健康档案", "健康记录", "健康评估", "健康修改")
        )
        if text == "健康禁用":
            await self._handle_admin_toggle(event, False)
            return
        if text == "健康解禁":
            await self._handle_admin_toggle(event, True)
            return
        if is_health_command and not self._health_enabled():
            await self._notice(event, "健康功能已禁用", ["健康功能当前由管理员暂停使用，请稍后再试。"])
            return
        if is_health_command and self.store.is_banned(uid):
            await self._notice(event, "健康功能已停用", ["检测到明显异常的健康数据，该用户已被永久禁止使用健康功能。", "如认为是误判，请联系管理员处理。"])
            return
        if text == "确认档案" and uid in self._pending_profiles:
            await self._confirm_profile(event)
            return
        if text == "重新填写" and uid in self._pending_profiles:
            self._pending_profiles.pop(uid, None)
            await self._notice(event, "健康档案", ["本次档案填写已取消。"])
            return
        if text in {"健康", "健康帮助"}:
            await self._help(event)
            return
        if text.startswith("健康档案"):
            await self._handle_profile(event, text[len("健康档案"):].strip())
            return
        if text.startswith("健康记录"):
            await self._handle_meal(event, text[len("健康记录"):].strip())
            return
        if text.startswith("健康评估"):
            await self._handle_assessment(event, text[len("健康评估"):].strip())
            return
        if text == "健康面板":
            await self._handle_dashboard(event)
            return
        if text == "健康撤销":
            await self._handle_undo(event)
            return
        if text.startswith("健康修改"):
            await self._handle_edit(event, text[len("健康修改"):].strip())
