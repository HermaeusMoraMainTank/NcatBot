"""Galgame 百宝盒 — 移植自 astrbot_plugin_galgame_box。

上游: https://github.com/PyuraMazo/astrbot_plugin_galgame_box
指令前缀统一为 ``gal``（不再使用 /旮旯）。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

from ncatbot.core import registrar
from ncatbot.event.qq import GroupMessageEvent as GroupMessage
from ncatbot.plugin import NcatBotPlugin
from ncatbot.types import MessageArray as MessageChain
from ncatbot.utils import get_log

_log = get_log()

PLUGIN_DIR = Path(__file__).resolve().parent

HELP_TEXT = """【Galgame 百宝盒】
指令前缀：gal

VNDB
  gal 作品 <名>     搜索作品（别名：游戏 / vn）
  gal 角色 <名>     搜索角色（别名：人物 / character）
  gal 厂商 <名>     搜索厂商（别名：作者 / producer）
  gal ID <VNDB ID>  按 ID 查询（别名：id）
  gal 简讯 [月-日]  今日发售/生日（别名：event）

TouchGal
  gal 随机          随机一部作品（别名：random）
  gal 推荐 <标签…>  按标签推荐（别名：标签 / recommend）
  gal 下载 <内容>   资源链接（别名：资源 / download）

AnimeTrace
  gal 出处 [图片链接]  角色识别（别名：识别 / find）
  可引用图片；无参数时等待下一张图

会话中可用：换一个 / 结束（推荐）
"""

DEFAULT_CONFIG: dict[str, Any] = {
    "basicSetting": {
        "requestTimeout": 5,
        "requestTime": 30,
        "sessionTimeout": 60,
        "cleanOnRestart": False,
        "enableFont": True,
        "forwardLimit": 10,
        "resultsLimit": False,
    },
    "safetySetting": {
        "enableNSFW": False,
        "proxy": "",
        "touchgalToken": "",
        "cfClearance": "",
        "tls": "chrome136",
    },
    "characterSetting": {
        "characterOptions": ["a-血型", "b-身高/体重", "c-性别（不剧透）"],
    },
    "producerSetting": {"producerVns": 9},
    "recommendSetting": {"recommendCache": 5},
    "findSetting": {"findResults": 3},
    "eventSetting": {"eventRating": 75},
    "scheduleSetting": {
        "pushTime": "07:00",
        "pushList": [],
        "scheduleContent": "c-投票数>50且评分最高（综合）",
        "genderFilter": "c-不筛选",
    },
}

# 子指令 → 规范化名
_SUB_ALIASES = {
    "作品": "vn",
    "游戏": "vn",
    "vn": "vn",
    "角色": "character",
    "人物": "character",
    "character": "character",
    "厂商": "producer",
    "作者": "producer",
    "producer": "producer",
    "id": "id",
    "ID": "id",
    "简讯": "event",
    "event": "event",
    "随机": "random",
    "random": "random",
    "推荐": "recommend",
    "标签": "recommend",
    "recommend": "recommend",
    "下载": "download",
    "资源": "download",
    "download": "download",
    "出处": "find",
    "识别": "find",
    "find": "find",
}


class GalgameBox(NcatBotPlugin):
    name = "GalgameBox"
    version = "1.0.0"

    async def on_load(self):
        from .compat import AstrBotConfig, StarTools, install_shim
        from .compat.html_renderer import set_output_dir
        from .compat.session_waiter import feed_session

        self._feed_session = feed_session
        install_shim(PLUGIN_DIR)
        set_output_dir(PLUGIN_DIR / "data" / "output")
        StarTools.set_data_dir(PLUGIN_DIR / "data")
        (PLUGIN_DIR / "data" / "cache").mkdir(parents=True, exist_ok=True)
        (PLUGIN_DIR / "data" / "output").mkdir(parents=True, exist_ok=True)

        # 延迟导入 core（依赖 shim）
        from .core.function.cache import Cache
        from .core.network import Downloader, Http
        from .core.services import Services

        self._Cache = Cache
        self._Downloader = Downloader
        self._Http = Http
        self._Services = Services

        cfg = self._build_config()
        self._astr_config = AstrBotConfig(cfg)
        await Services.initialize(self._astr_config)

        self._register_push()
        _log.info("[GalgameBox] 已加载，指令前缀 gal")

    async def on_close(self):
        try:
            await self._Services.get(self._Downloader).terminate()
            await self._Services.get(self._Http).terminate()
            await self._Services.get(self._Cache).terminate()
        except Exception as e:
            _log.warning("[GalgameBox] 卸载清理失败: %s", e)

    def _build_config(self) -> dict[str, Any]:
        """合并默认配置与 ConfigMixin 扁平/嵌套项。"""
        import copy

        cfg = copy.deepcopy(DEFAULT_CONFIG)
        for section, defaults in DEFAULT_CONFIG.items():
            raw = self.get_config(section, None)
            if isinstance(raw, dict):
                cfg[section] = {**defaults, **raw}
            else:
                # 允许扁平键如 sessionTimeout
                if isinstance(defaults, dict):
                    merged = dict(defaults)
                    for k, v in defaults.items():
                        flat = self.get_config(k, None)
                        if flat is not None:
                            merged[k] = flat
                    cfg[section] = merged
        return cfg

    def _register_push(self) -> None:
        schedule = self._astr_config.get("scheduleSetting", {})
        push_list = schedule.get("pushList") or []
        self._push_groups: list[int] = []
        for item in push_list:
            s = str(item)
            if "-" in s:
                s = s.rsplit("-", 1)[-1]
            if s.isdigit():
                self._push_groups.append(int(s))

        if not self._push_groups:
            _log.info("[GalgameBox] 推送白名单为空，跳过定时任务")
            return

        push_time = str(schedule.get("pushTime", "07:00")).replace("：", ":")
        try:
            hour, minute = map(int, push_time.split(":", 1))
            assert 0 <= hour <= 23 and 0 <= minute <= 59
        except Exception:
            _log.error("[GalgameBox] pushTime 格式错误，跳过定时任务")
            return

        ok = self.add_scheduled_task(
            "gal_daily_push",
            f"{hour:02d}:{minute:02d}",
            callback=self._push_today,
        )
        if ok:
            _log.info(
                "[GalgameBox] 已注册每日推送 %02d:%02d → %s 个群",
                hour,
                minute,
                len(self._push_groups),
            )

    async def _push_today(self):
        from .core.command import EventTimed
        from .compat.message_components import result_to_ncatbot_segments
        from .compat.message_components import MessageResult

        try:
            async for res in self._Services.get(EventTimed).goooooooooo():
                segs = result_to_ncatbot_segments(
                    MessageResult(kind="image", payload=res)
                    if isinstance(res, str)
                    else res
                )
                for gid in self._push_groups:
                    try:
                        await self.api.qq.post_group_msg(
                            group_id=gid, rtf=MessageChain(segs)
                        )
                    except Exception as e:
                        _log.warning("[GalgameBox] 推送到 %s 失败: %s", gid, e)
        except Exception as e:
            _log.exception("[GalgameBox] 每日推送失败: %s", e)

    def _parse_command(self, text: str) -> Optional[tuple[str, str]]:
        """返回 (subcommand, args)；仅 help 时 subcommand 为空串。"""
        t = text.strip()
        if t.startswith("/"):
            t = t[1:].lstrip()
        parts = t.split(None, 2)
        if not parts:
            return None
        head = parts[0]
        if head.lower() != "gal":
            return None
        if len(parts) == 1:
            return ("", "")
        sub_raw = parts[1]
        sub = _SUB_ALIASES.get(sub_raw) or _SUB_ALIASES.get(sub_raw.lower())
        if sub is None:
            # 未知子指令 → 帮助
            return ("", "")
        args = parts[2] if len(parts) > 2 else ""
        return (sub, args)

    @registrar.qq.on_group_message()
    async def handle(self, event: GroupMessage):
        from .compat.event import from_ncatbot
        from .compat.session_waiter import feed_session, get_active_session

        # 快速判断：非 gal 且无活跃会话则直接跳过，避免每条群消息都转换事件
        text = ""
        for segment in event.message:
            if hasattr(segment, "text") and segment.text and str(segment.text).strip():
                text = str(segment.text).strip()
                break
        if not text:
            text = re.sub(r"\[CQ:[^\]]+\]", "", event.raw_message or "").strip()

        sid = f"qq:{event.group_id}:{event.sender.user_id}"
        has_session = get_active_session(sid) is not None
        parsed = self._parse_command(text)
        if parsed is None and not has_session:
            return

        astr_event = await from_ncatbot(event, self.api)
        if await feed_session(sid, astr_event):
            return

        if parsed is None:
            return

        sub, args = parsed
        if not sub:
            await event.reply(text=HELP_TEXT, at_sender=False)
            return

        await self._dispatch(astr_event, event, sub, args)

    async def _dispatch(
        self, astr_event: Any, native: GroupMessage, sub: str, args: str
    ) -> None:
        from .core.command import (
            Character,
            Download,
            Event,
            Find,
            Producer,
            Random,
            Recommend,
            Vn,
            VndbId,
        )
        from .core.type.exceptions import EarlyReturn, Tips
        from .compat.message_components import result_to_ncatbot_segments

        S = self._Services
        try:
            if sub == "vn":
                if not args.strip():
                    await native.reply(text="用法：gal 作品 <作品名>", at_sender=False)
                    return
                gen = S.get(Vn).goooooooooo(astr_event, args.strip())
            elif sub == "character":
                if not args.strip():
                    await native.reply(text="用法：gal 角色 <角色名>", at_sender=False)
                    return
                gen = S.get(Character).goooooooooo(astr_event, args.strip())
            elif sub == "producer":
                if not args.strip():
                    await native.reply(text="用法：gal 厂商 <厂商名>", at_sender=False)
                    return
                gen = S.get(Producer).goooooooooo(astr_event, args.strip())
            elif sub == "id":
                if not args.strip():
                    await native.reply(text="用法：gal ID <VNDB ID>", at_sender=False)
                    return
                gen = S.get(VndbId).goooooooooo(astr_event, args.strip())
            elif sub == "event":
                gen = S.get(Event).goooooooooo(astr_event, args.strip())
            elif sub == "random":
                gen = S.get(Random).goooooooooo(astr_event)
            elif sub == "recommend":
                if not args.strip():
                    await native.reply(
                        text="用法：gal 推荐 <标签…>（多个标签空格分隔）",
                        at_sender=False,
                    )
                    return
                gen = S.get(Recommend).goooooooooo(astr_event, args.strip())
            elif sub == "download":
                if not args.strip():
                    await native.reply(
                        text="用法：gal 下载 <ID/关键词>", at_sender=False
                    )
                    return
                gen = S.get(Download).goooooooooo(astr_event, args.strip())
            elif sub == "find":
                gen = S.get(Find).goooooooooo(astr_event, args.strip())
            else:
                await native.reply(text=HELP_TEXT, at_sender=False)
                return

            async for res in gen:
                segs = result_to_ncatbot_segments(res)
                if segs:
                    await self.api.qq.post_group_msg(
                        group_id=native.group_id, rtf=MessageChain(segs)
                    )
        except EarlyReturn:
            return
        except Tips as e:
            msg = str(e).split("：")[0]
            await native.reply(text=msg, at_sender=False)
        except Exception as e:
            _log.exception("[GalgameBox] 指令失败: %s", e)
            msg = "发生非预期异常！"
            if isinstance(e, RuntimeError) and "endpoints failed" in str(e):
                msg = "图片渲染失败！请确认已安装 playwright 并执行 playwright install chromium"
            await native.reply(text=msg, at_sender=False)
