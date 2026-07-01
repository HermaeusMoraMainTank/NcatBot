import base64
import io
import os
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path

from PIL import Image as PILImage
from PIL import ImageDraw, ImageFont

from ncatbot.event.qq import GroupMessageEvent as GroupMessage
from ncatbot.plugin import NcatBotPlugin
from ncatbot.core import registrar
from ncatbot.types import Image as QQImage
from ncatbot.utils import get_log

from common.utils.CommonUtil import CommonUtil

_CLOCK_IN_TRIGGERS = ("群上班", "打卡", "补卡", "上班")
_CLOCK_OUT_TRIGGERS = ("群下班", "下班")
_MIN_SHIFT_SECONDS = 6 * 3600
_MAX_SHIFT_SECONDS = 18 * 3600

_log = get_log("WorkClock")

_CARD_THEMES = {
    "in": {
        "bg": (255, 248, 242),
        "header": (255, 138, 76),
        "accent": (255, 106, 48),
        "text": (48, 48, 56),
        "muted": (120, 108, 100),
    },
    "in_dup": {
        "bg": (248, 249, 252),
        "header": (156, 166, 186),
        "accent": (130, 140, 160),
        "text": (48, 48, 56),
        "muted": (110, 118, 132),
    },
    "in_early": {
        "bg": (255, 245, 236),
        "header": (255, 168, 88),
        "accent": (230, 120, 50),
        "text": (48, 48, 56),
        "muted": (120, 108, 100),
    },
    "out": {
        "bg": (242, 246, 255),
        "header": (82, 118, 210),
        "accent": (58, 92, 180),
        "text": (48, 48, 56),
        "muted": (100, 110, 130),
    },
    "warn": {
        "bg": (255, 244, 244),
        "header": (220, 96, 86),
        "accent": (200, 70, 60),
        "text": (48, 48, 56),
        "muted": (130, 100, 100),
    },
}

_FONT_REGULAR = (
    Path(r"C:\Windows\Fonts\msyh.ttc"),
    Path(r"C:\Windows\Fonts\simhei.ttf"),
    Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
)

_FONT_BOLD = (
    Path(r"C:\Windows\Fonts\msyhbd.ttc"),
    Path(r"C:\Windows\Fonts\msyh.ttc"),
    Path(r"C:\Windows\Fonts\simhei.ttf"),
    Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
)

_CN_DIGIT = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}

_PERIOD_PREFIXES = (
    ("凌晨", 0),
    ("早上", 0),
    ("上午", 0),
    ("中午", 0),
    ("午后", 12),
    ("下午", 12),
    ("晚间", 12),
    ("晚上", 12),
    ("晚", 12),
)


class WorkClock(NcatBotPlugin):
    name = "WorkClock"
    version = "1.6.1"
    description = "群友上下班打卡与工时统计（跨群共享、24小时超时重置）"

    async def on_load(self) -> None:
        self.data.setdefault("by_user", {})
        self._migrate_legacy_data()
        repaired = self._repair_all_user_states()
        if repaired:
            _log.info(f"WorkClock 启动时已修复 {repaired} 条异常班次记录")
        cleaned = self._cleanup_forgotten_shifts()
        if cleaned:
            _log.info(f"WorkClock 启动时已清理 {cleaned} 个超时未下班班次")
        cleaned_images = self._cleanup_stale_card_images()
        if cleaned_images:
            _log.info(f"WorkClock 启动时已清理 {cleaned_images} 个历史临时图片")
        _log.info("WorkClock 上下班打卡插件已加载")

    def _now(self) -> datetime:
        return datetime.now()

    def _day_boundary(self) -> time:
        """仅用于卡片上的工作日标签，不参与班次判定。"""
        raw = str(self.get_config("day_boundary", "04:00")).strip()
        for fmt in ("%H:%M", "%H:%M:%S"):
            try:
                return datetime.strptime(raw, fmt).time()
            except ValueError:
                continue
        return time(4, 0)

    def _work_date(self, dt: datetime | None = None) -> str:
        dt = dt or self._now()
        d = dt.date()
        if dt.time() < self._day_boundary():
            d -= timedelta(days=1)
        return d.strftime("%Y-%m-%d")

    def _forget_timeout(self) -> timedelta:
        return timedelta(hours=float(self.get_config("forget_timeout_hours", 24)))

    def _is_forgotten_shift(self, clock_in: datetime, now: datetime) -> bool:
        return now - clock_in >= self._forget_timeout()

    def _auto_close_forgotten_shift(
        self, user_id: int, shift: dict, clock_in: datetime
    ) -> tuple[dict, str]:
        end_dt = (clock_in + self._forget_timeout()).replace(second=0, microsecond=0)
        closed = self._auto_close_shift(user_id, shift, end_dt)
        duration = self._format_duration(self._shift_duration_seconds(closed))
        end_display = self._format_clock_display(closed["clock_out"])
        hours = int(self._forget_timeout().total_seconds() // 3600)
        note = (
            f"上一班次超过{hours}小时未下班，已自动结班"
            f"（{end_display}，工时 {duration}）"
        )
        return closed, note

    def _auto_close_shift(self, user_id: int, shift: dict, end_dt: datetime) -> dict:
        closed = dict(shift)
        closed["clock_out"] = end_dt.replace(microsecond=0).isoformat(timespec="seconds")
        closed["auto_closed"] = True
        self._user_state(user_id)["last_closed"] = closed
        self._close_shift(user_id)
        return closed

    def _cleanup_stale_card_images(self) -> int:
        data_dir = Path(__file__).resolve().parent / "data"
        if not data_dir.is_dir():
            return 0
        removed = 0
        for pattern in ("card_*.png", "active_list_*.png"):
            for path in data_dir.glob(pattern):
                try:
                    path.unlink(missing_ok=True)
                    removed += 1
                except OSError as exc:
                    _log.warning(f"WorkClock 清理临时图片失败: {path} ({exc})")
        return removed

    @staticmethod
    def _qq_image_from_png(png: bytes) -> QQImage:
        return QQImage(file=f"base64://{base64.b64encode(png).decode()}")

    def _cleanup_forgotten_shifts(self, now: datetime | None = None) -> int:
        """自动结清超过 forget_timeout 仍未下班的 active 班次。"""
        now = now or self._now()
        cleaned = 0
        by_user = self.data.get("by_user", {})
        for uid_str, state in list(by_user.items()):
            if not isinstance(state, dict):
                continue
            shift = state.get("active")
            if not isinstance(shift, dict) or not shift.get("clock_in") or shift.get("clock_out"):
                continue
            try:
                user_id = int(uid_str)
                clock_in = self._parse_clock_datetime(shift["clock_in"])
            except (ValueError, TypeError):
                state.pop("active", None)
                cleaned += 1
                continue
            if self._is_forgotten_shift(clock_in, now):
                self._auto_close_forgotten_shift(user_id, shift, clock_in)
                cleaned += 1
        if cleaned:
            self._save_data()
            _log.info(f"WorkClock 已自动结清 {cleaned} 个超时未下班班次")
        return cleaned

    def _work_date_to_date(self, work_date: str) -> date:
        return datetime.strptime(work_date, "%Y-%m-%d").date()

    def _parse_clock_datetime(self, value: str) -> datetime:
        if "T" in value:
            return datetime.fromisoformat(value)
        return datetime.combine(date.today(), self._parse_time(value))

    def _format_clock_display(self, value: str) -> str:
        """卡片展示用时间（跨天班次标注日期）。"""
        dt = self._parse_clock_datetime(value)
        return dt.strftime("%m-%d %H:%M")

    def _user_state(self, user_id: int) -> dict:
        return self.data.setdefault("by_user", {}).setdefault(str(user_id), {})

    def _shift_close_time(self, shift: dict) -> datetime | None:
        clock_out = shift.get("clock_out")
        if not clock_out:
            return None
        try:
            return self._parse_clock_datetime(clock_out)
        except (ValueError, TypeError):
            return None

    def _repair_user_state(self, state: dict) -> bool:
        """修复 active 已写入 clock_out 但未结清的半完成状态（异常退出/崩溃后遗留）。"""
        active = state.get("active")
        if not isinstance(active, dict) or not active.get("clock_in") or not active.get("clock_out"):
            return False

        repaired = dict(active)
        existing_closed = state.get("last_closed")
        if isinstance(existing_closed, dict) and existing_closed.get("clock_out"):
            active_end = self._shift_close_time(repaired)
            closed_end = self._shift_close_time(existing_closed)
            if active_end and closed_end and closed_end >= active_end:
                state.pop("active", None)
                return True

        state["last_closed"] = repaired
        state.pop("active", None)
        return True

    def _repair_all_user_states(self) -> int:
        repaired = 0
        by_user = self.data.get("by_user", {})
        for state in by_user.values():
            if not isinstance(state, dict):
                continue
            if self._repair_user_state(state):
                repaired += 1
        if repaired:
            self._save_data()
        return repaired

    def _get_open_shift(self, user_id: int) -> dict | None:
        state = self._user_state(user_id)
        if self._repair_user_state(state):
            self._save_data()
        shift = state.get("active")
        if isinstance(shift, dict) and shift.get("clock_in") and not shift.get("clock_out"):
            return shift
        return None

    def _get_today_closed_shift(self, user_id: int, now: datetime | None = None) -> dict | None:
        now = now or self._now()
        closed = self._user_state(user_id).get("last_closed")
        if not isinstance(closed, dict):
            return None
        if not closed.get("clock_in") or not closed.get("clock_out"):
            return None
        if closed.get("work_date") != self._work_date(now):
            return None
        return closed

    def _start_shift(self, user_id: int, clock_in_dt: datetime) -> dict:
        shift = {
            "work_date": self._work_date(clock_in_dt),
            "clock_in": clock_in_dt.replace(microsecond=0).isoformat(timespec="seconds"),
            "clock_out": None,
        }
        self._user_state(user_id)["active"] = shift
        return shift

    def _close_shift(self, user_id: int) -> None:
        self._user_state(user_id).pop("active", None)

    def _clear_user_today_records(self, user_id: int, work_date: str) -> None:
        """清除指定工作日的上下班记录（active 与 last_closed）。"""
        state = self._user_state(user_id)
        active = state.get("active")
        if isinstance(active, dict) and active.get("work_date") == work_date:
            state.pop("active", None)
        closed = state.get("last_closed")
        if isinstance(closed, dict) and closed.get("work_date") == work_date:
            state.pop("last_closed", None)
        self._save_data()

    def _combine_manual_time(self, manual_time: time, now: datetime) -> datetime:
        """补卡时间：在前/今/后三天内选最合理的一个时刻。"""
        candidates = [
            datetime.combine(now.date() + timedelta(days=offset), manual_time)
            for offset in (-1, 0, 1)
        ]
        past = [dt for dt in candidates if dt <= now]
        if past:
            return max(past)
        return min(candidates)

    def _shift_duration_seconds(self, shift: dict) -> int:
        start = self._parse_clock_datetime(shift["clock_in"])
        end = self._parse_clock_datetime(shift["clock_out"])
        return max(0, int((end - start).total_seconds()))

    def _legacy_to_shift(self, rec: dict) -> dict | None:
        if not rec.get("clock_in"):
            return None
        try:
            anchor = self._work_date_to_date(rec.get("date") or self._work_date())
        except ValueError:
            return None
        clock_in = datetime.combine(anchor, self._parse_time(rec["clock_in"]))
        shift = {
            "work_date": rec.get("date") or self._work_date(clock_in),
            "clock_in": clock_in.isoformat(timespec="seconds"),
            "clock_out": None,
        }
        if rec.get("clock_out"):
            clock_out = datetime.combine(anchor, self._parse_time(rec["clock_out"]))
            if clock_out <= clock_in:
                clock_out += timedelta(days=1)
            shift["clock_out"] = clock_out.isoformat(timespec="seconds")
        return shift

    def _pick_better_shift(self, left: dict | None, right: dict | None) -> dict | None:
        if left is None:
            return right
        if right is None:
            return left
        left_open = not left.get("clock_out")
        right_open = not right.get("clock_out")
        if left_open != right_open:
            return left if left_open else right
        left_in = self._parse_clock_datetime(left["clock_in"])
        right_in = self._parse_clock_datetime(right["clock_in"])
        return left if left_in >= right_in else right

    def _migrate_legacy_data(self) -> None:
        legacy = self.data.pop("by_group", None)
        if not legacy:
            return
        by_user = self.data.setdefault("by_user", {})
        for group_records in legacy.values():
            if not isinstance(group_records, dict):
                continue
            for uid, rec in group_records.items():
                if not isinstance(rec, dict):
                    continue
                shift = self._legacy_to_shift(rec)
                if shift is None or shift.get("clock_out"):
                    continue
                existing = by_user.get(uid, {}).get("active")
                merged = self._pick_better_shift(existing, shift)
                if merged is None:
                    continue
                by_user.setdefault(uid, {})["active"] = merged
        _log.info("WorkClock 已将 by_group 数据迁移为按用户存储")
        self._save_data()

    def _parse_time(self, value: str) -> time:
        return datetime.strptime(value, "%H:%M:%S").time()

    def _normalize_time_text(self, text: str) -> str:
        text = text.strip()
        for fw, hw in zip("０１２３４５６７８９：．", "0123456789:."):
            text = text.replace(fw, hw)
        return re.sub(r"\s+", "", text)

    def _parse_cn_number(self, s: str) -> int | None:
        s = s.strip()
        if not s:
            return None
        if s.isdigit():
            return int(s)
        if s == "十":
            return 10
        if s.startswith("十"):
            rest = s[1:]
            return 10 + (_CN_DIGIT.get(rest, 0) if rest else 0)
        if "十" in s:
            idx = s.index("十")
            left, right = s[:idx], s[idx + 1:]
            tens = _CN_DIGIT.get(left, 0) if left else 1
            ones = _CN_DIGIT.get(right, 0) if right else 0
            return tens * 10 + ones
        if len(s) == 1 and s in _CN_DIGIT:
            return _CN_DIGIT[s]
        return None

    def _build_time(self, hour: int, minute: int, second: int, period_add: int) -> time | None:
        if period_add and hour < 12:
            hour += period_add
        if 0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59:
            return time(hour, minute, second)
        return None

    def _looks_like_time_text(self, text: str) -> bool:
        """判断后缀是否像在填写补卡时间，避免误匹配日常聊天。"""
        text = self._normalize_time_text(text)
        if not text:
            return False
        if re.fullmatch(r"\d{1,2}", text):
            return True
        if re.search(r"[\d.:：．]", text):
            return True
        if "点" in text or "半" in text or "整" in text:
            return True
        if any(text.startswith(prefix) for prefix, _ in _PERIOD_PREFIXES):
            return True
        if re.search(r"[零一二两三四五六七八九十]", text):
            return True
        return False

    def _parse_clock_in_time(self, text: str) -> time | None:
        """解析补卡时间，支持 9点 / 九点 / 9:00 / 9.00 / 九点半 / 下午三点 等。"""
        text = self._normalize_time_text(text)
        if not text:
            return None

        period_add = 0
        for prefix, add in _PERIOD_PREFIXES:
            if text.startswith(prefix):
                text = text[len(prefix):]
                period_add = add
                break

        text = text.removesuffix("钟").removesuffix("分")

        m = re.fullmatch(r"(\d{1,2})[:.](\d{2})(?:[:.](\d{2}))?", text)
        if m:
            sec = int(m.group(3)) if m.group(3) else 0
            return self._build_time(int(m.group(1)), int(m.group(2)), sec, period_add)

        m = re.fullmatch(r"(\d{1,2})点(?:(\d{1,2})|半|整)?", text)
        if m:
            hour = int(m.group(1))
            if "半" in text:
                minute = 30
            elif m.group(2):
                minute = int(m.group(2))
            else:
                minute = 0
            return self._build_time(hour, minute, 0, period_add)

        m = re.fullmatch(r"(\d{1,2})", text)
        if m:
            return self._build_time(int(m.group(1)), 0, 0, period_add)

        m = re.fullmatch(
            r"([零一二两三四五六七八九十]+)点(半|整|([零一二两三四五六七八九十]+))?",
            text,
        )
        if m:
            hour = self._parse_cn_number(m.group(1))
            if hour is None:
                return None
            if m.group(2) == "半":
                minute = 30
            elif m.group(3):
                minute = self._parse_cn_number(m.group(3)) or 0
            else:
                minute = 0
            return self._build_time(hour, minute, 0, period_add)

        return None

    def _format_duration(self, seconds: int) -> str:
        seconds = max(0, int(seconds))
        hours, remainder = divmod(seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        parts: list[str] = []
        if hours:
            parts.append(f"{hours}小时")
        if minutes:
            parts.append(f"{minutes}分")
        if secs or not parts:
            parts.append(f"{secs}秒")
        return "".join(parts)

    def _short_date(self, work_date: str | None = None) -> str:
        if work_date:
            return datetime.strptime(work_date, "%Y-%m-%d").strftime("%m-%d")
        return self._now().strftime("%m-%d")

    def _load_font(
        self, size: int, *, bold: bool = False
    ) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        candidates = _FONT_BOLD if bold else _FONT_REGULAR
        for p in candidates:
            if p.is_file():
                try:
                    return ImageFont.truetype(str(p), size)
                except OSError:
                    continue
        return ImageFont.load_default()

    def _draw_divider(
        self, draw: ImageDraw.ImageDraw, x1: int, y: int, x2: int, color: tuple[int, int, int]
    ) -> None:
        draw.line([(x1, y), (x2, y)], fill=color, width=1)

    def _load_avatar(self, user_id: int, size: int = 72) -> PILImage.Image:
        avatar = PILImage.new("RGB", (size, size), (224, 233, 252))
        try:
            avatar_path = CommonUtil.get_avatar(str(user_id))
            if avatar_path and os.path.exists(avatar_path):
                avatar = PILImage.open(avatar_path).convert("RGB").resize((size, size))
        except Exception:
            pass

        mask = PILImage.new("L", (size, size), 0)
        mdraw = ImageDraw.Draw(mask)
        mdraw.ellipse((0, 0, size, size), fill=255)
        out = PILImage.new("RGBA", (size, size), (0, 0, 0, 0))
        out.paste(avatar, (0, 0), mask)
        return out

    def _text_width(self, draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
        return int(draw.textbbox((0, 0), text, font=font)[2])

    def _fit_text(
        self, draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_w: int
    ) -> str:
        if self._text_width(draw, text, font) <= max_w:
            return text
        trimmed = text
        while trimmed and self._text_width(draw, trimmed + "…", font) > max_w:
            trimmed = trimmed[:-1]
        return (trimmed + "…") if trimmed else "…"

    def _render_card(
        self,
        user_id: int,
        nickname: str,
        kind: str,
        title: str,
        rows: list[tuple[str, str]],
        note: str | None = None,
        tail: str | None = None,
        work_date: str | None = None,
    ) -> bytes:
        theme = _CARD_THEMES.get(kind, _CARD_THEMES["in"])
        W = 500
        M = 16
        accent_w = 5
        pad_x = 24
        card_x1 = M + accent_w
        content_left = card_x1 + pad_x
        content_right = W - M - pad_x
        content_w = content_right - content_left

        font_name = self._load_font(16)
        font_title = self._load_font(20, bold=True)
        font_date = self._load_font(15)
        font_label = self._load_font(17)
        font_value = self._load_font(22, bold=True)
        font_hero = self._load_font(36, bold=True)
        font_note = self._load_font(15)
        font_tail = self._load_font(16)

        avatar_size = 48
        header_h = 84
        row_h = 46
        if len(rows) == 1:
            body_h = 108
        elif rows:
            body_h = len(rows) * row_h + 20
        else:
            body_h = 40
        note_h = 26 if note else 0
        tail_h = 28 if tail else 0
        bottom_pad = 22
        H = M + header_h + 1 + body_h + note_h + tail_h + bottom_pad + M

        canvas = PILImage.new("RGB", (W, H), (240, 243, 248))
        draw = ImageDraw.Draw(canvas)

        card_box = (M, M, W - M, H - M)
        draw.rounded_rectangle(card_box, radius=16, fill=(255, 255, 255), outline=(220, 226, 236))
        draw.rounded_rectangle(
            (M, M, M + accent_w, H - M),
            radius=16,
            fill=theme["accent"],
        )
        draw.rectangle((M + accent_w // 2, M, M + accent_w, H - M), fill=theme["accent"])

        avatar = self._load_avatar(user_id, avatar_size)
        avatar_y = M + (header_h - avatar_size) // 2
        canvas.paste(avatar, (content_left, avatar_y), avatar)

        text_x = content_left + avatar_size + 14
        date_text = self._short_date(work_date)
        date_w = self._text_width(draw, date_text, font_date)
        date_x = content_right - date_w
        name_max_w = max(60, date_x - text_x - 12)
        nickname = self._fit_text(draw, nickname, font_name, name_max_w)
        title = self._fit_text(draw, title, font_title, content_right - text_x)

        draw.text((text_x, M + 22), nickname, font=font_name, fill=theme["muted"])
        draw.text((date_x, M + 22), date_text, font=font_date, fill=theme["muted"])
        draw.text((text_x, M + 46), title, font=font_title, fill=theme["text"])

        divider_y = M + header_h
        self._draw_divider(draw, card_x1 + 12, divider_y, W - M - 12, (235, 239, 245))

        body_y = divider_y + 18
        if len(rows) == 1:
            label, value = rows[0]
            display = self._format_clock_display(value) if ("T" in value or ":" in value) else value
            display = self._fit_text(draw, display, font_hero, content_w)
            hero_w = self._text_width(draw, display, font_hero)
            draw.text(
                (content_left + (content_w - hero_w) // 2, body_y + 8),
                display,
                font=font_hero,
                fill=theme["accent"],
            )
            label = self._fit_text(draw, label, font_label, content_w)
            label_w = self._text_width(draw, label, font_label)
            draw.text(
                (content_left + (content_w - label_w) // 2, body_y + 58),
                label,
                font=font_label,
                fill=theme["muted"],
            )
        elif rows:
            for i, (label, value) in enumerate(rows):
                y = body_y + i * row_h
                short = (
                    label.replace("（最晚）", "")
                    .replace("上班时间", "上班")
                    .replace("下班时间", "下班")
                    .replace("今日工时", "工时")
                )
                display = value if "工时" in label else self._format_clock_display(value)
                label_fit = self._fit_text(draw, short, font_label, content_w // 2)
                value_fit = self._fit_text(draw, display, font_value, content_w // 2)
                draw.text((content_left, y + 10), label_fit, font=font_label, fill=theme["muted"])
                val_w = self._text_width(draw, value_fit, font_value)
                draw.text((content_right - val_w, y + 8), value_fit, font=font_value, fill=theme["accent"])
                if i < len(rows) - 1:
                    self._draw_divider(
                        draw, content_left, y + row_h - 4, content_right, (243, 246, 250)
                    )

        y = body_y + body_h
        if note:
            note_text = self._fit_text(draw, note, font_note, content_w)
            note_w = self._text_width(draw, note_text, font_note)
            draw.text(
                (content_left + (content_w - note_w) // 2, y),
                note_text,
                font=font_note,
                fill=theme["muted"],
            )
            y += note_h

        if tail:
            tail_text = self._fit_text(draw, tail, font_tail, content_w)
            tail_w = self._text_width(draw, tail_text, font_tail)
            draw.text(
                (content_left + (content_w - tail_w) // 2, y),
                tail_text,
                font=font_tail,
                fill=theme["text"],
            )

        buf = io.BytesIO()
        canvas.save(buf, format="PNG")
        return buf.getvalue()

    async def _reply_card(
        self,
        event: GroupMessage,
        kind: str,
        title: str,
        rows: list[tuple[str, str]],
        note: str | None = None,
        tail: str | None = None,
        work_date: str | None = None,
    ) -> None:
        nickname = event.sender.nickname or str(event.sender.user_id)
        png = self._render_card(
            event.sender.user_id,
            nickname,
            kind,
            title,
            rows,
            note=note,
            tail=tail,
            work_date=work_date,
        )
        await self.api.qq.post_group_msg(
            group_id=event.group_id,
            image=self._qq_image_from_png(png),
            reply=event.message_id,
        )

    async def _clock_in(self, event: GroupMessage, manual_time: time | None = None) -> None:
        now = self._now()
        user_id = event.sender.user_id
        is_manual = manual_time is not None
        clock_in_dt = (
            self._combine_manual_time(manual_time, now)
            if manual_time is not None
            else now.replace(microsecond=0)
        )
        shift = self._get_open_shift(user_id)
        auto_note: str | None = None

        if shift:
            existing = self._parse_clock_datetime(shift["clock_in"])
            if self._is_forgotten_shift(existing, clock_in_dt):
                _, auto_note = self._auto_close_forgotten_shift(user_id, shift, existing)
                shift = None
            elif clock_in_dt < existing:
                shift["clock_in"] = clock_in_dt.isoformat(timespec="seconds")
                shift["work_date"] = self._work_date(clock_in_dt)
                self._save_data()
                delta_min = int((existing - clock_in_dt).total_seconds() / 60)
                title = "补卡成功" if is_manual else "签到时间已更新"
                note = (
                    f"已按补卡时间录入，比上次提前 {delta_min} 分钟"
                    if is_manual
                    else f"比上次提前了 {delta_min} 分钟"
                )
                await self._reply_card(
                    event,
                    "in_early",
                    title,
                    [("首次上班", shift["clock_in"])],
                    note=note,
                    tail="今天也要加油呀～",
                    work_date=shift["work_date"],
                )
                return
            else:
                existing_display = self._format_clock_display(shift["clock_in"])
                new_display = self._format_clock_display(clock_in_dt.isoformat(timespec="seconds"))
                note = (
                    f"补卡时间 {new_display} 晚于已记录 {existing_display}，未更新"
                    if is_manual
                    else f"本次 {self._format_clock_display(now.isoformat(timespec='seconds'))} 不计入，以最早为准"
                )
                await self._reply_card(
                    event,
                    "in_dup",
                    "本班次已签到",
                    [("首次上班", shift["clock_in"])],
                    note=note,
                    work_date=shift["work_date"],
                )
                return

        shift = self._start_shift(user_id, clock_in_dt)
        self._save_data()
        title = "补卡成功" if is_manual else "上班打卡成功"
        note = auto_note
        if is_manual:
            note = "已按指定时间录入" if not auto_note else f"{auto_note}；已按指定时间录入"
        await self._reply_card(
            event,
            "in",
            title,
            [("签到时间", shift["clock_in"])],
            note=note,
            tail="今天也要加油呀～",
            work_date=shift["work_date"],
        )

    async def _clock_in_with_text(self, event: GroupMessage, time_text: str) -> None:
        parsed = self._parse_clock_in_time(time_text)
        if parsed is None:
            await self._reply_card(
                event,
                "warn",
                "补卡时间无法识别",
                [],
                note="示例：补卡 9点 / 上班 9:00 / 打卡 九点 / 打卡 九点半",
            )
            return
        await self._clock_in(event, manual_time=parsed)

    async def _reply_shift_duration_rejected(
        self,
        event: GroupMessage,
        *,
        too_short: bool,
        work_date: str,
    ) -> None:
        if too_short:
            await self._reply_card(
                event,
                "warn",
                "就上这么点时间",
                [],
                tail="别上了",
                work_date=work_date,
            )
        else:
            await self._reply_card(
                event,
                "warn",
                "上这么久 工资一定很高吧",
                [],
                work_date=work_date,
            )

    async def _save_closed_shift(
        self,
        event: GroupMessage,
        user_id: int,
        closed: dict,
        *,
        kind: str,
        title: str,
        note: str | None = None,
        tail: str | None = "辛苦啦，早点休息～",
    ) -> None:
        duration_sec = self._shift_duration_seconds(closed)
        work_date = closed.get("work_date") or self._work_date()

        if duration_sec < _MIN_SHIFT_SECONDS:
            self._clear_user_today_records(user_id, work_date)
            await self._reply_shift_duration_rejected(
                event, too_short=True, work_date=work_date
            )
            return
        if duration_sec > _MAX_SHIFT_SECONDS:
            self._clear_user_today_records(user_id, work_date)
            await self._reply_shift_duration_rejected(
                event, too_short=False, work_date=work_date
            )
            return

        if tail and duration_sec >= 9 * 3600:
            tail = "加班也要注意身体哦"
        self._user_state(user_id)["last_closed"] = closed
        self._close_shift(user_id)
        self._save_data()
        await self._reply_card(
            event,
            kind,
            title,
            [
                ("上班时间", closed["clock_in"]),
                ("下班时间", closed["clock_out"]),
                ("今日工时", self._format_duration(duration_sec)),
            ],
            note=note,
            tail=tail,
            work_date=closed["work_date"],
        )

    async def _clock_out(
        self, event: GroupMessage, manual_time: time | None = None
    ) -> None:
        now = self._now().replace(microsecond=0)
        user_id = event.sender.user_id
        is_manual = manual_time is not None
        clock_out_dt = (
            self._combine_manual_time(manual_time, now)
            if manual_time is not None
            else now
        )
        shift = self._get_open_shift(user_id)

        if shift:
            clock_in_dt = self._parse_clock_datetime(shift["clock_in"])
            if clock_out_dt < clock_in_dt:
                await self._reply_card(
                    event,
                    "warn",
                    "下班时间无效",
                    [],
                    note="下班时间不能早于上班时间",
                )
                return

            closed = dict(shift)
            cap_note: str | None = None
            if self._is_forgotten_shift(clock_in_dt, clock_out_dt):
                end_dt = (clock_in_dt + self._forget_timeout()).replace(
                    second=0, microsecond=0
                )
                closed["clock_out"] = end_dt.isoformat(timespec="seconds")
                hours = int(self._forget_timeout().total_seconds() // 3600)
                cap_note = f"已超过{hours}小时未下班，工时按{hours}小时计"
            else:
                closed["clock_out"] = clock_out_dt.isoformat(timespec="seconds")

            title = "补卡成功" if is_manual else "下班打卡成功"
            if is_manual and not cap_note:
                cap_note = "已按指定时间录入"
            await self._save_closed_shift(
                event,
                user_id,
                closed,
                kind="out",
                title=title,
                note=cap_note,
            )
            return

        closed = self._get_today_closed_shift(user_id, now)
        if not closed:
            await self._reply_card(
                event,
                "warn",
                "还没打上班卡哦",
                [],
                note="请先发送「打卡」「上班」或「补卡」",
            )
            return

        clock_in_dt = self._parse_clock_datetime(closed["clock_in"])
        if clock_out_dt < clock_in_dt:
            await self._reply_card(
                event,
                "warn",
                "下班时间无效",
                [],
                note="下班时间不能早于上班时间",
            )
            return

        existing_out = self._parse_clock_datetime(closed["clock_out"])
        if clock_out_dt == existing_out:
            duration = self._format_duration(self._shift_duration_seconds(closed))
            await self._reply_card(
                event,
                "in_dup",
                "本班次已下班",
                [
                    ("上班时间", closed["clock_in"]),
                    ("下班时间", closed["clock_out"]),
                    ("今日工时", duration),
                ],
                note="与已记录时间一致，未更新",
                work_date=closed["work_date"],
            )
            return

        updated = dict(closed)
        delta_min = int(abs((clock_out_dt - existing_out).total_seconds()) / 60)
        existing_display = self._format_clock_display(closed["clock_out"])
        new_display = self._format_clock_display(
            clock_out_dt.isoformat(timespec="seconds")
        )

        if is_manual:
            updated["clock_out"] = clock_out_dt.isoformat(timespec="seconds")
            direction = "提前" if clock_out_dt < existing_out else "延后"
            await self._save_closed_shift(
                event,
                user_id,
                updated,
                kind="out",
                title="补卡成功",
                note=(
                    f"已按补卡时间录入，比上次{direction} {delta_min} 分钟"
                    f"（{existing_display} → {new_display}）"
                ),
            )
            return

        if clock_out_dt > existing_out:
            updated["clock_out"] = clock_out_dt.isoformat(timespec="seconds")
            await self._save_closed_shift(
                event,
                user_id,
                updated,
                kind="out",
                title="下班时间已更新",
                note=f"比上次延后了 {delta_min} 分钟（{existing_display} → {new_display}）",
            )
            return

        await self._reply_card(
            event,
            "in_dup",
            "本班次已下班",
            [
                ("上班时间", closed["clock_in"]),
                ("下班时间", closed["clock_out"]),
                ("今日工时", self._format_duration(self._shift_duration_seconds(closed))),
            ],
            note=(
                f"本次 {self._format_clock_display(now.isoformat(timespec='seconds'))} "
                f"不计入，以最晚为准"
            ),
            work_date=closed["work_date"],
        )

    async def _clock_out_with_text(self, event: GroupMessage, time_text: str) -> None:
        parsed = self._parse_clock_in_time(time_text)
        if parsed is None:
            await self._reply_card(
                event,
                "warn",
                "补卡时间无法识别",
                [],
                note="示例：下班 6点 / 下班 18:00 / 下班 六点半",
            )
            return
        await self._clock_out(event, manual_time=parsed)

    def _format_list_time(self, value: str) -> str:
        dt = self._parse_clock_datetime(value)
        return dt.strftime("%H:%M")

    async def _build_group_today_rows(
        self, group_id: str
    ) -> list[tuple[str, str, bool, str, str | None, int]]:
        """返回今日打卡列表：(昵称, user_id, 是否上班中, 上班时间, 下班时间, 工时秒数)。"""
        self._repair_all_user_states()
        self._cleanup_forgotten_shifts()

        by_user = self.data.get("by_user", {})
        if not by_user:
            return []

        now = self._now()
        today = self._work_date(now)
        members_response = await self.api.qq.query.get_group_member_list(
            group_id=group_id
        )
        members = CommonUtil.parse_group_member_list(members_response)
        member_by_id = {str(m.user_id): m for m in members}

        rows: list[tuple[str, str, bool, str, str | None, int]] = []
        for user_id, state in by_user.items():
            if user_id not in member_by_id:
                continue
            if not isinstance(state, dict):
                continue

            member = member_by_id[user_id]
            name = member.card or member.nickname or user_id

            shift = state.get("active")
            if isinstance(shift, dict) and shift.get("clock_in") and not shift.get("clock_out"):
                clock_in = self._parse_clock_datetime(shift["clock_in"])
                if not self._is_forgotten_shift(clock_in, now):
                    duration = max(0, int((now - clock_in).total_seconds()))
                    rows.append(
                        (name, user_id, True, shift["clock_in"], None, duration)
                    )
                    continue

            closed = state.get("last_closed")
            if (
                isinstance(closed, dict)
                and closed.get("clock_in")
                and closed.get("clock_out")
                and closed.get("work_date") == today
            ):
                duration = self._shift_duration_seconds(closed)
                rows.append(
                    (
                        name,
                        user_id,
                        False,
                        closed["clock_in"],
                        closed["clock_out"],
                        duration,
                    )
                )

        rows.sort(key=lambda item: (0 if item[2] else 1, item[3]))
        return rows

    def _render_active_list_image(
        self, rows: list[tuple[str, str, bool, str, str | None, int]]
    ) -> bytes:
        theme = _CARD_THEMES["in"]
        width = 980
        padding = 28
        row_height = 42
        title_height = 56
        height = padding * 2 + title_height + len(rows) * row_height + 16

        img = PILImage.new("RGB", (width, height), color=theme["bg"])
        draw = ImageDraw.Draw(img)

        title_font = self._load_font(28, bold=True)
        text_font = self._load_font(20)
        meta_font = self._load_font(15)
        inner_width = width - padding * 2

        y = padding
        title = "群上班列表"
        title_w = self._text_width(draw, title, title_font)
        draw.text(
            ((width - title_w) // 2, y),
            title,
            font=title_font,
            fill=theme["accent"],
        )
        y += title_height - 8
        draw.line(
            [(padding, y), (width - padding, y)],
            fill=(220, 225, 235),
            width=2,
        )
        y += 16

        active_count = 0
        for name, user_id, is_active, clock_in, clock_out, duration_sec in rows:
            duration = self._format_duration(duration_sec)
            left = f"{name}（{user_id}）"
            if is_active:
                active_count += 1
                clock_display = self._format_list_time(clock_in)
                right = f"{clock_display} 上班中 · {duration}"
                color = theme["text"]
            else:
                in_display = self._format_list_time(clock_in)
                out_display = self._format_list_time(clock_out or clock_in)
                right = f"{in_display}-{out_display} 已下班 · {duration}"
                color = theme["muted"]
            line = self._fit_text(
                draw, f"{left} →→→ {right}", text_font, inner_width
            )
            draw.text((padding, y), line, font=text_font, fill=color)
            y += row_height

        closed_count = len(rows) - active_count
        if active_count and closed_count:
            footer = f"上班中 {active_count} 人 · 已下班 {closed_count} 人"
        elif active_count:
            footer = f"共 {active_count} 人在上班"
        else:
            footer = f"共 {closed_count} 人已下班"
        footer_w = self._text_width(draw, footer, meta_font)
        draw.text(
            (width - padding - footer_w, y - 6),
            footer,
            font=meta_font,
            fill=theme["muted"],
        )

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    async def _handle_group_active_list(self, event: GroupMessage) -> None:
        rows = await self._build_group_today_rows(str(event.group_id))
        if not rows:
            await self.api.qq.post_group_msg(
                group_id=event.group_id,
                text="今天还没有人打卡哦~",
                reply=event.message_id,
            )
            return

        png = self._render_active_list_image(rows)
        await self.api.qq.post_group_msg(
            group_id=event.group_id,
            image=self._qq_image_from_png(png),
            reply=event.message_id,
        )

    @registrar.qq.on_group_message()
    async def handle_work_clock(self, input: GroupMessage) -> None:
        message = input.raw_message.strip()
        if message == "群上班列表":
            await self._handle_group_active_list(input)
            return

        for trigger in _CLOCK_OUT_TRIGGERS:
            if message == trigger:
                await self._clock_out(input)
                return
            if not message.startswith(trigger):
                continue
            time_text = message[len(trigger) :].strip()
            if not time_text:
                await self._clock_out(input)
                return
            if not self._looks_like_time_text(time_text):
                continue
            await self._clock_out_with_text(input, time_text)
            return

        for trigger in _CLOCK_IN_TRIGGERS:
            if message == trigger:
                await self._clock_in(input)
                return
            if not message.startswith(trigger):
                continue
            time_text = message[len(trigger) :].strip()
            if not time_text:
                await self._clock_in(input)
                return
            if not self._looks_like_time_text(time_text):
                continue
            await self._clock_in_with_text(input, time_text)
            return
