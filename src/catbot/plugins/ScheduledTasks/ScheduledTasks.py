import asyncio
import json
import random
import re
import time
from difflib import SequenceMatcher
from pathlib import Path

from ncatbot.core import registrar
from ncatbot.plugin import NcatBotPlugin
from ncatbot.types import MessageArray, Record
from ncatbot.utils import get_log


LOG = get_log("ScheduledTasks")
PLUGIN_DIR = Path(__file__).resolve().parent
IMAGE_PATH = PLUGIN_DIR / "resources" / "noon_image.jpg"
EVENING_IMAGE_PATH = PLUGIN_DIR / "resources" / "evening_image.png"
MUSIC_URL = "http://music.163.com/song/media/outer/url?id=2691385220&userid=112511786&sc=wm&tn="
MUSIC_TITLE = "小石DISCO"
JIABAOYU_MUSIC_URL = "http://music.163.com/song/media/outer/url?id=2728903724&userid=112511786&sc=wm&tn="
JIABAOYU_KEYWORDS = ("贾宝玉", "回答我")
LYRIC_PATTERNS = (
    "哒哒哒哒哒啦哒所以说是恋恋胜利啦",
    "哒哒哒哒哒啦哒就好像是陷入恋爱啦",
    "爱loveheart恋魔法winkchuuuuuu",
)
SIMILARITY_THRESHOLD = 0.72


class ScheduledTasks(NcatBotPlugin):
    """集中管理无需指令的定时任务。"""

    name = "ScheduledTasks"
    version = "1.0.0"
    author = "NcatBot"
    description = "集中管理无需指令的定时任务"

    async def on_load(self) -> None:
        self.init_defaults({"group_list": [], "music_cooldown": 30})
        self._main_loop = asyncio.get_running_loop()
        self._scheduled_futures = set()
        self._music_last_sent: dict[str, float] = {}
        if not IMAGE_PATH.is_file():
            LOG.error("定时任务所需的图片资源不存在: %s", IMAGE_PATH)
            return
        if not EVENING_IMAGE_PATH.is_file():
            LOG.error("定时任务所需的图片资源不存在: %s", EVENING_IMAGE_PATH)
            return

        self.add_scheduled_task(
            "send_noon_image",
            "11:00",
            callback=self._schedule_noon_image,
        )
        self.add_scheduled_task(
            "send_evening_image",
            "18:00",
            callback=self._schedule_evening_image,
        )
        LOG.info("ScheduledTasks 已加载，午间图片目标群: %s", self._group_list())

    def _schedule_noon_image(self) -> None:
        self._schedule_image(self._send_noon_image)

    def _schedule_evening_image(self) -> None:
        self._schedule_image(self._send_evening_image)

    def _schedule_image(self, callback) -> None:
        loop = getattr(self, "_main_loop", None)
        if loop is None or loop.is_closed():
            LOG.warning("ScheduledTasks 主事件循环不可用，跳过午间图片任务")
            return
        coroutine = callback()
        if loop.is_running():
            future = asyncio.run_coroutine_threadsafe(coroutine, loop)
            self._scheduled_futures.add(future)
            future.add_done_callback(self._scheduled_futures.discard)
        else:
            coroutine.close()

    async def on_close(self) -> None:
        for future in getattr(self, "_scheduled_futures", set()):
            future.cancel()
        getattr(self, "_scheduled_futures", set()).clear()

    def _group_list(self) -> list[str]:
        raw = self.get_config("group_list", [])
        if not isinstance(raw, list):
            return []
        return [str(group_id).strip() for group_id in raw if str(group_id).strip()]

    @staticmethod
    def _normalize_music_text(value: str) -> str:
        value = str(value or "").lower()
        return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", value)

    def _music_similarity(self, value: str) -> float:
        normalized = self._normalize_music_text(value)
        if not normalized:
            return 0.0
        scores = []
        for pattern in LYRIC_PATTERNS:
            target = self._normalize_music_text(pattern)
            if target in normalized:
                return 1.0
            window_lengths = range(
                max(1, len(target) - 4), min(len(normalized), len(target) + 4) + 1
            )
            scores.append(max(
                (
                    SequenceMatcher(None, normalized[start:start + length], target).ratio()
                    for length in window_lengths
                    for start in range(0, len(normalized) - length + 1)
                ),
                default=0.0,
            ))
        return max(scores, default=0.0)

    @staticmethod
    def _message_parts(event) -> tuple[str, bool]:
        text_parts: list[str] = []
        music_share = False
        for segment in event.message or []:
            text = getattr(segment, "text", None)
            if text:
                text_parts.append(str(text))
            data = getattr(segment, "data", None)
            if not data:
                continue
            try:
                payload = json.loads(data) if isinstance(data, str) else data
            except (TypeError, json.JSONDecodeError):
                continue
            music = payload.get("meta", {}).get("music", {}) if isinstance(payload, dict) else {}
            if isinstance(music, dict):
                title = str(music.get("title") or "")
                if MUSIC_TITLE.lower() in title.lower():
                    music_share = True
        return "".join(text_parts), music_share

    @registrar.qq.on_group_message(priority=80)
    async def handle_music_trigger(self, event) -> None:
        group_id = str(getattr(event, "group_id", ""))
        text, music_share = self._message_parts(event)
        if all(keyword in text for keyword in JIABAOYU_KEYWORDS):
            music_url = JIABAOYU_MUSIC_URL
            music_name = "贾宝玉触发曲"
        elif music_share or self._music_similarity(text) >= SIMILARITY_THRESHOLD:
            music_url = MUSIC_URL
            music_name = MUSIC_TITLE
        else:
            return
        now = time.monotonic()
        cooldown = max(0, int(self.get_config("music_cooldown", 30)))
        cooldown_key = f"{group_id}:{music_url}"
        if now - self._music_last_sent.get(cooldown_key, 0.0) < cooldown:
            return
        try:
            await self.api.qq.post_group_msg(
                group_id=group_id,
                rtf=MessageArray([Record(file=music_url)]),
            )
            self._music_last_sent[cooldown_key] = now
            LOG.info("检测到音乐触发词，已向群 %s 发送网易云%s", group_id, music_name)
        except Exception:
            LOG.exception("向群 %s 发送网易云%s失败", group_id, music_name)

    async def _send_noon_image(self) -> None:
        await self._send_random_image(IMAGE_PATH, "午间图片", 30)

    async def _send_evening_image(self) -> None:
        await self._send_random_image(EVENING_IMAGE_PATH, "晚间图片", 60)

    async def _send_random_image(self, image_path: Path, task_name: str, window_minutes: int) -> None:
        group_list = self._group_list()
        if not group_list:
            LOG.warning("ScheduledTasks 未配置%s目标群，跳过今日发送", task_name)
            return

        delay = random.uniform(0, window_minutes * 60)
        LOG.info("%s任务已触发，将在 %.0f 秒后发送", task_name, delay)
        await asyncio.sleep(delay)

        for group_id in group_list:
            try:
                await self.api.qq.send_group_image(group_id, str(image_path))
                LOG.info("%s已发送到群 %s", task_name, group_id)
            except Exception:
                LOG.exception("%s发送到群 %s 失败", task_name, group_id)
