from __future__ import annotations

import io
import random
import re
from datetime import date
from pathlib import Path

import httpx
from PIL import Image as PILImage

from common.utils.CommonUtil import CommonUtil
from common.utils.plugin_commands import format_help, is_help_message
from ncatbot.core import registrar
from ncatbot.event.qq import GroupMessageEvent as GroupMessage
from ncatbot.plugin import NcatBotPlugin
from ncatbot.types import At, Image, MessageArray

from .config import DEFAULT_CONFIG, level_for_score
from .render import (
    cache_is_fresh,
    cache_key,
    render_ranking,
    render_sign_card,
    render_wallet_card,
)
from .store import EconomyStore

HELP = format_help(
    "GroupEconomy 经济系统",
    [
        "经济帮助 / 钱包帮助 / 签到帮助：查看完整使用说明",
        "签到：每日领取钱包和经验，返回签到图片",
        "我的钱包 / 查看我的钱包：查询余额和等级",
        "查看等级排名：跨群经验排名",
        "查看钱包排名：钱包排名",
        "买礼物给 / 送礼物给 @用户 [金额]：随机扣除 1~30，提升老婆好感",
    ],
)


class GroupEconomy(NcatBotPlugin):
    name = "GroupEconomy"
    version = "1.0.0"
    description = "签到、钱包、跨群等级和礼物好感经济系统"

    async def on_load(self) -> None:
        self.init_defaults(DEFAULT_CONFIG)
        self.data_dir = Path(self.workspace) / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir = self.data_dir / "cache"
        self.card_dir = self.data_dir / "cards"
        self.store = EconomyStore(self.data_dir / "economy.db")

    async def on_close(self) -> None:
        self.store.close()

    def cfg(self, key: str):
        return self.get_config(key, DEFAULT_CONFIG[key])

    def _at_user(self, event) -> str | None:
        for segment in event.message or []:
            if isinstance(segment, At) and str(segment.user_id) != str(getattr(event, "self_id", "")):
                return str(segment.user_id)
        return None

    async def _target_in_group(self, event, target_id: str) -> bool | None:
        """校验送礼目标；查询失败时返回 None，让交易仍可降级执行。"""
        try:
            response = await self.api.qq.query.get_group_member_list(
                group_id=event.group_id
            )
            members = CommonUtil.parse_group_member_list(response)
            return any(str(member.user_id) == str(target_id) for member in members)
        except Exception:
            return None

    async def _background(self) -> str | None:
        url = str(self.cfg("background_url") or "").strip()
        if not url:
            return None
        path = self.cache_dir / f"background_{cache_key(url)}.jpg"
        if cache_is_fresh(path, int(self.cfg("background_cache_hours") or 24)):
            return str(path)

        async def download() -> bytes | None:
            try:
                async with httpx.AsyncClient(timeout=float(self.cfg("background_timeout") or 10), follow_redirects=True) as client:
                    response = await client.get(url)
                    response.raise_for_status()
                    return response.content
            except Exception:
                return None

        data = await download()
        if not data:
            return str(path) if path.exists() else None
        try:
            PILImage.open(io.BytesIO(data)).convert("RGB").save(path, "JPEG")
            return str(path)
        except Exception:
            return str(path) if path.exists() else None

    async def _send_card(self, event, result: dict) -> None:
        user_id = str(event.sender.user_id)
        account = self.store.account(user_id)
        rank = self.store.global_rank(user_id)
        avatar = await CommonUtil.get_avatar_async(user_id)
        background = await self._background()
        output = self.card_dir / f"signin_{user_id}_{date.today().isoformat()}.png"
        render_sign_card(
            background_path=background,
            avatar_path=avatar,
            nickname=str(account.get("nickname") or event.sender.nickname or user_id),
            reward=int(result.get("reward", 0)),
            balance=int(account["balance"]),
            score=int(account["score"]),
            level=level_for_score(int(account["score"])),
            rank=rank,
            streak=int(result.get("streak", 1)),
            output_path=output,
        )
        await self.api.qq.post_group_msg(group_id=event.group_id, rtf=MessageArray([Image(file=str(output))]))

    async def _send_wallet_card(
        self,
        event,
        *,
        title: str,
        main_value: str,
        main_label: str,
        notice: str,
    ) -> None:
        user_id = str(event.sender.user_id)
        account = self.store.account(user_id)
        rank = self.store.global_rank(user_id)
        last = self.store.last_checkin(user_id)
        avatar = await CommonUtil.get_avatar_async(user_id)
        background = await self._background()
        output = self.card_dir / f"wallet_{user_id}_{date.today().isoformat()}.png"
        render_wallet_card(
            background_path=background,
            avatar_path=avatar,
            nickname=str(account.get("nickname") or event.sender.nickname or user_id),
            balance=int(account["balance"]),
            score=int(account["score"]),
            level=level_for_score(int(account["score"])),
            rank=rank,
            streak=int(last.get("streak", 0)) if last else 0,
            total_earned=int(account["total_earned"]),
            total_spent=int(account["total_spent"]),
            title=title,
            main_value=main_value,
            main_label=main_label,
            notice=notice,
            output_path=output,
        )
        await self.api.qq.post_group_msg(
            group_id=event.group_id,
            rtf=MessageArray([Image(file=str(output))]),
        )

    async def _checkin(self, event) -> None:
        low = max(0, int(self.cfg("checkin_min_reward") or 10))
        high = max(low, int(self.cfg("checkin_max_reward") or 30))
        account = self.store.account(str(event.sender.user_id))
        level = level_for_score(int(account["score"]))
        reward = random.randint(low + level * 5, high + level * 5)
        result = self.store.checkin(str(event.sender.user_id), str(event.sender.nickname or ""), reward)
        if not result["ok"]:
            last = self.store.last_checkin(str(event.sender.user_id))
            last_reward = int(last.get("reward", 0)) if last else 0
            streak = int(last.get("streak", 0)) if last else 0
            await self._send_wallet_card(
                event,
                title="今日已签到",
                main_value="已签到",
                main_label="签到状态",
                notice=f"今日奖励 +{last_reward} · 连续签到 {streak} 天",
            )
            return
        await self._send_card(event, result)

    async def _gift(self, event) -> None:
        target_id = self._at_user(event)
        sender_id = str(event.sender.user_id)
        if not target_id:
            await event.reply(text="用法：买礼物给 @用户 [金额]")
            return
        if target_id == sender_id:
            await event.reply(text="不能给自己买礼物哦")
            return
        in_group = await self._target_in_group(event, target_id)
        if in_group is not True:
            message = (
                "找不到对方，礼物只能送给当前群成员"
                if in_group is False
                else "暂时无法确认对方是否在群内，请稍后再试"
            )
            await event.reply(text=message)
            return
        cfg_min = max(1, int(self.cfg("gift_min_amount") or 1))
        cfg_max = max(cfg_min, int(self.cfg("gift_max_amount") or 100))
        reserve_value = self.cfg("gift_wallet_reserve")
        reserve = max(0, int(reserve_value if reserve_value is not None else 0))
        unlimited_users = self.cfg("gift_unlimited_users") or []
        if isinstance(unlimited_users, (str, int)):
            unlimited_users = [unlimited_users]
        unlimited = sender_id in {str(value) for value in unlimited_users}
        balance = int(self.store.account(sender_id)["balance"])
        upper = min(cfg_max, balance - reserve)
        if upper < cfg_min:
            await event.reply(text=f"余额不足，送礼后需要至少保留 {reserve} 钱包余额")
            return
        text_parts = [
            str(getattr(segment, "text", "") or "")
            for segment in event.message or []
            if getattr(segment, "text", None)
        ]
        numbers = [int(value) for value in re.findall(r"\d+", " ".join(text_parts))]
        amount = numbers[-1] if numbers else random.randint(cfg_min, upper)
        if amount < cfg_min or amount > upper:
            await event.reply(text=f"礼物金额必须在 {cfg_min}~{upper} 之间，且不会扣光余额")
            return
        mood_max = 2
        existing_favor = 0
        waifu = self.get_plugin("TodayWaifu")
        if waifu and hasattr(waifu, "get_favor"):
            existing_favor = waifu.get_favor(str(event.group_id), sender_id, target_id)
        if existing_favor > 50:
            favor_delta = amount % 10
        else:
            mood_max = 5
            favor_delta = random.randint(1, amount)
        mood = "不喜欢" if random.randrange(mood_max) == 0 else "喜欢"
        if mood == "不喜欢":
            favor_delta = -favor_delta
        favor_delta = max(
            int(self.cfg("gift_favor_min") or -20),
            min(int(self.cfg("gift_favor_max") or 100), favor_delta),
        )
        result = self.store.gift(
            str(event.group_id), sender_id, target_id, amount, reserve,
            favor_delta, mood, enforce_daily_limit=not unlimited,
        )
        if not result["ok"]:
            message = "今天已经送过礼物了" if result.get("reason") == "daily" else "余额不足"
            await event.reply(text=message)
            return
        if waifu and hasattr(waifu, "change_favor"):
            favor = waifu.change_favor(str(event.group_id), sender_id, target_id, favor_delta)
            favor_text = f"当前好感度 {favor}"
        else:
            favor_text = f"好感变化 {favor_delta:+d}"
        await event.reply(text=f"你花了 {amount} 钱买礼物送给 {target_id}，对方{mood}，{favor_text}，余额 {result['balance']}")

    async def _ranking(self, event, *, wallet: bool = False) -> None:
        rows = self.store.wallet_ranking(int(self.cfg("ranking_limit") or 10)) if wallet else self.store.global_ranking(int(self.cfg("ranking_limit") or 10))
        for row in rows:
            row["level"] = level_for_score(int(row["score"]))
            row["avatar_path"] = await CommonUtil.get_avatar_async(str(row["user_id"]))
        output = self.card_dir / ("global_wallet_ranking.png" if wallet else "global_level_ranking.png")
        render_ranking(
            rows,
            output,
            wallet=wallet,
            title="金币财富榜" if wallet else "跨群等级榜",
        )
        await self.api.qq.post_group_msg(group_id=event.group_id, rtf=MessageArray([Image(file=str(output))]))

    @registrar.qq.on_group_message(priority=35)
    async def handle_message(self, event: GroupMessage) -> None:
        text = (event.raw_message or "").strip()
        if text in ("经济帮助", "钱包帮助", "签到帮助") or is_help_message(
            text,
            command_names=(
                "签到",
                "买礼物给",
                "送礼物给",
                "我的钱包",
                "查看我的钱包",
                "查看等级排名",
                "查看钱包排名",
            ),
        ):
            await event.reply(text=HELP, at_sender=False)
        elif text == "签到":
            await self._checkin(event)
        elif text in ("我的钱包", "查看我的钱包", "查看钱包余额"):
            account = self.store.account(str(event.sender.user_id))
            await self._send_wallet_card(
                event,
                title="我的钱包",
                main_value=str(account["balance"]),
                main_label="钱包余额",
                notice=f"累计收入 {account['total_earned']} · 累计支出 {account['total_spent']}",
            )
        elif text == "查看等级排名":
            await self._ranking(event)
        elif text == "查看钱包排名":
            await self._ranking(event, wallet=True)
        elif text.startswith(("买礼物给", "送礼物给")):
            await self._gift(event)
