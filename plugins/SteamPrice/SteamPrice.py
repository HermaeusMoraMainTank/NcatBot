"""Steam 价格查询（小黑盒）— NcatBot 插件。

上游来源（同步更新对照）：
https://github.com/penguin-madagascar/astrbot_plugin_steam_price_heybox
"""

from __future__ import annotations

import logging

import httpx
from ncatbot.core import non_self, registrar
from ncatbot.event.qq import GroupMessageEvent, PrivateMessageEvent
from ncatbot.plugin import NcatBotPlugin

from .steam_price import PriceLookupError, SteamPriceService

_log = logging.getLogger("SteamPrice")

COMMAND_PREFIXES = ("steam价格", "小黑盒查价", "steam查价")

DEFAULT_CONFIG = {
    "timeout_seconds": 15,
    "default_country": "CN",
    "default_language": "schinese",
    "default_history_country": "CN",
    "history_days": 720,
    "history_event_limit": 5,
    "global_price_limit": 10,
    "show_api_links": False,
    "llm_name_retry_count": 3,
}


class SteamPrice(NcatBotPlugin):
    name = "SteamPrice"
    version = "1.0.0"

    async def on_load(self):
        self.init_defaults(DEFAULT_CONFIG)
        timeout = float(self.get_config("timeout_seconds", 15))
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json,text/plain,*/*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        )
        self._service = SteamPriceService.from_config(
            dict(self.config),
            self._http,
            name_corrector=self._make_name_corrector(),
        )
        _log.info(
            "SteamPrice 插件加载完成 v%s（LLM 校正: AiUtil DeepSeek）",
            self.version,
        )

    async def on_close(self):
        http = getattr(self, "_http", None)
        if http is not None:
            await http.aclose()

    def _make_name_corrector(self):
        """用 AiUtil.search_deepseek 做游戏名 / 未知中文地区校正。"""
        from common.utils.AiUtil import AiUtil

        from .name_correction import (
            CORRECTION_SYSTEM_PROMPT,
            NameCorrection,
            NameCorrectionRequest,
            build_correction_prompt,
            parse_correction_response,
        )

        async def correct(request: NameCorrectionRequest) -> NameCorrection | None:
            try:
                result = await AiUtil.search_deepseek(
                    keyword=build_correction_prompt(request),
                    prompt=CORRECTION_SYSTEM_PROMPT,
                )
            except Exception as exc:
                _log.warning("Steam 游戏名 LLM 校正失败: %s", exc)
                return None
            if not result or not result.get("content"):
                _log.warning("Steam 游戏名 LLM 校正无内容")
                return None
            correction = parse_correction_response(str(result["content"]))
            if correction is None:
                _log.warning("Steam 游戏名 LLM 校正 JSON 无效")
            return correction

        return correct

    def _match_prefix(self, raw: str) -> str | None:
        for prefix in COMMAND_PREFIXES:
            if raw == prefix or raw.startswith(prefix + " "):
                return prefix
        return None

    async def _reply(
        self, event: GroupMessageEvent | PrivateMessageEvent, text: str
    ) -> None:
        if isinstance(event, GroupMessageEvent):
            await event.reply(text=text, at_sender=False)
        else:
            uid = str(
                getattr(event, "user_id", None) or getattr(event.sender, "user_id", "")
            )
            await self.api.qq.post_private_msg(user_id=uid, text=text)

    @non_self
    @registrar.on_message()
    async def dispatch(self, event: GroupMessageEvent | PrivateMessageEvent):
        if not isinstance(event, (GroupMessageEvent, PrivateMessageEvent)):
            return
        raw = (event.raw_message or "").strip()
        if not raw:
            return
        prefix = self._match_prefix(raw)
        if prefix is None:
            return

        query = raw[len(prefix) :].strip()
        try:
            messages = await self._service.execute(query)
        except PriceLookupError as exc:
            await self._reply(event, str(exc))
            return
        except Exception as exc:
            _log.exception("Steam 价格查询失败")
            await self._reply(event, f"Steam 价格查询失败：{exc}")
            return

        for message in messages:
            await self._reply(event, message)
