from __future__ import annotations

import random
import sqlite3
import time
from datetime import date
from pathlib import Path

from ncatbot.core import registrar
from ncatbot.plugin import NcatBotPlugin

DEFAULT_BASE_STOLEN_TIERS = [
    {"min_balance": 1_000_000, "base": 20000},
    {"min_balance": 100_000, "base": 5000},
    {"min_balance": 10_000, "base": 1000},
    {"min_balance": 0, "base": 500},
]

ROBBERY_HELP = (
    "打劫说明：\n"
    "- 打劫 @对方 或 打劫 对方QQ\n"
    "- 双方钱包都要有 1000 金币以上；每天发起和被打劫各限一次。\n"
    "- 成功按对方余额档位拿钱，失败要赔给对方一部分。\n"
    "- 发「打劫帮助」随时看这条。"
)


class Robbery(NcatBotPlugin):
    name = "Robbery"
    version = "1.0.0"
    author = "NcatBot"
    description = "打劫群友钱包"

    async def on_load(self) -> None:
        self.init_defaults({
            "success_rate": 0.4,
            "victim_min_balance": 1000,
            "daily_limit": True,
            "stolen_balance_ratio": 0.025,
            "base_stolen_tiers": DEFAULT_BASE_STOLEN_TIERS,
            "max_stolen": 10000,
            "max_stolen_ratio": 0.10,
            "max_loot_balance_ratio": 1.0,
            "loot_cap_bypass_users": ["273421673", "635773721"],
            "guaranteed_failure_users": ["273421673", "635773721"],
            "guaranteed_failure_exempt_users": [],
            # Backend-only test profile. These fields are never included in user replies.
            "test_user_profiles": {
                "10123121": {
                    "is_test_account": True,
                    "victim_success_rate": 0.60,
                    "stolen_balance_ratio": 0.10,
                    "loss_stolen_ratio": 0.10,
                    "robbery_failure_rate": 0.75,
                },
            },
        })
        self._economy = self.get_plugin("GroupEconomy")
        if self._economy is None:
            raise RuntimeError("Robbery requires GroupEconomy")
        self._db = sqlite3.connect(str(Path(self.workspace) / "robbery.db"), check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS records (victim_id TEXT, robber_id TEXT, day TEXT, UNIQUE(victim_id, robber_id, day))")
        self._db.commit()

    async def on_close(self) -> None:
        if hasattr(self, "_db"):
            self._db.close()

    def _at_user(self, event) -> str | None:
        for segment in event.message or []:
            user_id = getattr(segment, "user_id", None)
            if user_id is not None:
                return str(user_id)
        return None

    def _used_today(self, robber_id: str, victim_id: str) -> tuple[bool, bool]:
        day = date.today().isoformat()
        rows = self._db.execute(
            "SELECT victim_id, robber_id FROM records WHERE day = ? AND (victim_id = ? OR robber_id = ?)",
            (day, victim_id, robber_id),
        ).fetchall()
        return any(str(row[0]) == victim_id for row in rows), any(str(row[1]) == robber_id for row in rows)

    def _record(self, victim_id: str, robber_id: str) -> None:
        self._db.execute(
            "INSERT OR IGNORE INTO records(victim_id, robber_id, day) VALUES (?, ?, ?)",
            (victim_id, robber_id, date.today().isoformat()),
        )
        self._db.commit()

    def _economy_plugin(self):
        """热重载 GroupEconomy 后刷新引用，避免继续使用已关闭的 store。"""
        economy = self.get_plugin("GroupEconomy")
        if economy is not None:
            self._economy = economy
        return self._economy

    def _test_profile(self, user_id: str) -> dict:
        profiles = self.get_config("test_user_profiles", {}) or {}
        profile = profiles.get(str(user_id), {})
        return profile if isinstance(profile, dict) and profile.get("is_test_account") else {}

    @staticmethod
    def _rate(value: object, default: float) -> float:
        try:
            return min(1.0, max(0.0, float(value)))
        except (TypeError, ValueError):
            return default

    @classmethod
    def _profile_rate(cls, profile: dict, key: str, default: float) -> float:
        return cls._rate(profile.get(key, default), default)

    @staticmethod
    def _ratio(value: object, default: float) -> float:
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _base_stolen_amount(victim_balance: int, tiers: object) -> int:
        balance = max(0, int(victim_balance))
        parsed: list[tuple[int, int]] = []
        for tier in tiers if isinstance(tiers, list) else []:
            if not isinstance(tier, dict):
                continue
            try:
                parsed.append((int(tier.get("min_balance", 0)), int(tier.get("base", 0))))
            except (TypeError, ValueError):
                continue
        for minimum, base in sorted(parsed, reverse=True):
            if balance >= minimum:
                return max(0, base)
        return 0

    @staticmethod
    def _robbery_amount(
        victim_balance: int,
        balance_ratio: float = 0.025,
        base_amount: int = 500,
        max_stolen: int = 10000,
        max_stolen_ratio: float = 0.10,
    ) -> int:
        balance = max(0, int(victim_balance))
        ratio_limit = int(balance * max(0.0, balance_ratio))
        ceiling = max(max(0, int(max_stolen)), int(balance * max(0.0, max_stolen_ratio)))
        return min(random.randint(0, ratio_limit) + max(0, int(base_amount)), ceiling)

    @staticmethod
    def _loss_amount(stolen: int, loss_ratio: float | None = None) -> int:
        if loss_ratio is None:
            loss_ratio = 1 / random.randint(1, 4)
        return max(1, int(stolen * min(1.0, max(0.0, loss_ratio))))

    @registrar.qq.on_group_message(priority=30)
    async def handle_message(self, event) -> None:
        text = (event.raw_message or "").strip()
        if not text.startswith("打劫"):
            return
        if text in ("打劫帮助", "打劫 帮助"):
            await event.reply(text=ROBBERY_HELP, at_sender=False)
            return
        victim_id = self._at_user(event)
        if victim_id is None:
            parts = text.split()
            victim_id = parts[1] if len(parts) > 1 and parts[1].isdigit() else None
        if not victim_id:
            await event.reply(text="用法：打劫 @对方 或 打劫 对方QQ")
            return
        robber_id = str(event.sender.user_id)
        if victim_id == robber_id:
            await event.reply(text="不能打劫自己")
            return
        used_victim, used_robber = self._used_today(robber_id, victim_id)
        if used_victim:
            await event.reply(text="对方今天已经被打劫了，给人家留点后路吧")
            return
        if used_robber:
            await event.reply(text="你今天已经成功打劫过了，贪心没有好果汁吃！")
            return
        economy_plugin = self._economy_plugin()
        if economy_plugin is None or not hasattr(economy_plugin, "store"):
            await event.reply(text="钱包系统暂不可用")
            return
        economy = economy_plugin.store
        robber_balance = int(economy.account(robber_id)["balance"])
        victim_balance = int(economy.account(victim_id)["balance"])
        minimum = max(1000, int(self.get_config("victim_min_balance", 1000)))
        if robber_balance < minimum:
            await event.reply(text="你的钱包余额不足1000金币，不能进行打劫")
            return
        if victim_balance < minimum:
            await event.reply(text="对方太穷了！打劫失败")
            return
        victim_profile = self._test_profile(victim_id)
        robber_profile = self._test_profile(robber_id)
        base_ratio = self._rate(self.get_config("stolen_balance_ratio", 0.025), 0.025)
        base_amount = self._base_stolen_amount(
            victim_balance,
            self.get_config("base_stolen_tiers", DEFAULT_BASE_STOLEN_TIERS),
        )
        stolen = self._robbery_amount(
            victim_balance,
            self._profile_rate(victim_profile, "stolen_balance_ratio", base_ratio),
            base_amount,
            self.get_config("max_stolen", 10000),
            self._rate(self.get_config("max_stolen_ratio", 0.10), 0.10),
        )
        loot_cap_users = {
            str(value)
            for value in (
                self.get_config("loot_cap_bypass_users", ["273421673", "635773721"]) or []
            )
        }
        loot_cap_ratio = self._ratio(self.get_config("max_loot_balance_ratio", 1.0), 1.0)
        capped = False
        if robber_id not in loot_cap_users and loot_cap_ratio > 0:
            loot_cap = int(robber_balance * loot_cap_ratio)
            if stolen > loot_cap:
                stolen = loot_cap
                capped = True
        protected = self.get_config(
            "guaranteed_failure_users", ["273421673", "635773721"]
        ) or []
        protected_users = {str(value) for value in protected}
        exempt_users = {
            str(value)
            for value in (self.get_config("guaranteed_failure_exempt_users", []) or [])
        }
        base_success_rate = self._rate(self.get_config("success_rate", 0.4), 0.4)
        success_rate = self._profile_rate(victim_profile, "victim_success_rate", base_success_rate)
        failure_rate = self._profile_rate(
            robber_profile, "robbery_failure_rate", 1.0 - success_rate
        )
        # 保护名单内的账号互相打劫时按正常概率结算；豁免名单临时解除受害者保护。
        forced_failure = (
            victim_id in protected_users
            and victim_id not in exempt_users
            and robber_id not in protected_users
        )
        if forced_failure or random.random() < failure_rate:
            compensation = min(random.randint(1, max(1, stolen // 2)), robber_balance)
            economy.transfer_balance(robber_id, victim_id, compensation, reason="robbery_failure")
            await event.reply(text=f"打劫失败，赔偿对方 {compensation} 金币")
            return
        loss = self._loss_amount(
            stolen,
            float(victim_profile["loss_stolen_ratio"])
            if "loss_stolen_ratio" in victim_profile
            else None,
        )
        economy.change_balance(victim_id, -loss, reason="robbery_loss", related_user_id=robber_id)
        economy.change_balance(robber_id, stolen, reason="robbery_reward", related_user_id=victim_id)
        self._record(victim_id, robber_id)
        suffix = "（受身家限制）" if capped else ""
        await event.reply(
            text=f"打劫成功，获得 {stolen} 金币；对方实际损失 {loss} 金币{suffix}"
        )
