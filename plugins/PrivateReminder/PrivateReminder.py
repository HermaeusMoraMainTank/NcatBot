from __future__ import annotations

import asyncio
import re
import time
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from ncatbot.core import registrar
from ncatbot.plugin import NcatBotPlugin
from ncatbot.utils import get_log

from .render import render_help, render_list, render_notice, render_reminder

LOG = get_log("PrivateReminder")

_RELATIVE_RE = re.compile(r"^(\d+)\s*(秒|分钟|分|小时|时|天)\s*(?:后)?\s*(.*)$")
_HALF_HOUR_RE = re.compile(r"^半\s*小时\s*(?:后)?\s*(.*)$")
_DAY_WORD_RE = re.compile(r"^(今天|明天|后天)\s*")
_FULL_DATE_RE = re.compile(
    r"^(\d{4})\s*(?:年|[-/])\s*(\d{1,2})\s*(?:月|[-/])\s*(\d{1,2})\s*(?:日|号)?\s*"
)
_MONTH_DAY_RE = re.compile(
    r"^(\d{1,2})\s*(?:月|[-/])\s*(\d{1,2})\s*(?:日|号)?\s*"
)
_CLOCK_TIME_RE = re.compile(r"^(\d{1,2})\s*[:：]\s*(\d{2})")
_POINT_TIME_RE = re.compile(r"^(\d{1,2})\s*[点时]\s*(?:(\d{1,2})\s*分?)?")

_RELATIVE_SECONDS = {"秒": 1, "分钟": 60, "分": 60, "小时": 3600, "时": 3600, "天": 86400}
_DAY_OFFSETS = {"今天": 0, "明天": 1, "后天": 2}


def _consume_time(text: str) -> tuple[int, int, str] | None:
    match = _CLOCK_TIME_RE.match(text)
    if match:
        return int(match.group(1)), int(match.group(2)), text[match.end():]
    match = _POINT_TIME_RE.match(text)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        rest = text[match.end():]
        if minute == 0 and rest.startswith("半"):
            minute = 30
            rest = rest[1:]
        return hour, minute, rest
    return None


def _content_of(text: str) -> str:
    content = text.strip()
    content = re.sub(r"^[\s,，.。;；:：\-—、]+", "", content)
    if content == "整":
        return "时间到了"
    return content or "时间到了"


def parse_reminder_time(raw: str, now: datetime) -> tuple[datetime, str]:
    """解析预约时间；日期与时间之间、数字与单位之间都可以没有空格。"""
    raw = unicodedata.normalize("NFKC", str(raw or "")).strip()
    if not raw:
        raise ValueError("请在时间后写提醒内容，例如：预约 明天9点 收鱼")

    def build(year: int, month: int, day: int, hour: int, minute: int, content: str | None) -> tuple[datetime, str]:
        try:
            due = datetime(year, month, day, hour, minute, tzinfo=now.tzinfo)
        except ValueError as exc:
            raise ValueError("时间无效，请检查日期和时分") from exc
        return due, _content_of(content or "")

    match = _RELATIVE_RE.fullmatch(raw)
    if match:
        amount = int(match.group(1))
        unit = match.group(2)
        content = _content_of(match.group(3))
        if amount <= 0:
            raise ValueError("提醒时间必须大于 0")
        due = now + timedelta(seconds=amount * _RELATIVE_SECONDS[unit])
        if (due - now).total_seconds() < 10:
            raise ValueError("提醒时间至少要在 10 秒后")
        return due, content

    match = _HALF_HOUR_RE.fullmatch(raw)
    if match:
        return now + timedelta(minutes=30), _content_of(match.group(1))

    match = _DAY_WORD_RE.match(raw)
    if match:
        time_info = _consume_time(raw[match.end():])
        if time_info is not None:
            hour, minute, rest = time_info
            day_word = match.group(1)
            target = now.date() + timedelta(days=_DAY_OFFSETS[day_word])
            due, content = build(
                target.year, target.month, target.day, hour, minute, rest
            )
            if due <= now:
                raise ValueError(f"{day_word}这个时间已经过去")
            return due, content

    match = _FULL_DATE_RE.match(raw)
    if match:
        time_info = _consume_time(raw[match.end():])
        if time_info is not None:
            year, month, day = (int(value) for value in match.groups())
            hour, minute, rest = time_info
            due, content = build(year, month, day, hour, minute, rest)
            if due <= now:
                raise ValueError("这个时间已经过去")
            return due, content

    match = _MONTH_DAY_RE.match(raw)
    if match:
        time_info = _consume_time(raw[match.end():])
        if time_info is not None:
            month, day = (int(value) for value in match.groups())
            hour, minute, rest = time_info
            due, content = build(now.year, month, day, hour, minute, rest)
            if due <= now:
                due, content = build(now.year + 1, month, day, hour, minute, rest)
            return due, content

    time_info = _consume_time(raw)
    if time_info is not None:
        hour, minute, rest = time_info
        due, content = build(now.year, now.month, now.day, hour, minute, rest)
        if due <= now:
            target = now.date() + timedelta(days=1)
            due, content = build(
                target.year, target.month, target.day, hour, minute, rest
            )
        return due, content

    raise ValueError("时间格式无法识别，例如：预约 明天9点、预约 11月5日 9点30分")


def format_remaining(due: datetime, now: datetime) -> str:
    seconds = max(0, int((due - now).total_seconds()))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if days:
        return f"{days}天{hours}小时" if hours else f"{days}天"
    if hours:
        return f"{hours}小时{minutes}分钟" if minutes else f"{hours}小时"
    if minutes:
        return f"{minutes}分钟"
    return f"{max(1, seconds)}秒"


class PrivateReminder(NcatBotPlugin):
    name = "PrivateReminder"
    version = "1.0.0"
    author = "NcatBot"
    description = "预约时间并通过私聊发送提醒"

    async def on_load(self) -> None:
        self.init_defaults(
            {
                "timezone": "Asia/Shanghai",
                "check_interval": "20s",
                "max_pending": 20,
                "max_content_length": 200,
                "max_horizon_days": 365,
            }
        )
        try:
            self._timezone = ZoneInfo(str(self.get_config("timezone", "Asia/Shanghai")))
        except (KeyError, ValueError):
            self._timezone = datetime.now().astimezone().tzinfo or ZoneInfo("UTC")
        self.data.setdefault("reminders", [])
        if not isinstance(self.data.get("reminders"), list):
            self.data["reminders"] = []
        self.data.setdefault("next_id", 1)
        self.image_dir = Path(self.workspace) / "images"
        self.image_dir.mkdir(parents=True, exist_ok=True)
        self._reminder_lock = asyncio.Lock()
        interval = self.get_config("check_interval", "20s")
        self.add_scheduled_task(
            "check_private_reminders",
            interval,
            callback=self._check_reminders,
        )
        pending = sum(
            1 for item in self.data["reminders"]
            if isinstance(item, dict) and not item.get("failed_at")
        )
        LOG.info("PrivateReminder 已加载，待发送预约 %s 条", pending)

    def _now(self) -> datetime:
        return datetime.now(self._timezone)

    def _config_int(self, key: str, default: int, minimum: int = 1) -> int:
        try:
            value = int(self.get_config(key, default))
        except (TypeError, ValueError):
            value = default
        return max(minimum, value)

    def _max_pending(self) -> int:
        return self._config_int("max_pending", 20)

    def _max_content_length(self) -> int:
        return self._config_int("max_content_length", 200, 1)

    def _max_horizon_days(self) -> int:
        return self._config_int("max_horizon_days", 365)

    def _reminders(self) -> list[dict]:
        reminders = self.data.setdefault("reminders", [])
        if not isinstance(reminders, list):
            reminders = []
            self.data["reminders"] = reminders
        return reminders

    def _user_reminders(self, user_id: str) -> list[dict]:
        return [
            item for item in self._reminders()
            if isinstance(item, dict) and str(item.get("user_id")) == str(user_id)
        ]

    @staticmethod
    def _parse_due(item: dict, fallback_tz) -> datetime | None:
        try:
            due = datetime.fromisoformat(str(item.get("due_at", "")))
        except (TypeError, ValueError):
            return None
        if due.tzinfo is None:
            due = due.replace(tzinfo=fallback_tz)
        return due

    def _image_path(self, prefix: str) -> Path:
        self.image_dir.mkdir(parents=True, exist_ok=True)
        return self.image_dir / f"{prefix}_{time.time_ns()}.png"

    async def _reply(self, event, text: str) -> None:
        path = self._image_path("notice")
        lines = [line for line in str(text).splitlines() if line.strip()]
        await asyncio.to_thread(render_notice, path, "预约提醒", lines)
        await self._reply_image(event, path)

    async def _reply_image(self, event, path: Path) -> None:
        try:
            await event.reply(image=str(path), at_sender=False)
        finally:
            path.unlink(missing_ok=True)

    async def _reply_help(self, event) -> None:
        path = self._image_path("help")
        await asyncio.to_thread(render_help, path)
        await self._reply_image(event, path)

    async def _send_private(self, user_id: str, text: str) -> None:
        path = self._image_path("confirm")
        lines = [line for line in str(text).splitlines() if line.strip()]
        await asyncio.to_thread(render_notice, path, "预约确认", lines)
        await self._send_private_image(user_id, path)

    async def _send_private_image(self, user_id: str, path: Path) -> None:
        try:
            await asyncio.wait_for(
                self.api.qq.post_private_msg(user_id=user_id, image=str(path)),
                timeout=15,
            )
        finally:
            path.unlink(missing_ok=True)

    @staticmethod
    def _message_text(event) -> str:
        parts = [
            str(getattr(segment, "text", "") or "")
            for segment in (getattr(event, "message", None) or [])
        ]
        return "".join(parts).strip() or str(getattr(event, "raw_message", "") or "").strip()

    @registrar.qq.on_message(priority=30)
    async def handle_message(self, event) -> None:
        text = self._message_text(event)
        text = re.sub(r"^@\S+\s*", "", text).strip()
        if not text:
            return
        if text == "我的预约":
            await self._list_reminders(event)
            return
        if text.startswith("取消预约"):
            await self._cancel_reminder(event, text[4:].strip())
            return
        if not text.startswith("预约"):
            return
        arguments = text[2:].strip()
        if not arguments or arguments in ("帮助", "说明"):
            await self._reply_help(event)
            return
        if arguments in ("列表", "列表。"):
            await self._list_reminders(event)
            return
        await self._create_reminder(event, arguments)

    async def _create_reminder(self, event, raw: str) -> None:
        user_id = str(event.sender.user_id)
        now = self._now()
        try:
            due, content = parse_reminder_time(raw, now)
        except ValueError as exc:
            await self._reply(
                event,
                f"预约失败：{exc}\n发送“预约帮助”查看支持的日期与时间写法。",
            )
            return

        horizon = timedelta(days=self._max_horizon_days())
        if due - now > horizon:
            await self._reply(event, f"预约失败：最多只能预约 {self._max_horizon_days()} 天内的提醒")
            return
        if len(content) > self._max_content_length():
            await self._reply(
                event,
                f"预约失败：提醒内容最多 {self._max_content_length()} 个字符",
            )
            return
        active_count = sum(
            1 for item in self._user_reminders(user_id)
            if not item.get("failed_at")
        )
        if active_count >= self._max_pending():
            await self._reply(
                event,
                f"预约失败：最多同时保留 {self._max_pending()} 条预约，请先取消旧预约",
            )
            return

        reminder_id = int(self.data.get("next_id", 1))
        self.data["next_id"] = reminder_id + 1
        item = {
            "id": reminder_id,
            "user_id": user_id,
            "content": content,
            "due_at": due.isoformat(),
            "created_at": now.isoformat(),
            "attempts": 0,
        }
        self._reminders().append(item)
        self._save_data()

        confirmation = (
            f"已预约 #{reminder_id}\n"
            f"时间：{due.strftime('%Y-%m-%d %H:%M')}（{format_remaining(due, now)}后）\n"
            f"内容：{content}\n"
            "到时我会私聊提醒你。"
        )
        if getattr(event, "group_id", None) is not None:
            try:
                await self._send_private(
                    user_id,
                    f"【预约确认】\n{confirmation}",
                )
            except Exception:
                LOG.warning("向用户 %s 发送预约确认私聊失败", user_id)
                confirmation += "\n\n注意：私聊确认发送失败，请先添加机器人为好友，否则到时可能收不到提醒。"
            else:
                confirmation += "\n已私聊发送确认。"
        await self._reply(event, confirmation)

    def _list_entries(self, user_id: str, now: datetime) -> list[dict]:
        entries = []
        for item in sorted(
            self._user_reminders(user_id),
            key=lambda entry: str(entry.get("due_at", "")),
        ):
            due = self._parse_due(item, self._timezone)
            due_text = due.strftime("%Y-%m-%d %H:%M") if due else "时间异常"
            if item.get("failed_at"):
                status_text = "发送失败"
                status_detail = "可取消后重新预约"
                status_kind = "failed"
            elif due is None:
                status_text = "时间异常"
                status_detail = "可取消后重新预约"
                status_kind = "failed"
            else:
                status_text = "待发送"
                remaining = (due - now).total_seconds()
                status_detail = "已到期，等待补发" if remaining <= 0 else f"还有 {format_remaining(due, now)}"
                status_kind = "pending"
            entries.append(
                {
                    "id": item.get("id", "?"),
                    "due_text": due_text,
                    "status_text": status_text,
                    "status_detail": status_detail,
                    "status_kind": status_kind,
                    "content": str(item.get("content", "")),
                }
            )
        return entries

    async def _list_reminders(self, event) -> None:
        user_id = str(event.sender.user_id)
        now = self._now()
        entries = self._list_entries(user_id, now)
        path = self._image_path("list")
        await asyncio.to_thread(render_list, path, entries, self._max_pending())

        if getattr(event, "group_id", None) is None:
            await self._reply_image(event, path)
            return
        try:
            await self._send_private_image(user_id, path)
        except Exception:
            LOG.warning("向用户 %s 发送预约列表私聊失败", user_id)
            await self._reply(event, "预约列表只能私聊查看。请先添加机器人为好友，或在私聊发送“预约列表”。")
        else:
            await self._reply(event, "预约列表已私聊发送。")

    async def _cancel_reminder(self, event, target: str) -> None:
        user_id = str(event.sender.user_id)
        target = target.strip().lstrip("#")
        if not target:
            await self._reply(event, "取消预约：请指定编号，例如“取消预约 3”，或发送“取消预约 全部”")
            return
        if target in ("全部", "所有", "all"):
            owned = self._user_reminders(user_id)
            if not owned:
                await self._reply(event, "你还没有预约。")
                return
            self.data["reminders"] = [
                item for item in self._reminders()
                if not (isinstance(item, dict) and str(item.get("user_id")) == user_id)
            ]
            self._save_data()
            await self._reply(event, f"已取消 {len(owned)} 条预约。")
            return
        if not target.isdigit():
            await self._reply(event, "取消预约：编号必须是数字，例如“取消预约 3”")
            return
        reminder_id = int(target)
        owned = self._user_reminders(user_id)
        item = next((entry for entry in owned if int(entry.get("id", 0)) == reminder_id), None)
        if item is None:
            await self._reply(event, f"没有找到 #{reminder_id}。发送“预约列表”查看编号。")
            return
        self._reminders().remove(item)
        self._save_data()
        await self._reply(event, f"已取消 #{reminder_id}：{item.get('content', '')}")

    async def _check_reminders(self) -> None:
        lock = getattr(self, "_reminder_lock", None)
        if lock is None:
            self._reminder_lock = asyncio.Lock()
            lock = self._reminder_lock
        if lock.locked():
            return
        async with lock:
            now = self._now()
            changed = False
            reminders = self._reminders()
            for item in list(reminders):
                if not isinstance(item, dict):
                    reminders.remove(item)
                    changed = True
                    continue
                failed_at = item.get("failed_at")
                if failed_at:
                    try:
                        failed = datetime.fromisoformat(str(failed_at))
                    except (TypeError, ValueError):
                        failed = now
                    if failed.tzinfo is None:
                        failed = failed.replace(tzinfo=self._timezone)
                    if now - failed > timedelta(days=7):
                        reminders.remove(item)
                        changed = True
                    continue
                due = self._parse_due(item, self._timezone)
                if due is None:
                    reminders.remove(item)
                    changed = True
                    continue
                if due > now:
                    continue
                if int(item.get("attempts", 0)) >= 3:
                    continue
                next_attempt_text = item.get("next_attempt")
                if next_attempt_text:
                    try:
                        next_attempt = datetime.fromisoformat(str(next_attempt_text))
                    except (TypeError, ValueError):
                        next_attempt = None
                    if next_attempt is not None:
                        if next_attempt.tzinfo is None:
                            next_attempt = next_attempt.replace(tzinfo=self._timezone)
                        if next_attempt > now:
                            continue

                user_id = str(item.get("user_id", ""))
                path = self._image_path("reminder")
                late = now - due > timedelta(minutes=5)
                try:
                    await asyncio.to_thread(
                        render_reminder,
                        path,
                        str(item.get("content", "")),
                        due.strftime("%Y-%m-%d %H:%M"),
                        late=late,
                    )
                    await self._send_private_image(user_id, path)
                except Exception as exc:
                    path.unlink(missing_ok=True)
                    attempts = int(item.get("attempts", 0)) + 1
                    item["attempts"] = attempts
                    item["last_error"] = str(exc)[:200]
                    if attempts >= 3:
                        item["failed_at"] = now.isoformat()
                    else:
                        item["next_attempt"] = (
                            now + timedelta(seconds=60 * attempts)
                        ).isoformat()
                    changed = True
                    LOG.warning(
                        "向用户 %s 发送预约 #%s 失败（第 %s 次）：%s",
                        user_id, item.get("id"), attempts, exc,
                    )
                    continue

                reminders.remove(item)
                changed = True
                LOG.info("已向用户 %s 发送预约 #%s", user_id, item.get("id"))

            if changed:
                self._save_data()
