from __future__ import annotations

import asyncio
import json
import random
import re
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from PIL import Image as PILImage
from PIL import ImageDraw, ImageFont
from ncatbot.core import registrar
from ncatbot.plugin import NcatBotPlugin
from ncatbot.types import At, Image, MessageArray, PlainText
from ncatbot.utils import get_log
from common.utils.CommonUtil import CommonUtil

from .mcfish_rules import (
    EQUIPMENT,
    FISH_KINGS,
    HIDDEN_FISH,
    FishKingWindow,
    active_window,
    daily_attempt_limit,
    daily_fishing_zone,
    fishing_zone_label,
    fish_zone_weights,
    fish_king_probabilities,
    fishing_xp,
    fishing_wait_for_result,
    format_duration,
    bite_skill_from_reply,
    hook_skill_bonus,
    hook_skill_from_reply,
    fragment_roll,
    fragment_jackpot,
    generate_week_windows,
    king_success_rate,
    king_bite_rate,
    level_for_xp,
    xp_required_for_level,
    rod_unlock_level,
    hidden_fish_trigger,
    path_progress,
    parse_count_token,
    treasure_sale_price,
)


LOG = get_log("McFish")


class _McFishEvent:
    """Keep the upstream message style without NcatBot's default sender mention."""

    def __init__(self, event):
        self._event = event

    def __getattr__(self, name):
        return getattr(self._event, name)

    async def reply(self, *args, **kwargs):
        kwargs.setdefault("at_sender", False)
        return await self._event.reply(*args, **kwargs)


POLES = {
    "木竿": (30, 100),
    "铁竿": (50, 300),
    "金竿": (70, 700),
    "钻石竿": (100, 1500),
    "下界合金竿": (150, 3100),
    "三叉戟": (301, 4000),
}
POLE_ALIASES = {
    "木杆": "木竿",
    "铁杆": "铁竿",
    "金杆": "金竿",
    "钻石杆": "钻石竿",
    "下界合金杆": "下界合金竿",
}
FISH = {
    "鳕鱼": 10, "鲑鱼": 50, "热带鱼": 100, "河豚": 300, "鹦鹉螺": 500, "墨鱼": 500,
    "鲤鱼": 80, "鳗鱼": 180, "水晶虾": 260, "深海鱼": 320, "银鱼": 400, "金鳞鱼": 600,
}
ITEMS = {"诱钓": 1000, "海之眷顾": 2500, "唱片": 3000, "美西螈": 3000, "海豚": 1000, "净化书": 5000, "宝藏诅咒": 0, "垃圾": 10}
SHOP_EQUIPMENT_PRICES = {"魔法鱼饵": 1800, "隐秘诱饵": 2600, "万能鱼饵": 3200}
SHOP_EQUIPMENT_STOCK = {"魔法鱼饵": 5, "隐秘诱饵": 3, "万能鱼饵": 2}
SHOP_WOOD_PRICE = 180
AXOLOTL = "美西螈"
UPGRADES = {"铁竿": "木竿", "金竿": "铁竿", "钻石竿": "金竿", "下界合金竿": "钻石竿", "三叉戟": "下界合金竿"}

MCFISH_HELP = """一款钓鱼模拟器
----------指令----------
- 钓鱼看板 / 钓鱼商店 / 钓鱼背包
- 钓鱼时钟（只在查询时发送图片，显示当前和最近5次鱼王窗口，不主动通知）
- 钓鱼图鉴 或 钓鱼图鉴 鱼名
- 查看上次钓鱼结果
- 购买xxx [数量]
- 出售xxx [数量]，可一次出售多个物品
- 出售所有垃圾
- 装备[xx竿|三叉戟|美西螈|永久饰品|消耗品]
- 附魔[诱钓|海之眷顾]
- 修复鱼竿
- 合成[xx竿|三叉戟|七件新装备]
- 消除[绑定|宝藏]诅咒 [数量]
- 进行钓鱼 / 进行n次钓鱼（支持“进行十次钓鱼”等中文数量）
- 当前装备概率明细 / 查看钓鱼规则

规则V6.0:
1. 钓鱼规则
-> 每次钓鱼按一竿独立结算；进行n次钓鱼会立即生成并保存结果，再按隐藏上钩时间延时发送汇总，不会把n次结果视为同一时刻。
-> 同一玩家已有钓鱼任务时再次发送钓鱼，会提示“你正在钓鱼，请稍后再试”；其他玩家不受影响。
-> 钓鱼经验用于升级：1→2级需要100经验，之后每级升级所需经验增加50；每日总次数按等级为50/60/70/80/100次，单次指令最多进行10次，鱼竿按等级逐级解锁。
-> 不同鱼种经验不同：普通鱼5–50点，隐藏鱼80–120点，鱼王成功300–900点；鱼王脱钩不获得捕获经验。
-> 每竿保存自己的虚拟时间、鱼竿、鱼饵、鱼王窗口和结果类型；只有虚拟时间落在鱼王窗口内才有鱼王机会。
-> 钓鱼结束时会逐竿显示隐藏上钩时间、结果和该竿获得的经验，并同时显示本次经验总计。
-> 普通鱼直接结算。鱼王分为咬钩阶段和上钩阶段：前者决定保留、牺牲当前鱼王尝试后置鱼王或放弃，后者使用精准提竿、强力提竿、耐心控鱼、爆发收线四种技能。
-> 没有鱼竿且余额不足时，每3天最多补偿1根木竿；有鱼竿时必须先装备，不能重复领取补偿。无装备且有余额时可花费100购买鱼竿，最多可以购买3次。

2. 鱼竿
-> 木竿：耐久30，金币商店常驻价180；铁竿：耐久50，均价300；金竿：耐久70，均价700。
-> 钻石竿：耐久100，均价1500；下界合金竿：耐久150，均价3100。
-> 三叉戟：耐久301，可使1次钓鱼视为3竿，只能合成、修复和交易。
-> 三叉戟 : 可使1次钓鱼视为3次钓鱼。耐久上限:301，只能合成、修复和交易。
-> 合成鱼竿需要3根上一等级鱼竿，合成成功率90%(包括梭哈)。

3. 新装备：只能用钓鱼碎片合成，不能直接从钓鱼结果获得
-> 财富浮标：永久，120碎片。宝藏概率区间乘1.35，鱼王咬钩率乘1.20。
-> 坚韧钓组：永久，160碎片。鱼王上钩成功率增加12个百分点，失败率最低保留5%。
-> 黄金磁石：永久，220碎片。宝藏品质提高1档，宝藏出售价格乘1.25。
-> 幸运海星：永久，300碎片。普通碎片概率由8%提高至16%，不改变鱼王碎片数量。
-> 魔法鱼饵：消耗品，24碎片，10竿；金币商店价1800。每竿等待时间乘0.85，稀有普通鱼权重乘1.25。
-> 隐秘诱饵：消耗品，42碎片，6竿；金币商店价2600。仅在对应鱼点判定隐藏鱼，触发率乘1.50。
-> 万能鱼饵：消耗品，50碎片，5竿；金币商店价3200。每竿可兜底一个普通鱼点/鱼饵限制，不影响鱼王窗口、前置状态和鱼竿门槛。
-> 永久饰品最多同时装备2件；消耗品按实际完成的每一竿扣除1次。

4. 碎片
-> 普通鱼：基础8%获得1–2个；连续12次未获得时第13次必得。
-> 稀有鱼或宝藏：22%获得2–4个，并重置个人保底计数。
-> 鱼王成功：必得5–9个。
-> 每竿另有0.1%概率触发碎片宝藏，额外获得50个碎片，并显示特殊提示。
-> 碎片只用于合成装备和后续图鉴/任务奖励，不能直接钓出新装备。

5. 鱼王时钟
-> 鱼王每周生成非整点、短时长窗口，全部位于北京时间09:00–21:00；每组前置鱼王和后置鱼王窗口会部分重合。
-> 窗口每周重新生成并持久化，不是固定整点，也不会创建定时任务或主动群通知。
-> 发送“钓鱼时钟”查看当前环境、当前鱼王和最近5次鱼王窗口；不会显示钓鱼方法或可能鱼种。

6. 图鉴、鱼类和旧规则
-> “钓鱼图鉴”显示捕获数量、占比、首次捕获、鱼王咬钩/成功/脱钩统计及长度记录。
-> 普通鱼包括鳕鱼、鲑鱼、热带鱼、河豚、鹦鹉螺、墨鱼；隐藏鱼包括幽光鳐、星砂鳗、云潮鲨。
-> 诱钓和海之眷顾仍保留为旧鱼竿附魔兼容项，每级最高3级；宝藏诅咒无法交易，可用净化书消除。
-> 鱼竿数量大于50不能购买，大于30不能钓鱼；每天最多交易鱼竿10把、购买物品30件（垃圾除外）。"""


class McFish(NcatBotPlugin):
    name = "McFish"
    version = "1.0.0"
    author = "NcatBot"
    description = "钓鱼模拟器"
    MAX_FISH_BATCH = 10

    async def on_load(self) -> None:
        self.init_defaults({"fish_limit": 50, "success_rate": 0.6, "starting_pole_price": 100, "sell_tax": 0.8})
        self._economy = self.get_plugin("GroupEconomy")
        if self._economy is None:
            raise RuntimeError("McFish requires GroupEconomy")
        self._db = sqlite3.connect(str(Path(self.workspace) / "mcfish.db"), check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS players (user_id TEXT PRIMARY KEY, state TEXT NOT NULL, updated_at REAL NOT NULL)")
        self._db.execute("CREATE TABLE IF NOT EXISTS shop (day TEXT PRIMARY KEY, discounts TEXT NOT NULL, inventory TEXT NOT NULL)")
        self._db.execute("CREATE TABLE IF NOT EXISTS admin_adjustments (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, reason TEXT NOT NULL, old_state TEXT NOT NULL, new_state TEXT NOT NULL, created_at REAL NOT NULL)")
        self._db.execute("CREATE TABLE IF NOT EXISTS migrations (migration_id TEXT PRIMARY KEY, applied_at REAL NOT NULL, details TEXT NOT NULL)")
        self._db.execute("CREATE TABLE IF NOT EXISTS fish_windows (week_start TEXT NOT NULL, chain TEXT NOT NULL, king_name TEXT NOT NULL, starts_at TEXT NOT NULL, ends_at TEXT NOT NULL, PRIMARY KEY (week_start, king_name))")
        self._db.execute("CREATE TABLE IF NOT EXISTS fishing_attempts (user_id TEXT NOT NULL, attempt_index INTEGER NOT NULL, scheduled_at TEXT NOT NULL, rod_id TEXT NOT NULL, bait_id TEXT NOT NULL, active_window_id TEXT, result_type TEXT NOT NULL, created_at REAL NOT NULL)")
        self._db.commit()
        self._user_locks: dict[str, asyncio.Lock] = {}
        self._fishing_tasks: dict[str, asyncio.Task] = {}
        self._load_original_data()
        self._ensure_fish_windows()
        self._repair_abnormal_pole_trades()
        self._repair_abnormal_pole_inventory()
        self._repair_abnormal_equipment()
        self._reset_fishing_progress_for_level_release()

    async def on_close(self) -> None:
        for task in getattr(self, "_fishing_tasks", {}).values():
            if not task.done():
                task.cancel()
        self._fishing_tasks = {}
        if hasattr(self, "_db"):
            self._db.close()

    def _state(self, user_id: str) -> dict:
        row = self._db.execute("SELECT state FROM players WHERE user_id = ?", (str(user_id),)).fetchone()
        state = json.loads(row[0]) if row else {}
        state.setdefault("pack", {})
        for old_name, canonical_name in POLE_ALIASES.items():
            if old_name in state["pack"]:
                value = state["pack"].pop(old_name)
                state["pack"].setdefault(canonical_name, [])
                state["pack"][canonical_name].extend(
                    value if isinstance(value, list) else self._shop_poles(value, canonical_name)
                )
        if state.get("equip") and state["equip"].get("name") in POLE_ALIASES:
            state["equip"]["name"] = POLE_ALIASES[state["equip"]["name"]]
        state.setdefault("fish_today", 0)
        state.setdefault("fish_xp", 0)
        state.setdefault("fish_level", max(1, level_for_xp(int(state.get("fish_xp", 0)))))
        state.setdefault("fish_bless", 0)
        state.setdefault("fish_day", date.today().isoformat())
        state.setdefault("equip", None)
        state.setdefault("king_paths", {})
        state.setdefault("king_stats", {})
        state.setdefault("fish_stats", {})
        state.setdefault("fragment_misses", 0)
        state.setdefault("accessories", [])
        state.setdefault("active_consumables", {})
        state.setdefault("trade_day", date.today().isoformat())
        state.setdefault("trade_count", 0)
        state.setdefault("pole_trade_count", 0)
        state.setdefault("item_buy_count", 0)
        state.setdefault("half_price_count", 0)
        today = date.today().isoformat()
        state.setdefault("fishing_zone_day", today)
        state.setdefault("fishing_zone", daily_fishing_zone())
        state.setdefault("buy_times", 0)
        state.setdefault("emergency_pole_day", "")
        state.setdefault("emergency_pole_count", 1 if state.get("emergency_pole_day") else 0)
        state.setdefault("starter_pole_day", "")
        state.setdefault("last_fishing_result", None)
        if state["fish_day"] != today:
            state["fish_day"], state["fish_today"] = today, 0
            state["fishing_zone"] = daily_fishing_zone()
        if state["fishing_zone_day"] != today:
            state["fishing_zone_day"], state["fishing_zone"] = today, daily_fishing_zone()
        if state["trade_day"] != date.today().isoformat():
            state["trade_day"], state["trade_count"] = date.today().isoformat(), 0
            state["pole_trade_count"], state["item_buy_count"] = 0, 0
        return state

    def _ensure_fish_windows(self) -> None:
        """Persist one irregular schedule per week; this method never sends messages."""
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today - timedelta(days=today.weekday())
        for schedule_start in (week_start, week_start + timedelta(days=7)):
            key = schedule_start.date().isoformat()
            if self._db.execute("SELECT 1 FROM fish_windows WHERE week_start = ? LIMIT 1", (key,)).fetchone():
                continue
            for window in generate_week_windows(schedule_start, seed=schedule_start.toordinal()):
                self._db.execute(
                    "INSERT INTO fish_windows VALUES (?, ?, ?, ?, ?)",
                    (key, window.chain, window.king_name, window.starts_at.isoformat(), window.ends_at.isoformat()),
                )
        self._db.commit()

    def _reset_fishing_progress_for_level_release(self) -> None:
        """Initialize the level release once and normalize equipped rods."""
        migration_id = "mcfish-level-release-reset-2026-09-07-v2"
        if self._db.execute("SELECT 1 FROM migrations WHERE migration_id = ?", (migration_id,)).fetchone():
            return
        today = date.today().isoformat()
        rows = self._db.execute("SELECT user_id, state FROM players").fetchall()
        for user_id, raw_state in rows:
            state = json.loads(raw_state)
            state.setdefault("pack", {})
            equip = state.get("equip") or {}
            equip_name = equip.get("name")
            if equip_name and equip_name != "木竿":
                if equip_name in POLES:
                    state["pack"].setdefault(equip_name, []).append(dict(equip))
                elif equip_name == AXOLOTL:
                    state["pack"][equip_name] = int(state["pack"].get(equip_name, 0)) + 1
                state["equip"] = {"name": "木竿", "durable": 30, "maintenance": 0, "induce": 0, "favor": 0}
            state["fish_xp"] = 0
            state["fish_level"] = 1
            state["fish_today"] = 0
            state["fish_day"] = today
            self._db.execute(
                "UPDATE players SET state = ?, updated_at = ? WHERE user_id = ?",
                (json.dumps(state, ensure_ascii=False), time.time(), str(user_id)),
            )
        self._db.execute(
            "INSERT INTO migrations(migration_id, applied_at, details) VALUES (?, ?, ?)",
            (migration_id, time.time(), json.dumps({"users": len(rows), "level": 1, "fish_today": 0}, ensure_ascii=False)),
        )
        self._db.commit()

    def _fish_windows(self) -> list[FishKingWindow]:
        if not hasattr(self, "_db"):
            return []
        today = datetime.now().date()
        week_start = today - timedelta(days=today.weekday())
        next_week = week_start + timedelta(days=7)
        rows = self._db.execute(
            "SELECT chain, king_name, starts_at, ends_at FROM fish_windows WHERE week_start IN (?, ?)",
            (week_start.isoformat(), next_week.isoformat()),
        ).fetchall()
        return [FishKingWindow(chain, name, datetime.fromisoformat(start), datetime.fromisoformat(end)) for chain, name, start, end in rows]

    def _clock_text(self) -> str:
        now = datetime.now()
        windows = self._fish_windows()
        current = active_window(windows, now)
        upcoming = next((item for item in sorted(windows, key=lambda value: value.starts_at) if item.starts_at > now), None)
        next_zone_at = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        current_zone = daily_fishing_zone(now)
        next_zone = daily_fishing_zone(next_zone_at)
        zone_lines = (
            f"当前鱼点：{fishing_zone_label(current_zone)}\n"
            f"当前鱼点剩余：{format_duration((next_zone_at - now).total_seconds())}\n"
            f"下一鱼点：{fishing_zone_label(next_zone)}\n"
            f"下一鱼点开始：{next_zone_at:%m月%d日 %H:%M}（还有{format_duration((next_zone_at - now).total_seconds())}）\n"
            "下一鱼点持续：24小时"
        )
        if current:
            state = "水面出现异动"
            remaining = format_duration((current.ends_at - now).total_seconds())
            duration = format_duration((current.ends_at - current.starts_at).total_seconds())
            king_line = f"当前鱼王：{current.king_name}\n鱼王窗口剩余：{remaining}\n鱼王窗口持续：{duration}\n鱼王窗口结束：{current.ends_at:%m月%d日 %H:%M}"
        else:
            state = "水面平静"
            if upcoming:
                until = format_duration((upcoming.starts_at - now).total_seconds())
                duration = format_duration((upcoming.ends_at - upcoming.starts_at).total_seconds())
                king_line = f"下一条鱼王：{upcoming.king_name}\n距离开始：{until}\n鱼王窗口持续：{duration}\n鱼王窗口开始：{upcoming.starts_at:%m月%d日 %H:%M}\n鱼王窗口结束：{upcoming.ends_at:%m月%d日 %H:%M}"
            else:
                king_line = "下一条鱼王：本周已结束"
        return f"钓鱼时钟（北京时间）\n当前：{now:%m月%d日 %H:%M}\n{zone_lines}\n当前状态：{state}\n{king_line}"

    def _render_clock_image(self, uid: str) -> Path:
        """Render the queried clock and the five nearest future king windows."""
        now = datetime.now()
        windows = sorted(self._fish_windows(), key=lambda value: value.starts_at)
        current = active_window(windows, now)
        upcoming = [item for item in windows if item.starts_at > now][:5]
        next_zone_at = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        current_zone = fishing_zone_label(daily_fishing_zone(now))
        next_zone = fishing_zone_label(daily_fishing_zone(next_zone_at))

        row_height = 76
        height = 500 + max(1, len(upcoming)) * row_height
        image = PILImage.new("RGBA", (1200, height), (150, 150, 150, 255))
        draw = ImageDraw.Draw(image)
        draw.rectangle((10, 10, image.width - 11, image.height - 11), fill=(255, 255, 255, 255), outline="black", width=3)
        title_font = self._font(82, bold=True)
        section_font = self._font(48, bold=True)
        body_font = self._font(36)
        small_font = self._font(28)

        draw.text((35, 25), "钓鱼时钟", fill="black", font=title_font)
        draw.text((1160, 48), f"北京时间 {now:%m月%d日 %H:%M}", fill="black", font=small_font, anchor="ra")
        draw.line((35, 125, image.width - 35, 125), fill="black", width=3)
        draw.text((45, 145), f"当前鱼点：{current_zone}", fill="black", font=body_font)
        draw.text((500, 145), f"下一鱼点：{next_zone}", fill="black", font=body_font)
        draw.text((500, 190), f"开始：{next_zone_at:%m月%d日 %H:%M}", fill="black", font=body_font)

        if current:
            draw.text((45, 245), "当前状态：水面出现异动", fill="black", font=body_font)
            draw.text(
                (500, 245),
                f"当前鱼王：{current.king_name}",
                fill="black",
                font=body_font,
            )
            draw.text((500, 290), f"窗口剩余：{format_duration((current.ends_at - now).total_seconds())}", fill="black", font=body_font)
        else:
            draw.text((45, 245), "当前状态：水面平静", fill="black", font=body_font)
            draw.text((500, 245), "当前没有鱼王窗口", fill="black", font=body_font)
        draw.line((35, 350, image.width - 35, 350), fill="black", width=3)
        draw.text((45, 367), "最近5次鱼王窗口", fill="black", font=section_font)

        columns = ((55, "序号"), (170, "鱼王"), (470, "开始"), (760, "结束"), (930, "距离开始"))
        header_y = 430
        for x, label in columns:
            draw.text((x, header_y), label, fill="black", font=small_font)
        draw.line((45, 470, image.width - 45, 470), fill="black", width=2)
        if upcoming:
            for index, window in enumerate(upcoming):
                y = 485 + index * row_height
                until = format_duration((window.starts_at - now).total_seconds())
                draw.text((55, y), str(index + 1), fill="black", font=body_font)
                draw.text((170, y), window.king_name, fill="black", font=body_font)
                draw.text((470, y), f"{window.starts_at:%m月%d日 %H:%M}", fill="black", font=body_font)
                draw.text((760, y), f"{window.ends_at:%H:%M}", fill="black", font=body_font)
                draw.text((930, y), until, fill="black", font=body_font)
        else:
            draw.text((55, 495), "本周暂无后续鱼王窗口", fill="black", font=body_font)

        output = Path(self.workspace) / "images" / f"clock_{uid}.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        image.save(output, "PNG")
        return output

    def _render_help_image(self, uid: str) -> Path:
        """Render the fishing rules as an image using the live game parameters."""
        lines = [line.strip() for line in MCFISH_HELP.splitlines() if line.strip()]
        lines.extend([
            "",
            "当前装备说明（以代码实际参数为准）",
        ])
        lines.extend(
            f"{name}：耐久{durability}，均价{price}金币，解锁等级{rod_unlock_level(name)}级。"
            for name, (durability, price) in POLES.items()
        )
        lines.append("三叉戟：耐久301，只能通过合成、修复和交易获得；每次钓鱼按3竿计算。")
        for name, info in EQUIPMENT.items():
            kind_text = "永久饰品" if info["kind"] == "permanent" else f"消耗品，{info['uses']}竿"
            effect_text = {
                "财富浮标": "宝藏概率区间×1.35，鱼王咬钩率×1.20",
                "坚韧钓组": "鱼王上钩成功率+12个百分点，最低成功率保留5%",
                "黄金磁石": "宝藏品质提高1档，宝藏售价×1.25",
                "魔法鱼饵": "每竿等待时间×0.85，稀有普通鱼权重×1.25",
                "幸运海星": "普通碎片概率由8%提高至16%",
                "隐秘诱饵": "对应鱼点隐藏鱼触发率×1.50",
                "万能鱼饵": "每竿兜底一个普通鱼点或鱼饵限制",
            }.get(name, "暂无效果说明")
            shop_text = f"，金币商店价{SHOP_EQUIPMENT_PRICES[name]}" if name in SHOP_EQUIPMENT_PRICES else ""
            lines.append(f"{name}：{kind_text}，合成需要{info['fragments']}碎片{shop_text}；效果：{effect_text}。")
        lines.extend([
            "",
            "已确认的特殊机制",
            "累计完成75次钓鱼保底获得1本净化书，超过75次的部分继续计入下一轮。",
            "背包中鱼的总数量超过100条时，掉落大类区间切换为2/8/35/45，普通非鱼竿物品数量可能增加至5个。",
            "普通碎片连续12次未获得时，第13次普通掉落必得1至2枚碎片。",
            "每竿另有0.1%概率触发碎片宝藏，额外获得50枚碎片并显示特殊提示。",
            "鱼王窗口、前置鱼王路径、咬钩策略和上钩技能按钓鱼时钟及实际提示进行。",
        ])
        body_font = self._font(50)
        measure = ImageDraw.Draw(PILImage.new("RGB", (1, 1)))
        wrapped: list[str] = []
        for line in lines:
            if not line:
                wrapped.append("")
                continue
            current = ""
            for char in line:
                candidate = current + char
                if current and measure.textbbox((0, 0), candidate, font=body_font)[2] > 900:
                    wrapped.append(current)
                    current = char
                else:
                    current = candidate
            if current:
                wrapped.append(current)
        output = Path(self.workspace) / "images" / f"help_{uid}.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        image = self._panel("钓鱼帮助", wrapped, max(500, 150 + len(wrapped) * 70 + 90))
        image.save(output, "PNG")
        return output

    def _save(self, user_id: str, state: dict) -> None:
        self._db.execute("INSERT OR REPLACE INTO players(user_id, state, updated_at) VALUES (?, ?, ?)", (str(user_id), json.dumps(state, ensure_ascii=False), time.time()))
        self._db.commit()

    @staticmethod
    def _record_capture(state: dict, name: str, *, caught_at: datetime | None = None) -> None:
        stats = state.setdefault("fish_stats", {})
        item = stats.setdefault(name, {"count": 0, "first_caught": None})
        item["count"] = int(item.get("count", 0)) + 1
        if not item.get("first_caught"):
            item["first_caught"] = (caught_at or datetime.now()).isoformat(timespec="seconds")

    def _grant_fishing_fragments(self, state: dict, source: str) -> int:
        state["fragment_jackpot"] = False
        if source == "king":
            amount = random.randint(5, 9)
            state["fragment_misses"] = 0
        elif source == "rare":
            amount = random.randint(2, 4) if random.random() < 0.22 else 0
            state["fragment_misses"] = 0 if amount else int(state.get("fragment_misses", 0)) + 1
        else:
            if fragment_jackpot():
                state["fragment_jackpot"] = True
                state["fragment_misses"] = 0
                self._add(state, "钓鱼碎片", 50)
                return 50
            fragment_rate = 0.16 if "幸运海星" in state.get("accessories", []) else 0.08
            amount, misses = fragment_roll(int(state.get("fragment_misses", 0)), rate=fragment_rate)
            state["fragment_misses"] = misses
        if amount:
            self._add(state, "钓鱼碎片", amount)
        return amount

    def _record_attempt(self, uid: str, attempt_index: int, scheduled_at: datetime, rod_id: str, bait_id: str, window: FishKingWindow | None, result_type: str) -> None:
        if not hasattr(self, "_db"):
            return
        self._db.execute(
            "INSERT INTO fishing_attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (uid, attempt_index, scheduled_at.isoformat(), rod_id, bait_id, window.king_name if window else None, result_type, time.time()),
        )

    def _economy_change(self, user_id: str, delta: int, reason: str) -> bool:
        economy = self._plugin_loader.get_plugin("GroupEconomy") if hasattr(self, "_plugin_loader") else None
        if economy is not None:
            self._economy = economy
        return bool(self._economy.store.change_balance(user_id, delta, reason=reason)["ok"])

    def _load_original_data(self) -> None:
        """读取 ZeroBot 原插件的物品表；资源缺失时保留内置兼容数据。"""
        path = Path(__file__).parent / "resources" / "articlesInfo.json"
        self._catalog = {"zones": [], "items": []}
        if path.is_file():
            try:
                self._catalog = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self.logger.warning("无法读取原 McFish 物品表: %s", path)
        self._prices = dict(FISH) | {name: price for name, (_, price) in POLES.items()} | ITEMS | SHOP_EQUIPMENT_PRICES
        self._types = {}
        for name, item in HIDDEN_FISH.items():
            self._prices[name] = int(item["price"])
            self._types[name] = "fish"
        self._wastes = ["垃圾"]
        self._thing_order = []
        for item in self._catalog.get("物品", []):
            name = str(item.get("名称", ""))
            if not name:
                continue
            if name != "宝藏诅咒" and name not in self._thing_order:
                self._thing_order.append(name)
            self._prices[name] = int(item.get("价格", self._prices.get(name, 0)))
            self._types[name] = str(item.get("类型", ""))
            if self._types[name] == "waste":
                self._wastes.append(name)

    def _shop_data(self) -> tuple[dict[str, int], dict[str, int]]:
        """返回当天价格倍率和跨天延续的库存。"""
        today = date.today().isoformat()
        prices = getattr(self, "_prices", dict(FISH) | {name: price for name, (_, price) in POLES.items()} | ITEMS)
        prices.update(SHOP_EQUIPMENT_PRICES)
        if not hasattr(self, "_db"):
            discounts = {name: random.randint(50, 199) for name in prices if name not in ("垃圾", "宝藏诅咒") and name not in HIDDEN_FISH}
            discounts.update({"木竿": 100, **{name: 100 for name in SHOP_EQUIPMENT_PRICES}})
            fish = random.choice(list(FISH))
            inventory = {"初始木竿": [{"durable": 30, "maintenance": 0, "induce": 0, "favor": 0}], fish: max(1, 100 - discounts.get(fish, 100))}
            inventory.update(SHOP_EQUIPMENT_STOCK)
            return discounts, inventory
        row = self._db.execute("SELECT discounts, inventory FROM shop WHERE day = ?", (today,)).fetchone()
        if row:
            discounts = json.loads(row[0])
            inventory = json.loads(row[1])
            listed_equipment = {name: name in inventory for name in SHOP_EQUIPMENT_STOCK}
            if "初始木竿" in inventory and isinstance(inventory["初始木竿"], int):
                inventory["初始木竿"] = self._shop_poles(inventory["初始木竿"], "木竿")
            inventory = self._normalize_shop_inventory(inventory)
            inventory.setdefault("初始木竿", [{"durable": 30, "maintenance": 0, "induce": 0, "favor": 0}])
            for name, stock in SHOP_EQUIPMENT_STOCK.items():
                if not listed_equipment[name]:
                    inventory[name] = stock
            discounts.setdefault("木竿", 100)
            discounts.update({name: 100 for name in SHOP_EQUIPMENT_PRICES})
            self._db.execute(
                "UPDATE shop SET inventory = ? WHERE day = ?",
                (json.dumps(inventory, ensure_ascii=False), today),
            )
            self._db.commit()
            return discounts, inventory
        previous = self._db.execute("SELECT discounts, inventory FROM shop ORDER BY day DESC LIMIT 1").fetchone()
        previous_inventory = json.loads(previous[1]) if previous else {}
        if "初始木竿" in previous_inventory and isinstance(previous_inventory["初始木竿"], int):
            previous_inventory["初始木竿"] = self._shop_poles(previous_inventory["初始木竿"], "木竿")
        previous_inventory = self._normalize_shop_inventory(previous_inventory)
        discounts = {}
        for name in prices:
            if name in ("垃圾", "宝藏诅咒") or name in HIDDEN_FISH:
                continue
            count = previous_inventory.get(name, 0)
            count = len(count) if isinstance(count, list) else int(count or 0)
            discounts[name] = random.randint(90, 109) if count > 150 else random.randint(50, 199)
        discounts["木竿"] = 100
        discounts.update({name: 100 for name in SHOP_EQUIPMENT_PRICES})
        inventory = dict(previous_inventory)
        fish = random.choice(list(FISH))
        inventory[fish] = int(inventory.get(fish, 0)) + 100 - discounts.get(fish, 100)
        if inventory[fish] < 1:
            inventory[fish] = 100
        wood = self._shop_poles(inventory.get("初始木竿", []), "木竿")
        wood.append({"durable": 30, "maintenance": 0, "induce": 0, "favor": 0})
        inventory["初始木竿"] = wood if len(wood) <= 5 else wood[-1:]
        for name, stock in SHOP_EQUIPMENT_STOCK.items():
            inventory.setdefault(name, stock)
        self._db.execute(
            "INSERT INTO shop(day, discounts, inventory) VALUES (?, ?, ?)",
            (today, json.dumps(discounts, ensure_ascii=False), json.dumps(inventory, ensure_ascii=False)),
        )
        self._db.commit()
        return discounts, inventory

    @staticmethod
    def _normalize_shop_inventory(inventory: dict) -> dict:
        """Remove empty listings so the display and purchase lookup share one inventory."""
        normalized = dict(inventory)
        for name, value in list(normalized.items()):
            if name == "初始木竿" or name in POLES:
                poles = McFish._shop_poles(value, "木竿" if name == "初始木竿" else name)
                if poles:
                    normalized[name] = poles
                else:
                    normalized.pop(name, None)
            else:
                try:
                    if int(value or 0) <= 0:
                        normalized.pop(name, None)
                except (TypeError, ValueError):
                    normalized.pop(name, None)
        return normalized

    def _shop_price(self, name: str, discounts: dict[str, int]) -> int:
        prices = getattr(self, "_prices", dict(FISH) | {pole: price for pole, (_, price) in POLES.items()} | ITEMS)
        if name == "初始木竿":
            return SHOP_WOOD_PRICE
        if name == "木竿":
            return SHOP_WOOD_PRICE
        if name in SHOP_EQUIPMENT_PRICES:
            return SHOP_EQUIPMENT_PRICES[name]
        return prices.get(name, ITEMS.get(name, 0)) * discounts.get(name, 100) // 100

    def _pole_price(self, pole: dict, discounts: dict[str, int], name: str = "木竿") -> int:
        base_price = POLES[name][1]
        value = base_price - (POLES[name][0] - int(pole.get("durable", 0))) - int(pole.get("maintenance", 0)) * 2
        enchant_levels = int(pole.get("induce", 0)) + int(pole.get("favor", 0))
        value += base_price * min(3, max(0, enchant_levels)) // 2
        return max(0, value * discounts.get(name, 100) // 100)

    def _repair_abnormal_pole_trades(self) -> None:
        """Reverse the confirmed 2026-09-07 pole trades made with the bad pricing."""
        migration_id = "mcfish-pole-price-reversal-2026-09-07"
        if self._db.execute("SELECT 1 FROM migrations WHERE migration_id = ?", (migration_id,)).fetchone():
            return

        repairs = {
            "794383252": {
                "balance": 311,
                "poles": [],
                "remove_equipped_pole": True,
                "trade_count": 1,
            },
            "1908545345": {
                "balance": -7127,
                "poles": [
                    ("木竿", {"durable": 4, "maintenance": 6, "induce": 0, "favor": 0}),
                    ("木竿", {"durable": 3, "maintenance": 1, "induce": 2, "favor": 0}),
                    ("木竿", {"durable": 30, "maintenance": 1, "induce": 1, "favor": 1}),
                ],
                "trade_count": 3,
            },
            "786405040": {
                "balance": -3046,
                "poles": [("木竿", {"durable": 29, "maintenance": 8, "induce": 0, "favor": 1})],
                "trade_count": 1,
            },
            "3230201299": {
                "balance": -2605,
                "poles": [("木竿", {"durable": 28, "maintenance": 8, "induce": 2, "favor": 0})],
                "trade_count": 1,
            },
            "550500903": {
                "balance": -1510,
                "poles": [("下界合金竿", {"durable": 1, "maintenance": 0, "induce": 0, "favor": 0})],
                "trade_count": 1,
            },
        }
        old_states: dict[str, str] = {}
        new_states: dict[str, str] = {}
        try:
            for user_id, repair in repairs.items():
                row = self._db.execute("SELECT state FROM players WHERE user_id = ?", (user_id,)).fetchone()
                if not row:
                    continue
                state = json.loads(row[0])
                old_states[user_id] = json.dumps(state, ensure_ascii=False, sort_keys=True)
                for pole_name, pole in repair["poles"]:
                    state.setdefault("pack", {}).setdefault(pole_name, []).append(pole)
                if repair.get("remove_equipped_pole") and (state.get("equip") or {}).get("name") in POLES:
                    state["equip"] = None
                state["trade_count"] = max(0, int(state.get("trade_count", 0)) - int(repair["trade_count"]))
                state["pole_trade_count"] = max(0, int(state.get("pole_trade_count", 0)) - int(repair["trade_count"]))
                new_states[user_id] = json.dumps(state, ensure_ascii=False, sort_keys=True)

            for user_id, repair in repairs.items():
                if user_id not in new_states:
                    continue
                if repair["balance"]:
                    balance_reason = f"{migration_id}:balance"
                    economy_store = getattr(self._economy, "store", None)
                    already_applied = economy_store is not None and economy_store._conn.execute(
                        "SELECT 1 FROM ledger WHERE user_id = ? AND reason = ? LIMIT 1",
                        (user_id, balance_reason),
                    ).fetchone()
                    if not already_applied and not self._economy_change(user_id, int(repair["balance"]), balance_reason):
                        raise RuntimeError(f"无法回滚用户 {user_id} 的鱼竿交易余额")
                self._db.execute(
                    "UPDATE players SET state = ?, updated_at = ? WHERE user_id = ?",
                    (new_states[user_id], time.time(), user_id),
                )

            row = self._db.execute("SELECT inventory FROM shop WHERE day = ?", (date.today().isoformat(),)).fetchone()
            if row:
                inventory = json.loads(row[0])
                initial_wood = self._shop_poles(inventory.get("初始木竿", []), "木竿")
                initial_wood.append({"durable": 30, "maintenance": 0, "induce": 0, "favor": 0})
                inventory["初始木竿"] = initial_wood
                for pole_name, pole in repairs["1908545345"]["poles"] + repairs["786405040"]["poles"] + repairs["3230201299"]["poles"]:
                    poles = self._shop_poles(inventory.get(pole_name, []), pole_name)
                    for index, candidate in enumerate(poles):
                        if candidate == pole:
                            del poles[index]
                            break
                    inventory[pole_name] = poles
                self._db.execute(
                    "UPDATE shop SET inventory = ? WHERE day = ?",
                    (json.dumps(inventory, ensure_ascii=False), date.today().isoformat()),
                )

            for user_id, old_state in old_states.items():
                self._db.execute(
                    "INSERT INTO admin_adjustments(user_id, reason, old_state, new_state, created_at) VALUES (?, ?, ?, ?, ?)",
                    (user_id, migration_id, old_state, new_states[user_id], time.time()),
                )
            self._db.execute(
                "INSERT INTO migrations(migration_id, applied_at, details) VALUES (?, ?, ?)",
                (migration_id, time.time(), json.dumps({"users": sorted(new_states)}, ensure_ascii=False)),
            )
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

    def _repair_abnormal_pole_inventory(self) -> None:
        """Remove sold poles left duplicated by legacy name fields in shop rows."""
        migration_id = "mcfish-pole-price-reversal-inventory-cleanup-2026-09-07"
        if self._db.execute("SELECT 1 FROM migrations WHERE migration_id = ?", (migration_id,)).fetchone():
            return
        sold_poles = [
            ("木竿", {"durable": 30, "maintenance": 1, "induce": 1, "favor": 1}),
            ("木竿", {"durable": 29, "maintenance": 8, "induce": 0, "favor": 1}),
        ]
        row = self._db.execute("SELECT inventory FROM shop WHERE day = ?", (date.today().isoformat(),)).fetchone()
        if not row:
            return
        inventory = json.loads(row[0])
        for pole_name, expected in sold_poles:
            poles = self._shop_poles(inventory.get(pole_name, []), pole_name)
            for index, candidate in enumerate(poles):
                if all(int(candidate.get(key, 0)) == value for key, value in expected.items()):
                    del poles[index]
                    break
            inventory[pole_name] = poles
        self._db.execute(
            "UPDATE shop SET inventory = ? WHERE day = ?",
            (json.dumps(inventory, ensure_ascii=False), date.today().isoformat()),
        )
        self._db.execute(
            "INSERT INTO migrations(migration_id, applied_at, details) VALUES (?, ?, ?)",
            (migration_id, time.time(), json.dumps({"removed": sold_poles}, ensure_ascii=False)),
        )
        self._db.commit()

    def _repair_abnormal_equipment(self) -> None:
        """Finish the 2026-09-07 rollback for the user's retained equipped pole."""
        migration_id = "mcfish-pole-price-reversal-equipment-fix-2026-09-07"
        if self._db.execute("SELECT 1 FROM migrations WHERE migration_id = ?", (migration_id,)).fetchone():
            return

        user_id = "794383252"
        expected_equipment = {
            "name": "木竿",
            "durable": 3,
            "maintenance": 5,
            "induce": 3,
            "favor": 2,
        }
        row = self._db.execute("SELECT state FROM players WHERE user_id = ?", (user_id,)).fetchone()
        shop_row = self._db.execute(
            "SELECT inventory FROM shop WHERE day = ?", (date.today().isoformat(),)
        ).fetchone()
        if not row or not shop_row:
            return

        state = json.loads(row[0])
        equipment = state.get("equip") or {}
        if not all(equipment.get(key) == value for key, value in expected_equipment.items()):
            self._db.execute(
                "INSERT INTO migrations(migration_id, applied_at, details) VALUES (?, ?, ?)",
                (migration_id, time.time(), json.dumps({"removed": False}, ensure_ascii=False)),
            )
            self._db.commit()
            return

        old_state = json.dumps(state, ensure_ascii=False, sort_keys=True)
        state["equip"] = None
        new_state = json.dumps(state, ensure_ascii=False, sort_keys=True)
        inventory = json.loads(shop_row[0])
        initial_wood = self._shop_poles(inventory.get("初始木竿", []), "木竿")
        initial_wood.append({"durable": 30, "maintenance": 0, "induce": 0, "favor": 0})
        inventory["初始木竿"] = initial_wood
        try:
            self._db.execute(
                "UPDATE players SET state = ?, updated_at = ? WHERE user_id = ?",
                (new_state, time.time(), user_id),
            )
            self._db.execute(
                "UPDATE shop SET inventory = ? WHERE day = ?",
                (json.dumps(inventory, ensure_ascii=False), date.today().isoformat()),
            )
            self._db.execute(
                "INSERT INTO admin_adjustments(user_id, reason, old_state, new_state, created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, migration_id, old_state, new_state, time.time()),
            )
            self._db.execute(
                "INSERT INTO migrations(migration_id, applied_at, details) VALUES (?, ?, ?)",
                (migration_id, time.time(), json.dumps({"removed": True}, ensure_ascii=False)),
            )
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

    @staticmethod
    def _shop_poles(value, name: str) -> list[dict]:
        if isinstance(value, list):
            return value
        return [{"durable": POLES[name][0], "maintenance": 0, "induce": 0, "favor": 0} for _ in range(int(value or 0))]

    @staticmethod
    def _enchant_label(level: int) -> str:
        return ("0", "Ⅰ", "Ⅱ", "Ⅲ")[max(0, min(3, int(level)))]

    def _zone_boundaries(self, state: dict) -> tuple[int, int, int, int, int]:
        favor = int((state.get("equip") or {}).get("favor", 0))
        fish_count = sum(
            int(value) for name, value in state.get("pack", {}).items()
            if name in FISH and isinstance(value, int)
        )
        boosted = fish_count > 100 or (state.get("equip") or {}).get("name") == AXOLOTL
        if boosted:
            boundaries = (0, 2, 10, 45, 90)
        else:
            zone_probabilities = {
                str(info.get("类型")): int(info.get("概率[0-100)", 0))
                for info in getattr(self, "_catalog", {}).get("分类", [])
            }
            boundaries = (
                0,
                zone_probabilities.get("treasure", 0) + favor,
                zone_probabilities.get("treasure", 0)
                + zone_probabilities.get("pole", 0)
                + favor * 2,
                zone_probabilities.get("treasure", 0)
                + zone_probabilities.get("pole", 0)
                + zone_probabilities.get("fish", 0)
                + favor * 3,
                zone_probabilities.get("treasure", 0)
                + zone_probabilities.get("pole", 0)
                + zone_probabilities.get("fish", 0)
                + zone_probabilities.get("waste", 0),
            )
        if int(state.get("pack", {}).get("海豚", 0)) > 0:
            boundaries = (*boundaries[:4], 100)
        if "财富浮标" in state.get("accessories", []) and boundaries[1] > boundaries[0]:
            extra = int((boundaries[1] - boundaries[0]) * 0.35)
            boundaries = tuple(min(100, value + (extra if index else 0)) for index, value in enumerate(boundaries))
        return boundaries

    def _original_drop(self, state: dict, boundaries: tuple[int, int, int, int, int] | None = None, rod_name: str | None = None) -> tuple[str | None, int]:
        rod_name = rod_name or str((state.get("equip") or {}).get("name", "木竿"))
        catalog = getattr(self, "_catalog", {})
        zones = catalog.get("分类", [])
        items = catalog.get("物品", [])
        if not zones or not items:
            return None, 1
        boosted = sum(int(value) for name, value in state.get("pack", {}).items() if name in FISH and isinstance(value, int)) > 100 or (state.get("equip") or {}).get("name") == AXOLOTL
        boundaries = boundaries or self._zone_boundaries(state)
        roll = random.randrange(100)
        zone = None
        # 原插件使用累计区间，剩余 60% 为“赛博空气”。
        if boundaries[0] <= roll < boundaries[1]:
            zone = "treasure"
        elif boundaries[1] <= roll < boundaries[2]:
            zone = "pole"
        elif boundaries[2] <= roll < boundaries[3]:
            zone = "fish"
        elif boundaries[3] <= roll < boundaries[4]:
            zone = "waste"
        if zone is None:
            return None, 1
        candidates = [item for item in items if item.get("类型") == zone]
        if not candidates:
            return None, 1
        if zone == "fish" and "fishing_zone" in state:
            weights = fish_zone_weights(str(state.get("fishing_zone", daily_fishing_zone())), rod_name)
            if int(state.get("active_consumables", {}).get("魔法鱼饵", 0)) > 0:
                for name in ("水晶虾", "深海鱼", "银鱼", "金鳞鱼"):
                    if name in weights:
                        weights[name] = int(weights[name] * 1.25)
            names = [name for name, weight in weights.items() if weight > 0]
            selected_name = random.choices(names, weights=[weights[name] for name in names], k=1)[0]
            return selected_name, 5 if boosted else 1
        if zone == "fish" and int(state.get("active_consumables", {}).get("魔法鱼饵", 0)) > 0:
            rare_names = {"水晶虾", "深海鱼", "银鱼", "金鳞鱼"}
            weights = [
                int(item.get("概率[0-100)", 0)) * (1.25 if str(item.get("名称")) in rare_names else 1.0)
                for item in candidates
            ]
            selected = random.choices(candidates, weights=weights, k=1)[0]
            quantity = 5 if boosted and str(selected["名称"]) not in POLES else 1
            return str(selected["名称"]), quantity
        roll = random.randrange(100)
        lower = 0
        for item in candidates:
            upper = lower + int(item.get("概率[0-100)", 0))
            if lower <= roll < upper:
                quantity = 5 if boosted and str(item["名称"]) not in POLES else 1
                return str(item["名称"]), quantity
            lower = upper
        return str(candidates[-1]["名称"]), 1

    def _item_image(self, name: str) -> Path | None:
        icon_ids = {
            "财富浮标": "mcfish_wealth_bobber",
            "坚韧钓组": "mcfish_tough_tackle",
            "黄金磁石": "mcfish_gold_magnet",
            "魔法鱼饵": "mcfish_magic_bait",
            "幸运海星": "mcfish_lucky_starfish",
            "隐秘诱饵": "mcfish_hidden_lure",
            "万能鱼饵": "mcfish_universal_bait",
            **{name: item["asset_id"] for name, item in FISH_KINGS.items()},
            **{name: item["asset_id"] for name, item in HIDDEN_FISH.items()},
        }
        if name in icon_ids:
            path = Path(__file__).parent / "resources" / "icons" / f"{icon_ids[name]}.png"
            return path if path.is_file() else None
        filename = "book.png" if name in ("诱钓", "海之眷顾", "净化书", "宝藏诅咒") else f"{name}.png"
        path = Path(__file__).parent / "resources" / filename
        return path if path.is_file() else None

    def _add(self, state: dict, name: str, number: int = 1, *, pole: bool = False, pole_data: dict | None = None, random_pole: bool = False) -> None:
        if pole:
            value = pole_data or {"durable": POLES[name][0], "maintenance": 0, "induce": 0, "favor": 0}
            if random_pole:
                value = {
                    "durable": random.randrange(POLES[name][0]) + 1,
                    "maintenance": random.randrange(10),
                    "induce": random.randrange(3),
                    "favor": random.randrange(2),
                }
            state["pack"].setdefault(name, []).append(value)
        else:
            state["pack"][name] = int(state["pack"].get(name, 0)) + number

    def _remove(self, state: dict, name: str, number: int = 1, index: int | None = None) -> bool:
        if name in POLES:
            poles = state["pack"].get(name, [])
            if len(poles) < number:
                return False
            if index is None:
                del poles[:number]
            else:
                del poles[index:index + number]
            return True
        count = int(state["pack"].get(name, 0))
        if count < number:
            return False
        state["pack"][name] = count - number
        return True

    def _text(self, event) -> str:
        return "".join(str(getattr(seg, "text", "") or "") for seg in event.message or [])

    async def _send_original_message(self, event, message: MessageArray) -> None:
        """Send a raw message chain, matching upstream ctx.Send without auto Reply/At."""
        api = getattr(getattr(self, "api", None), "qq", None)
        if api is not None:
            await api.post_group_msg(group_id=event.group_id, rtf=message)
            return
        await event.reply(rtf=message, at_sender=False)

    def _user_lock(self, user_id: str) -> asyncio.Lock:
        locks = getattr(self, "_user_locks", None)
        if locks is None:
            locks = self._user_locks = {}
        return locks.setdefault(str(user_id), asyncio.Lock())

    async def _start_fishing(self, event, state: dict, text: str) -> None:
        """Start fishing in the background so the message handler stays responsive."""
        uid = str(event.sender.user_id)
        task = getattr(self, "_fishing_tasks", {}).get(uid)
        if task is not None and not task.done():
            await event.reply(text="你正在钓鱼，请稍后再试", at_sender=False)
            return

        async def run() -> None:
            try:
                await self._fish(event, state, text)
            except Exception:
                LOG.exception("后台钓鱼任务执行失败 (user=%s)", uid)
            finally:
                if self._fishing_tasks.get(uid) is asyncio.current_task():
                    self._fishing_tasks.pop(uid, None)

        if not hasattr(self, "_fishing_tasks"):
            self._fishing_tasks = {}
        self._fishing_tasks[uid] = asyncio.create_task(run())
        await asyncio.sleep(0)

    async def _show_last_fishing_result(self, event, state: dict) -> None:
        task = getattr(self, "_fishing_tasks", {}).get(str(event.sender.user_id))
        if task is not None and not task.done():
            await event.reply(text="你正在钓鱼，请稍后再试", at_sender=False)
            return
        result = state.get("last_fishing_result")
        if not isinstance(result, dict) or not result.get("text"):
            await event.reply(text="暂无上次钓鱼结果", at_sender=False)
            return
        image = result.get("image")
        if image:
            await event.reply(rtf=MessageArray([PlainText(text=str(result["text"])), Image(file=str(image))]))
        else:
            await event.reply(text=str(result["text"]), at_sender=False)

    async def _send_fishing_result(self, event, result_text: str, image: Path | None = None) -> None:
        if image:
            await event.reply(rtf=MessageArray([PlainText(text=result_text), Image(file=str(image))]))
        else:
            await event.reply(text=result_text)

    def _has_pole(self, state: dict) -> bool:
        return bool(state.get("equip")) or any(
            name in POLES and isinstance(value, list) and value
            for name, value in state.get("pack", {}).items()
        )

    def _has_usable_pole(self, state: dict) -> bool:
        """Return whether the player owns a durable rod allowed at their level."""
        level = int(state.get("fish_level", level_for_xp(int(state.get("fish_xp", 0)))))
        equip = state.get("equip") or {}
        if equip:
            name = str(equip.get("name", ""))
            return name == AXOLOTL or (
                name in POLES
                and rod_unlock_level(name) <= level
                and int(equip.get("durable", 0)) > 0
            )
        for name, value in state.get("pack", {}).items():
            if name not in POLES or rod_unlock_level(name) > level or not isinstance(value, list):
                continue
            if any(int(pole.get("durable", 0)) > 0 for pole in value):
                return True
        return False

    @staticmethod
    def _starter_pole_available(state: dict, today: date | None = None) -> bool:
        last_day = str(state.get("starter_pole_day", ""))
        if not last_day:
            return True
        try:
            return (today or date.today()) - date.fromisoformat(last_day) >= timedelta(days=3)
        except ValueError:
            return True

    def _fish_count_text(self, state: dict) -> str:
        level = int(state.get("fish_level", level_for_xp(int(state.get("fish_xp", 0)))))
        limit = daily_attempt_limit(level)
        used = min(limit, int(state.get("fish_today", 0)))
        return f"钓鱼等级：{level}（经验{int(state.get('fish_xp', 0))}）\n今日钓鱼次数：{used}/{limit}，剩余：{max(0, limit - used)}次"

    @staticmethod
    def _add_fishing_xp(state: dict, amount: int) -> tuple[int, int]:
        old_level = int(state.get("fish_level", 0))
        state["fish_xp"] = int(state.get("fish_xp", 0)) + max(0, int(amount))
        state["fish_level"] = level_for_xp(state["fish_xp"])
        return old_level, state["fish_level"]

    def _probability_detail_text(self, state: dict) -> str:
        zone = {
            str(info.get("类型")): int(info.get("概率[0-100)", 0))
            for info in getattr(self, "_catalog", {}).get("分类", [])
        }
        fish_count = sum(
            int(value) for name, value in state.get("pack", {}).items()
            if name in FISH and isinstance(value, int)
        )
        if fish_count > 100 or (state.get("equip") or {}).get("name") == AXOLOTL:
            category = [2, 8, 35, 45]
        else:
            category = [zone.get("treasure", 0), zone.get("pole", 0), zone.get("fish", 0), zone.get("waste", 0)]
        favor = int((state.get("equip") or {}).get("favor", 0))
        if favor:
            category[0] += favor
            category[1] += favor
            category[2] += favor
            category[3] -= favor * 3
        lines = [
            "\n大类概率:",
            f"宝藏 : {category[0]}%",
            f"鱼竿 : {category[1]}%",
            f"鱼类 : {category[2]}%",
            f"垃圾 : {category[3]}%",
            f"合计 : {sum(category)}%",
            "-----------",
            "宝藏概率:",
        ]
        raw_items = getattr(self, "_catalog", {}).get("物品", [])
        item_ranges: dict[str, int] = {}
        type_totals: dict[str, int] = {}
        for item in raw_items:
            item_type = str(item.get("类型", ""))
            item_ranges[str(item.get("名称", ""))] = int(item.get("概率[0-100)", 0))
            type_totals[item_type] = type_totals.get(item_type, 0) + int(item.get("概率[0-100)", 0))

        def item_probability(name: str, item_type: str, category_probability: int) -> str:
            raw = item_ranges.get(name, 0)
            total = type_totals.get(item_type, 0)
            value = raw * category_probability / total if total else 0
            return f"{value:.2f}%"

        treasure_names = [
            str(item.get("名称")) for item in raw_items
            if str(item.get("类型")) == "treasure"
        ]
        for name in treasure_names:
            lines.append(f"{name} : {item_probability(name, 'treasure', category[0])}")
        lines.extend(["-----------", "鱼竿概率:"])
        pole_names = [
            str(item.get("名称")) for item in raw_items
            if str(item.get("类型")) == "pole" or str(item.get("名称")) == AXOLOTL
        ]
        for name in pole_names:
            probability_category = category[0] if name == AXOLOTL else category[1]
            probability_type = "treasure" if name == AXOLOTL else "pole"
            lines.append(f"{name} : {item_probability(name, probability_type, probability_category)}")
        lines.extend(["-----------", "鱼类概率:"])
        fish_names = [
            str(item.get("名称")) for item in raw_items
            if str(item.get("类型")) == "fish" or str(item.get("名称")) == "海豚"
        ]
        for name in fish_names:
            probability_category = category[0] if name == "海豚" else category[2]
            probability_type = "treasure" if name == "海豚" else "fish"
            lines.append(f"{name} : {item_probability(name, probability_type, probability_category)}")
        lines.append("-----------")
        return "\n".join(lines)

    async def _buy_emergency_pole(self, event, state: dict) -> bool:
        if self._has_usable_pole(state):
            await event.reply(text="你已经有鱼竿了，请先装备或使用现有鱼竿")
            return False
        if int(state.get("emergency_pole_count", 0)) >= 3:
            await event.reply(text="请装备鱼竿后钓鱼")
            return False
        price = int(self.get_config("starting_pole_price", 100))
        uid = str(event.sender.user_id)
        balance = int(self._economy.store.account(uid)["balance"])
        if balance < price:
            await event.reply(text=f"你钱包当前只有{balance}金币,无法完成支付")
            return False
        if not self._economy_change(uid, -price, "mcfish_starting_pole"):
            await event.reply(text="钱包余额不足")
            return False
        state["equip"] = {"name": "木竿", "durable": 30, "maintenance": 0, "induce": 0, "favor": 0}
        state["emergency_pole_count"] = int(state.get("emergency_pole_count", 0)) + 1
        self._save(uid, state)
        await event.reply(text=f"已花费{price}金币购买并装备应急木竿")
        return True

    def _grant_starter_pole(self, uid: str, state: dict) -> None:
        state["equip"] = {
            "name": "木竿",
            "durable": 30,
            "maintenance": 0,
            "induce": 0,
            "favor": 0,
        }
        state["starter_pole_day"] = date.today().isoformat()
        self._save(uid, state)

    async def _choose(self, prompt: str, source_event, *, choices: dict[str, str],
                      timeout: float, timeout_reply: str, cancel_reply: str,
                      parse_response=None):
        """兼容未提供 session_choose 的旧版 NcatBot。"""
        if parse_response is not None:
            await source_event.reply(text=prompt, at_sender=False)
            source_user = str(source_event.sender.user_id)
            source_group = str(source_event.group_id)
            last_text = ""
            for attempt in range(2):
                try:
                    incoming = await self.wait_event(
                        predicate=lambda event: (
                            str(getattr(getattr(event, "data", event), "group_id", "")) == source_group
                            and str(getattr(getattr(event, "data", event), "user_id", "")) == source_user
                        ),
                        timeout=timeout,
                    )
                except (asyncio.TimeoutError, TimeoutError):
                    await source_event.reply(text=timeout_reply, at_sender=False)
                    return SimpleNamespace(ok=False, key=None, text=last_text, timed_out=True)
                event = getattr(incoming, "data", incoming)
                last_text = (getattr(event, "raw_message", "") or self._text(event)).strip()
                if last_text in choices:
                    return SimpleNamespace(ok=True, key=choices[last_text], text=last_text, timed_out=False)
                key = parse_response(last_text)
                if key is not None:
                    return SimpleNamespace(ok=True, key=key, text=last_text, timed_out=False)
                if attempt == 0:
                    await source_event.reply(text=f'请输入：{"/".join(choices)}或三个序号', at_sender=False)
            await source_event.reply(text=cancel_reply, at_sender=False)
            return SimpleNamespace(ok=False, key=None, text=last_text, timed_out=False)
        session_choose = getattr(self, "session_choose", None)
        if session_choose is not None:
            try:
                return await session_choose(
                    prompt, source_event, choices=choices, timeout=timeout,
                    timeout_reply=timeout_reply, cancel_reply=cancel_reply,
                )
            except TypeError as error:
                if "cancel_reply" not in str(error):
                    raise
        await source_event.reply(text=prompt, at_sender=False)
        source_user = str(source_event.sender.user_id)
        source_group = str(source_event.group_id)
        last_text = ""
        for attempt in range(2):
            try:
                incoming = await self.wait_event(
                    predicate=lambda event: (
                        str(getattr(getattr(event, "data", event), "group_id", "")) == source_group
                        and str(getattr(getattr(event, "data", event), "user_id", "")) == source_user
                    ),
                    timeout=timeout,
                )
            except (asyncio.TimeoutError, TimeoutError):
                await source_event.reply(text=timeout_reply, at_sender=False)
                return SimpleNamespace(ok=False, key=None, text=last_text, timed_out=True)
            event = getattr(incoming, "data", incoming)
            last_text = (getattr(event, "raw_message", "") or self._text(event)).strip()
            if last_text in choices:
                return SimpleNamespace(ok=True, key=choices[last_text], text=last_text, timed_out=False)
            if attempt == 0:
                await source_event.reply(text=f'请输入：{"/".join(choices)}', at_sender=False)
        await source_event.reply(text=cancel_reply, at_sender=False)
        return SimpleNamespace(ok=False, key=None, text=last_text, timed_out=False)

    @registrar.qq.on_group_command("钓鱼", priority=80)
    async def handle_short_fishing(self, event) -> None:
        """兼容原群内常用的短指令，并优先于普通消息处理器执行。"""
        text = (event.raw_message or self._text(event)).strip()
        if text != "钓鱼":
            return
        uid = str(event.sender.user_id)
        async with self._user_lock(uid):
            await self._start_fishing(event, self._state(uid), "进行钓鱼")

    async def _handle_message(self, event) -> None:
        text = (event.raw_message or self._text(event)).strip()
        if not text or not (text.startswith(("进行", "购买", "出售", "装备", "附魔", "合成", "消除", "修复", "维修")) or text in ("花费100购买鱼竿", "钓鱼帮助", "钓鱼背包", "钓鱼商店", "钓鱼看板", "钓鱼时钟", "钓鱼图鉴", "查看上次钓鱼结果", "出售所有垃圾", "当前装备概率明细", "查看钓鱼规则") or text.startswith("钓鱼图鉴 ")):
            return
        uid = str(event.sender.user_id)
        state = self._state(uid)
        if text == "查看上次钓鱼结果":
            await self._show_last_fishing_result(event, state); return
        if text in ("钓鱼背包",):
            output = await asyncio.to_thread(self._render_pack_image, uid, state)
            await self._send_original_message(event, MessageArray([Image(file=str(output))])); return
        if text == "钓鱼时钟":
            output = await asyncio.to_thread(self._render_clock_image, uid)
            await self._send_original_message(event, MessageArray([Image(file=str(output))])); return
        if text == "钓鱼图鉴" or text.startswith("钓鱼图鉴 "):
            output = await asyncio.to_thread(self._render_catalog_image, uid, state, text[4:].strip())
            await self._send_original_message(event, MessageArray([Image(file=str(output))])); return
        if text in ("钓鱼帮助", "查看钓鱼规则"):
            output = await asyncio.to_thread(self._render_help_image, uid)
            await self._send_original_message(event, MessageArray([Image(file=str(output))])); return
        if text == "花费100购买鱼竿":
            await self._buy_emergency_pole(event, state); return
        if text == "当前装备概率明细":
            await self._send_original_message(
                event,
                MessageArray([
                    At(user_id=uid),
                    PlainText(text=self._probability_detail_text(state)),
                ]),
            )
            return
        if text in ("钓鱼商店", "钓鱼看板"):
            output = await asyncio.to_thread(self._render_store_image, uid)
            await self._send_original_message(event, MessageArray([Image(file=str(output))])); return
        if text == "出售所有垃圾":
            await self._sell(event, state, "垃圾"); return
        if text.startswith("进行") and text.endswith("钓鱼"):
            await self._start_fishing(event, state, text); return
        if text.startswith("购买"):
            await self._buy(event, state, text[2:].strip()); return
        if text.startswith("出售"):
            await self._sell(event, state, text[2:].strip()); return
        if text.startswith("装备"):
            await self._equip(event, state, text[2:].strip()); return
        if text in ("修复鱼竿", "维修鱼竿"):
            await self._repair(event, state); return
        if text.startswith("附魔"):
            await self._enchant(event, state, text[2:].strip()); return
        if text.startswith("合成"):
            await self._craft(event, state, text[2:].strip()); return
        if text.startswith("消除"):
            await self._purify(event, state, text); return

    @registrar.qq.on_group_message(priority=29)
    async def handle_message(self, event) -> None:
        event = _McFishEvent(event)
        uid = str(event.sender.user_id)
        async with self._user_lock(uid):
            await self._handle_message(event)

    def _pack_text(self, state: dict) -> str:
        lines = ["钓鱼背包："]
        if state.get("equip"): lines.append(f"装备：{state['equip']['name']} 耐久 {state['equip']['durable']} 修复 {state['equip']['maintenance']} 诱钓 {state['equip']['induce']} 眷顾 {state['equip']['favor']}")
        for name, value in state["pack"].items():
            if (isinstance(value, list) and value) or (isinstance(value, int) and value > 0): lines.append(f"{name}: {len(value) if isinstance(value, list) else value}")
        return "\n".join(lines)

    def _render_catalog_image(self, uid: str, state: dict, requested: str = "") -> Path:
        """Render the personal catalogue only after an explicit query."""
        stats = state.get("fish_stats", {})
        king_stats = state.get("king_stats", {})
        total = sum(int(item.get("count", 0)) for item in stats.values())
        lines = [f"总捕获：{total}", f"已发现：{sum(1 for item in stats.values() if int(item.get('count', 0)) > 0)}/{len(FISH) + len(FISH_KINGS)}"]
        names = [requested] if requested and requested in stats else list(FISH)
        for name in names:
            item = stats.get(name, {})
            count = int(item.get("count", 0))
            ratio = (count / total * 100) if total else 0
            first = item.get("first_caught", "未捕获")
            lines.append(f"{name}：{count}（{ratio:.1f}%） 首次：{first}")
        for name in FISH_KINGS:
            item = king_stats.get(name, {})
            if requested and requested != name:
                continue
            lines.append(f"{name}：咬钩{item.get('bitten', 0)} 成功{item.get('caught', 0)} 脱钩{item.get('escaped', 0)}")
        body_font = self._font(50)
        draw = ImageDraw.Draw(PILImage.new("RGB", (1, 1)))
        wrapped: list[str] = []
        for line in lines:
            current = ""
            for char in line:
                candidate = current + char
                if current and draw.textbbox((0, 0), candidate, font=body_font)[2] > 900:
                    wrapped.append(current)
                    current = char
                else:
                    current = candidate
            if current:
                wrapped.append(current)
        output = Path(self.workspace) / "images" / f"catalog_{uid}.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        # Keep the final row clear of the panel border when the catalogue grows.
        image = self._panel("钓鱼图鉴", wrapped, max(360, 150 + len(wrapped) * 70 + 90))
        image.save(output, "PNG")
        return output

    def _shop_text(self) -> str:
        return "钓鱼商店：\n" + "\n".join(f"{name}: {price} 金币" for name, (_, price) in POLES.items()) + "\n" + "\n".join(f"{name}: {price} 金币" for name, price in ITEMS.items() if price)

    @staticmethod
    def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
        candidates = ("C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/msyh.ttc") if bold else ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf")
        for path in candidates:
            if Path(path).exists():
                return ImageFont.truetype(path, size)
        return ImageFont.load_default()

    @classmethod
    def _panel(cls, title: str, lines: list[str], height: int = 300) -> PILImage.Image:
        image = PILImage.new("RGBA", (1000, height), (255, 255, 255, 150))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 999, height - 1), outline="black", width=3)
        title_font = cls._font(100)
        body_font = cls._font(50)
        draw.text((10, 0), title, fill="black", font=title_font)
        title_box = draw.textbbox((10, 0), title, font=title_font)
        draw.line((10, title_box[3] + 8, title_box[2], title_box[3] + 8), fill="black", width=3)
        y = max(title_box[3] + 45, 150)
        for line in lines:
            draw.text((50, y), line, fill="black", font=body_font)
            y += 70
        return image

    def _resource_image(self, name: str, size: tuple[int, int]) -> PILImage.Image | None:
        path = self._item_image(name)
        if path is None:
            return None
        try:
            image = PILImage.open(path).convert("RGBA")
        except OSError:
            return None
        image.thumbnail(size, PILImage.Resampling.LANCZOS)
        return image

    @classmethod
    def _draw_bar(cls, draw: ImageDraw.ImageDraw, xy: tuple[int, int, int, int], value: float) -> None:
        left, top, right, bottom = xy
        draw.rectangle(xy, fill=(150, 150, 150))
        draw.rectangle((left, top, left + int(max(0.0, min(1.0, value)) * (right - left)), bottom), fill=(102, 102, 102))

    def _render_pack_image(self, uid: str, state: dict) -> Path:
        equip = state.get("equip")
        if equip:
            title_font = self._font(100)
            body_font = self._font(50)
            equip_height = 470
            equip_block = PILImage.new("RGBA", (1000, equip_height), (255, 255, 255, 150))
            draw = ImageDraw.Draw(equip_block)
            draw.rectangle((0, 0, 999, equip_height - 1), outline="black", width=3)
            draw.text((10, 0), "装备信息", fill="black", font=title_font)
            title_box = draw.textbbox((10, 0), "装备信息", font=title_font)
            draw.line((10, title_box[3] + 8, title_box[2], title_box[3] + 8), fill="black", width=3)

            # Keep the icon area and text origin aligned with the original renderer.
            icon_size = 300
            icon = self._resource_image(equip["name"], (icon_size, icon_size))
            if icon:
                equip_block.paste(icon, (10, 150), icon)
            text_x = 320
            name_font = self._font(75)
            draw.text((text_x, 150), equip["name"], fill="black", font=name_font)
            max_durable = 999 if equip["name"] == AXOLOTL else POLES[equip["name"]][0]
            metric_y = 235
            label_box = draw.textbbox((0, 0), "装备耐久", font=body_font)
            label_width = label_box[2] - label_box[0]
            bar_left = text_x + label_width + 5
            value_width = draw.textbbox((0, 0), "100", font=body_font)[2]
            bar_right = 1000 - 10 - value_width - 10
            draw.text((text_x, metric_y), "装备耐久", fill="black", font=body_font)
            self._draw_bar(draw, (bar_left, metric_y + 5, bar_right, metric_y + 5 + 42), int(equip.get("durable", 0)) / max_durable)
            draw.text((bar_right + 5, metric_y), str(equip.get("durable", 0)), fill="black", font=body_font)
            metric_y = 315
            draw.text((text_x, metric_y), "维修次数", fill="black", font=body_font)
            self._draw_bar(draw, (bar_left, metric_y + 5, bar_right, metric_y + 5 + 42), int(equip.get("maintenance", 0)) / 10)
            draw.text((bar_right + 5, metric_y), str(equip.get("maintenance", 0)), fill="black", font=body_font)
            metric_y = 395
            draw.text((text_x, metric_y), f" 附魔: 诱钓{self._enchant_label(equip.get('induce', 0))}  海之眷顾{self._enchant_label(equip.get('favor', 0))}", fill="black", font=body_font)
        else:
            equip_block = self._panel("装备信息", ["没有装备任何鱼竿"], 300)

        entries: list[tuple[str, int]] = []
        fish_count = 0
        pole_count = 0
        type_order = {"article": 0, "fish": 1, "pole": 2, "treasure": 3, "waste": 4}
        item_types = getattr(self, "_types", {})
        pack_items = list(state.get("pack", {}).items())
        pack_items.sort(key=lambda item: (type_order.get(item_types.get(item[0], ""), 99), item[0]))
        for name, value in pack_items:
            if isinstance(value, list):
                pole_count += len(value)
                sorted_poles = sorted(
                    value,
                    key=lambda item: f"{item.get('durable', 0)}/{item.get('maintenance', 0)}"
                    f"/{item.get('induce', 0)}/{item.get('favor', 0)}",
                )
                entries.extend(
                    (
                        f"{name}({item.get('durable', 0)}/{item.get('maintenance', 0)}"
                        f"/{item.get('induce', 0)}/{item.get('favor', 0)})",
                        1,
                    )
                    for item in sorted_poles
                )
            elif int(value) > 0:
                entries.append((name, int(value)))
                if name in FISH:
                    fish_count += int(value)
        if entries:
            rows = (len(entries) + 1) // 2
            measure = ImageDraw.Draw(PILImage.new("RGB", (1, 1)))
            font = self._font(45)
            value_width = measure.textbbox((0, 0), "10000", font=font)[2]
            column_widths = [0, 0]
            for index, (entry, _count) in enumerate(entries):
                column_widths[index % 2] = max(column_widths[index % 2], measure.textbbox((0, 0), entry, font=font)[2])
            if sum(10 + width + 10 + value_width + 10 for width in column_widths) > 980:
                font = self._font(32)
                value_width = measure.textbbox((0, 0), "10000", font=font)[2]
                column_widths = [0, 0]
                for index, (entry, _count) in enumerate(entries):
                    column_widths[index % 2] = max(column_widths[index % 2], measure.textbbox((0, 0), entry, font=font)[2])
            text_height = measure.textbbox((0, 0), "高度", font=font)[3]
            row_step = int(text_height * 2)
            pack_height = max(500, 10 + 115 + 10 + row_step * (rows + 1))
            pack_block = self._panel("背包信息", [], pack_height)
            draw = ImageDraw.Draw(pack_block)
            wall_width = max(0, (980 - sum(10 + width + 10 + value_width + 10 for width in column_widths)) // 2)
            first_x = wall_width + 20
            second_x = first_x + column_widths[0] + 10 + value_width + 10
            for x, width in ((first_x, column_widths[0]), (second_x, column_widths[1])):
                draw.text((x + width // 2, 150), "名称", fill="black", font=font, anchor="ma")
                draw.text((x + width + 10 + value_width // 2, 150), "数量", fill="black", font=font, anchor="ma")
            for index, (entry, count) in enumerate(entries):
                col, row = index % 2, index // 2
                x = first_x if col == 0 else second_x
                name_width = column_widths[col]
                y = 215 + row * row_step
                draw.text((x + name_width // 2, y), entry, fill="black", font=font, anchor="ma")
                draw.text((x + name_width + 10 + value_width // 2, y), str(count), fill="black", font=font, anchor="ma")
        else:
            pack_block = self._panel("背包信息", ["背包没有存放任何东西"], 300)

        economy = self._plugin_loader.get_plugin("GroupEconomy") if hasattr(self, "_plugin_loader") else None
        if economy is not None:
            self._economy = economy
        account = self._economy.store.account(uid)
        level = int(state.get("fish_level", max(1, level_for_xp(int(state.get("fish_xp", 0))))))
        total_xp = int(state.get("fish_xp", 0))
        current_level_xp = xp_required_for_level(level)
        next_level_xp = xp_required_for_level(level + 1)
        level_span = max(1, next_level_xp - current_level_xp)
        level_progress = max(0.0, min(1.0, (total_xp - current_level_xp) / level_span))
        daily_limit = daily_attempt_limit(level)
        profile_block = PILImage.new("RGBA", (1000, 290), (255, 255, 255, 150))
        profile_draw = ImageDraw.Draw(profile_block)
        profile_draw.rectangle((0, 0, 999, 289), outline="black", width=3)

        avatar_path = CommonUtil.get_avatar(uid)
        try:
            avatar = PILImage.open(avatar_path).convert("RGBA")
            avatar = avatar.resize((170, 170), PILImage.Resampling.LANCZOS)
            mask = PILImage.new("L", (170, 170), 0)
            ImageDraw.Draw(mask).ellipse((0, 0, 169, 169), fill=255)
            profile_block.paste(avatar, (35, 50), mask)
        except (OSError, ValueError):
            profile_draw.ellipse((35, 50, 204, 219), fill=(205, 205, 205), outline="black", width=2)

        title_font = self._font(62, bold=True)
        body_font = self._font(34)
        small_font = self._font(29)
        profile_draw.text((240, 25), "钓鱼档案", fill="black", font=title_font)
        profile_draw.text((245, 98), f"用户 {uid}", fill="black", font=body_font)
        profile_draw.text((245, 145), f"钓鱼等级  {level}", fill="black", font=body_font)
        profile_draw.text(
            (245, 193),
            f"经验 {total_xp - current_level_xp}/{level_span}",
            fill="black",
            font=small_font,
        )
        bar_left, bar_top, bar_right, bar_bottom = 590, 156, 965, 194
        profile_draw.rounded_rectangle((bar_left, bar_top, bar_right, bar_bottom), 10, fill=(215, 215, 215), outline="black", width=2)
        filled_right = bar_left + int((bar_right - bar_left) * level_progress)
        if filled_right > bar_left:
            profile_draw.rounded_rectangle((bar_left, bar_top, filled_right, bar_bottom), 10, fill=(70, 145, 95))
        profile_draw.text((590, 105), "本级经验", fill="black", font=small_font)
        profile_draw.text((590, 205), f"今日钓鱼 {min(daily_limit, int(state.get('fish_today', 0)))}/{daily_limit}", fill="black", font=small_font)
        profile_draw.text((820, 205), f"钱包 {int(account['balance'])}", fill="black", font=small_font)

        if fish_count > 100:
            profile_draw.text((245, 252), "钓鱼佬[已激活]", fill="black", font=small_font)
        if pole_count > 10:
            profile_draw.text((590, 252), "维修大师[已激活]", fill="black", font=small_font)

        output = Path(self.workspace) / "images" / f"pack_{uid}.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        result = PILImage.new("RGBA", (1020, 10 + profile_block.height + 10 + equip_block.height + 10 + pack_block.height + 10), (150, 150, 150, 255))
        ImageDraw.Draw(result).rectangle(
            (10, 10, result.width - 11, result.height - 11),
            fill=(255, 255, 255, 255),
        )
        result.paste(profile_block, (10, 10), profile_block)
        result.paste(equip_block, (10, 20 + profile_block.height), equip_block)
        result.paste(pack_block, (10, 30 + profile_block.height + equip_block.height), pack_block)
        result.save(output, "PNG")
        return output

    def _render_store_image(self, uid: str) -> Path:
        discounts, inventory = self._shop_data()
        order = list(dict.fromkeys(
            list(getattr(self, "_thing_order", []))
            + list(POLES)
            + list(SHOP_EQUIPMENT_PRICES)
            + list(discounts)
        ))
        prices = {
            name: self._shop_price(name, discounts)
            for name in order
            if name in discounts and name not in ("垃圾", "宝藏诅咒")
        }
        output = Path(self.workspace) / "images" / f"store_{uid}.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        title_font = self._font(100)
        discount_font = self._font(35)
        body_font = self._font(30)
        table_font = self._font(50)
        pole_label = "下界合金竿(100/100/0/0)"
        name_width = ImageDraw.Draw(PILImage.new("RGB", (1, 1))).textbbox((0, 0), pole_label, font=table_font)[2]
        number_width = ImageDraw.Draw(PILImage.new("RGB", (1, 1))).textbbox((0, 0), "10000", font=table_font)[2]
        price_width = number_width
        image_width = max(1000, 20 + name_width + 50 + number_width + 50 + price_width + 20)
        rows = (len(prices) + 1) // 2
        inventory_rows: list[tuple[str, int, int]] = []
        for name, count in inventory.items():
            if name == "初始木竿" or name in POLES:
                pole_name = "木竿" if name == "初始木竿" else name
                for pole in self._shop_poles(count, pole_name):
                    label = "木竿（常驻）" if name == "初始木竿" else f"{name}({pole.get('durable', 0)}/{pole.get('maintenance', 0)}/{pole.get('induce', 0)}/{pole.get('favor', 0)})"
                    pole_price = self._shop_price(name, discounts) if name == "初始木竿" else self._pole_price(pole, discounts, name)
                    inventory_rows.append((label, 1, pole_price))
            else:
                if int(count) <= 0:
                    continue
                inventory_rows.append((name, int(count), self._shop_price(name, discounts)))
        height = max(620, 270 + rows * 78 + 250 + max(1, len(inventory_rows)) * 72)
        image = PILImage.new("RGB", (image_width, height), (150, 150, 150))
        draw = ImageDraw.Draw(image)
        draw.text((10, 0), "今日波动", fill="black", font=title_font)
        title_box = draw.textbbox((10, 0), "今日波动", font=title_font)
        draw.line((10, title_box[3] + 8, title_box[2], title_box[3] + 8), fill="black", width=3)
        start_y = title_box[3] + 38
        item_width = (image_width - 40) / 2
        for index, (name, price) in enumerate(prices.items()):
            column, row = index % 2, index // 2
            x = 20 + column * item_width
            y = start_y + row * 78
            average = getattr(self, "_prices", {}).get(name, POLES.get(name, (0, 0))[1])
            change = discounts.get(name, 100) - 100
            change_text = f"+{change}%" if change > 0 else f"{change}%"
            name_text = f"{name}(均价{average})"
            discount_row_font = discount_font
            name_box = draw.textbbox((0, 0), name_text, font=discount_row_font)
            change_box = draw.textbbox((0, 0), change_text, font=discount_row_font)
            if name_box[2] + change_box[2] + 12 > item_width - 20:
                discount_row_font = self._font(32)
                name_box = draw.textbbox((0, 0), name_text, font=discount_row_font)
                change_box = draw.textbbox((0, 0), change_text, font=discount_row_font)
            draw.text((x, y), name_text, fill="black", font=discount_row_font)
            draw.text((x + item_width - 20 - (change_box[2] - change_box[0]), y), change_text, fill=(200, 50, 50) if change > 0 else (63, 133, 55), font=discount_row_font)
        note_y = start_y + rows * 78 + 20
        draw.text((10, note_y), "注:出售商品将会额外扣除20%的税收,附魔鱼竿请按实际价格", fill="black", font=body_font)
        section_y = note_y + 70
        draw.text((10, section_y), "上架内容", fill="black", font=title_font)
        section_box = draw.textbbox((10, section_y), "上架内容", font=title_font)
        draw.line((10, section_box[3] + 8, section_box[2], section_box[3] + 8), fill="black", width=3)
        table_y = section_box[3] + 32
        draw.text((10, table_y), "名称", fill="black", font=table_font)
        number_x = 20 + name_width + 50
        price_x = number_x + number_width + 50
        draw.text((number_x, table_y), "数量", fill="black", font=table_font)
        draw.text((price_x, table_y), "价格", fill="black", font=table_font)
        if inventory_rows:
            for index, (display_name, row_count, row_price) in enumerate(inventory_rows):
                row_y = table_y + 80 + index * 72
                draw.text((10, row_y), display_name, fill="black", font=body_font)
                draw.text((number_x, row_y), str(row_count), fill="black", font=body_font)
                draw.text((price_x, row_y), str(row_price), fill="black", font=body_font)
        else:
            draw.text((350, table_y + 90), "当前商店并没有上架任何物品", fill="black", font=body_font)
        image.save(output, "PNG")
        return output

    async def _fish(self, event, state: dict, text: str) -> None:
        event = _McFishEvent(event)
        uid = str(event.sender.user_id)
        match = re.fullmatch(r"进行(?:(\d+|[零〇一二两三四五六七八九十百千]+)次)?钓鱼", text)
        if not match:
            return
        requested = parse_count_token(match.group(1) or "1")
        if requested is None or requested <= 0:
            await event.reply(text="钓鱼次数格式不正确")
            return
        has_level_state = "fish_level" in state or "fish_xp" in state
        level = int(state.get("fish_level", level_for_xp(int(state.get("fish_xp", 0)))))
        daily_limit = daily_attempt_limit(level) if has_level_state else int(self.get_config("fish_limit", 50))
        if requested > self.MAX_FISH_BATCH:
            await event.reply(text=f"单次最多进行{self.MAX_FISH_BATCH}次钓鱼")
            return
        if requested > daily_limit:
            await event.reply(text=f"你当前等级每日最多钓鱼{daily_limit}次")
            return
        number = requested
        equip = state.get("equip")
        remaining_today = daily_limit - int(state["fish_today"])
        if remaining_today <= 0:
            await event.reply(text=f"今天你已经进行{daily_limit}次钓鱼了.\n游戏虽好,但请不要沉迷。")
            return
        pole_count = sum(
            len(value) for name, value in state.get("pack", {}).items()
            if name in POLES and isinstance(value, list)
        )
        if pole_count > 30:
            await event.reply(text=f"你有{pole_count}支鱼竿,大于30支的玩家不允许钓鱼")
            return
        if not equip:
            if self._has_usable_pole(state):
                await event.reply(text="你背包里已有鱼竿，请先装备后再钓鱼")
                return
            price = int(self.get_config("starting_pole_price", 100))
            balance = int(self._economy.store.account(uid)["balance"])
            if balance < price and self._starter_pole_available(state):
                self._grant_starter_pole(uid, state)
                equip = state["equip"]
                await event.reply(text="本次补偿一把耐久30的木竿（每3天一次）")
            else:
                if int(state.get("emergency_pole_count", 0)) >= 3:
                    await event.reply(text="请装备鱼竿后钓鱼")
                    return
                result = await self._choose(
                    '你尚未装备鱼竿,是否花费100购买鱼竿?\n回答"是"或"否"',
                    event,
                    choices={"是": "buy", "否": "cancel"},
                    timeout=120,
                    timeout_reply="等待超时,取消购买",
                    cancel_reply="已取消购买",
                )
                if result.key != "buy":
                    return
                if not await self._buy_emergency_pole(event, state):
                    return
                equip = state["equip"]
        axolotl = equip["name"] == AXOLOTL
        if has_level_state and not axolotl and level < rod_unlock_level(equip["name"]):
            await event.reply(text=f"你的钓鱼等级为{level}，无法使用{equip['name']}，需要等级{rod_unlock_level(equip['name'])}")
            return
        if not axolotl and int(equip["durable"]) <= 0:
            await event.reply(text=f"当前鱼竿没有耐久了，请先修复或更换鱼竿；{self._fish_count_text(state)}")
            return
        if not axolotl:
            number = min(number, int(equip["durable"]), remaining_today)
        if number <= 0:
            await event.reply(text="当前没有可用的钓鱼次数或鱼竿耐久")
            return
        fish_attempts = number
        result_note = ""
        if equip["name"] == "三叉戟":
            fish_attempts *= 3
        if axolotl:
            fish_available = sum(
                int(state["pack"].get(name, 0)) for name in FISH
            )
            required_fish = number * 3
            if fish_available < required_fish:
                state["equip"] = None
                self._save(uid, state)
                await event.reply(text="美西螈因为没吃到鱼,钓鱼时一直没回来,你失去了美西螈")
                return
            remaining = required_fish
            eaten: dict[str, int] = {}
            for fish_name in FISH:
                consumed = min(remaining, int(state["pack"].get(fish_name, 0)))
                if consumed:
                    self._remove(state, fish_name, consumed)
                    eaten[fish_name] = consumed
                    remaining -= consumed
                if remaining == 0:
                    break
            fish_attempts = sum(eaten.values()) // 3
            result_note = "(美西螈掉落翻5倍，吃3倍鱼：\n吃掉了：" + " ".join(
                f"{count}{fish_name}" for fish_name, count in eaten.items()
            ) + ")"
        else:
            equip["durable"] -= number
            if 0 < int(equip["durable"]) < 10:
                result_note = f"(你的鱼竿耐久仅剩{equip['durable']})"
            elif int(equip["durable"]) <= 0:
                result_note = "(你的鱼竿已销毁)"
        state["fish_today"] += number
        # 先持久化本次预约消耗；鱼获在每一竿实际等待结束后单独结算并保存。
        self._save(uid, state)
        magic_bait = int(state.get("active_consumables", {}).get("魔法鱼饵", 0)) > 0
        wait_rate = EQUIPMENT["魔法鱼饵"]["effects"]["wait_rate"] if magic_bait else 1.0
        initial_wait_seconds = fishing_wait_for_result(
            "鳕鱼", induce=int(equip.get("induce", 0)), wait_rate=wait_rate
        )
        await event.reply(text="你开始去钓鱼了,每条鱼会分别等待上钩,请耐心等待")
        results = {}
        attempt_results: list[dict] = []
        earned_xp = 0
        starting_level = int(state.get("fish_level", level))
        single_image: Path | None = None
        drop_boundaries = self._zone_boundaries(state)
        fishing_started_at = datetime.now()
        current_time = fishing_started_at
        current_wait_seconds = initial_wait_seconds
        windows = self._fish_windows()
        for attempt_index in range(fish_attempts):
            current_time += timedelta(seconds=current_wait_seconds)
            scheduled_at = current_time
            wait_seconds = (scheduled_at - datetime.now()).total_seconds()
            if wait_seconds > 0:
                await asyncio.sleep(wait_seconds)
            if magic_bait:
                state["active_consumables"]["魔法鱼饵"] -= 1
                state["pack"]["魔法鱼饵"] = max(0, int(state["pack"].get("魔法鱼饵", 0)) - 1)
            universal_bait = int(state.get("active_consumables", {}).get("万能鱼饵", 0)) > 0
            if universal_bait:
                state["active_consumables"]["万能鱼饵"] -= 1
                state["pack"]["万能鱼饵"] = max(0, int(state["pack"].get("万能鱼饵", 0)) - 1)
            hidden_lure_active = int(state.get("active_consumables", {}).get("隐秘诱饵", 0)) > 0
            if hidden_lure_active:
                state["active_consumables"]["隐秘诱饵"] -= 1
                state["pack"]["隐秘诱饵"] = max(0, int(state["pack"].get("隐秘诱饵", 0)) - 1)
            current_zone = str(state.get("fishing_zone", daily_fishing_zone()))
            active = [
                window for window in windows
                if window.contains(scheduled_at)
                and FISH_KINGS.get(window.king_name, {}).get("zone") == current_zone
            ]
            eligible_active = []
            for window in active:
                window_king = FISH_KINGS.get(window.king_name)
                prerequisite_name = window_king.get("requires") if window_king else None
                prerequisite_ready = not prerequisite_name or int(
                    state.get("king_stats", {}).get(prerequisite_name, {}).get("bitten", 0)
                ) > 0
                if prerequisite_ready:
                    eligible_active.append(window)
            king_window = random.choice(eligible_active) if eligible_active else None
            hidden_name = hidden_fish_trigger(
                zone=str(state.get("fishing_zone", "deep")),
                hidden_lure=hidden_lure_active,
            )
            if hidden_name:
                attempt_xp = fishing_xp(hidden_name)
                earned_xp += attempt_xp
                self._add_fishing_xp(state, attempt_xp)
                self._add(state, hidden_name)
                self._record_capture(state, hidden_name, caught_at=scheduled_at)
                results[hidden_name] = results.get(hidden_name, 0) + 1
                attempt_results.append({"time": scheduled_at, "result": hidden_name, "quantity": 1, "xp": attempt_xp})
                current_wait_seconds = fishing_wait_for_result(
                    hidden_name, induce=int(equip.get("induce", 0)), wait_rate=wait_rate
                )
                result_note += f"\n水中出现隐秘的光影……成功钓起【{hidden_name}】！"
                self._grant_fishing_fragments(state, "rare")
                self._record_attempt(uid, attempt_index, scheduled_at, equip["name"], "隐秘诱饵", king_window, "hidden_fish")
                self._save(uid, state)
                continue
            king = FISH_KINGS.get(king_window.king_name) if king_window else None
            prerequisite = king.get("requires") if king else None
            prerequisite_ready = not prerequisite or int(
                state.get("king_stats", {}).get(prerequisite, {}).get("bitten", 0)
            ) > 0
            bite_rate, hook_rate = (
                fish_king_probabilities(king_window.king_name, equip["name"], prerequisite_caught=prerequisite_ready)
                if king else (0.0, 0.0)
            )
            king_bites = bool(king and random.random() < king_bite_rate(bite_rate, wealth_bobber="财富浮标" in state.get("accessories", [])))
            if king_bites:
                king_stats = state.setdefault("king_stats", {}).setdefault(king_window.king_name, {})
                king_stats["bitten"] = int(king_stats.get("bitten", 0)) + 1
                post_window = next(
                    (
                        window for window in active
                        if FISH_KINGS.get(window.king_name, {}).get("requires") == king_window.king_name
                    ),
                    None,
                )
                if king["kind"] == "prerequisite" and post_window:
                    bite_choice = await self._choose(
                        "前置鱼王咬钩了！\n咬钩阶段技能：\n1. 保留目标（进入上钩判定）\n2. 以小钓大（牺牲当前鱼，尝试后置鱼王）\n3. 见好就收（放弃追逐）",
                        event,
                        choices={"1": "hold_target", "2": "mooch_post", "3": "give_up"},
                        timeout=30,
                        timeout_reply="等待超时，放弃这次机会",
                        cancel_reply="选择无效，放弃这次机会",
                    )
                else:
                    bite_choice = await self._choose(
                        "鱼王咬钩了！\n咬钩阶段技能：\n1. 保留目标（进入上钩判定）\n2. 见好就收（放弃追逐）",
                        event,
                        choices={"1": "hold_target", "2": "give_up"},
                        timeout=30,
                        timeout_reply="等待超时，放弃这次机会",
                        cancel_reply="选择无效，放弃这次机会",
                    )
                bite_skill = bite_skill_from_reply(bite_choice.text or "") or bite_choice.key
                if bite_choice.key in (None, "give_up") or bite_skill == "give_up":
                    king_stats["escaped"] = int(king_stats.get("escaped", 0)) + 1
                    attempt_results.append({"time": scheduled_at, "result": "鱼王机会（放弃）", "quantity": 0, "xp": 0})
                    current_wait_seconds = fishing_wait_for_result(
                        "鱼王机会（放弃）", induce=int(equip.get("induce", 0)), wait_rate=wait_rate
                    )
                    result_note += "\n你放弃了这次鱼王机会。"
                    self._record_attempt(uid, attempt_index, scheduled_at, equip["name"], "咬钩阶段放弃", king_window, "king_give_up")
                    self._save(uid, state)
                    continue
                if bite_skill == "mooch_post":
                    king_window = post_window
                    king = FISH_KINGS[king_window.king_name]
                    king_stats = state.setdefault("king_stats", {}).setdefault(king_window.king_name, {})
                    king_stats["bitten"] = int(king_stats.get("bitten", 0)) + 1
                    _, hook_rate = fish_king_probabilities(king_window.king_name, equip["name"], prerequisite_caught=True)
                    result_note += "\n你牺牲了当前前置鱼王，尝试引出后置鱼王！"

                hook_choice = await self._choose(
                    "已进入上钩判定。\n上钩阶段技能：\n1. 精准提竿\n2. 强力提竿\n3. 耐心控鱼\n4. 爆发收线",
                    event,
                    choices={number: strategy for number, strategy in (("1", "direct_reel"), ("2", "small_to_large"), ("3", "humble_bait"), ("4", "ambitious_bait"))},
                    timeout=30,
                    timeout_reply="等待超时，按1稳妥收线",
                    cancel_reply="选择无效，按1稳妥收线",
                )
                hook_skill = hook_skill_from_reply(hook_choice.text or "")
                if hook_skill is None:
                    hook_skill = {
                        "direct_reel": "precision_hookset",
                        "small_to_large": "powerful_hookset",
                        "humble_bait": "patient_control",
                        "ambitious_bait": "burst_reel",
                    }.get(hook_choice.key, "precision_hookset")
                strategy = {
                    "precision_hookset": "direct_reel",
                    "powerful_hookset": "small_to_large",
                    "patient_control": "humble_bait",
                    "burst_reel": "ambitious_bait",
                }[hook_skill]
                progress = path_progress(state, king_window.king_name, strategy)
                guaranteed = bool(progress.get("guaranteed"))
                success = random.random() < king_success_rate(
                    hook_rate + hook_skill_bonus(hook_skill),
                    guaranteed=guaranteed,
                    tough_tackle="坚韧钓组" in state.get("accessories", []),
                )
                if guaranteed:
                    progress["guaranteed"] = False
                if success:
                    attempt_xp = fishing_xp(king_window.king_name)
                    earned_xp += attempt_xp
                    self._add_fishing_xp(state, attempt_xp)
                    length = random.randint(*king["length"])
                    self._add(state, king_window.king_name)
                    self._record_capture(state, king_window.king_name, caught_at=scheduled_at)
                    king_stats["caught"] = int(king_stats.get("caught", 0)) + 1
                    price = int(king["base_price"] * length / ((king["length"][0] + king["length"][1]) / 2))
                    results[king_window.king_name] = results.get(king_window.king_name, 0) + 1
                    attempt_results.append({"time": scheduled_at, "result": king_window.king_name, "quantity": 1, "xp": attempt_xp})
                    current_wait_seconds = fishing_wait_for_result(
                        king_window.king_name, induce=int(equip.get("induce", 0)), wait_rate=wait_rate
                    )
                    self._grant_fishing_fragments(state, "king")
                    state.setdefault("king_results", {})[king_window.king_name] = {"length": length, "price": price}
                    result_note += f"\n成功钓起鱼王【{king_window.king_name}】！长度：{length} cm，售价：{price}。"
                    self._record_attempt(uid, attempt_index, scheduled_at, equip["name"], "鱼王策略", king_window, "king_success")
                else:
                    king_stats["escaped"] = int(king_stats.get("escaped", 0)) + 1
                    attempt_results.append({"time": scheduled_at, "result": "鱼王脱钩", "quantity": 0, "xp": 0})
                    current_wait_seconds = fishing_wait_for_result(
                        "鱼王脱钩", induce=int(equip.get("induce", 0)), wait_rate=wait_rate
                    )
                    self._record_attempt(uid, attempt_index, scheduled_at, equip["name"], "鱼王策略", king_window, "king_escape")
                self._save(uid, state)
                continue
            name, quantity = self._original_drop(state, drop_boundaries)
            if universal_bait and name is None:
                name = random.choice(list(FISH))
                quantity = 1
            if name is None:
                catalog = getattr(self, "_catalog", {})
                if catalog.get("分类") and catalog.get("物品"):
                    results["赛博空气"] = results.get("赛博空气", 0) + 1
                    single_image = None
                    attempt_results.append({"time": scheduled_at, "result": "赛博空气", "quantity": 1, "xp": 0})
                    current_wait_seconds = fishing_wait_for_result(
                        "赛博空气", induce=int(equip.get("induce", 0)), wait_rate=wait_rate
                    )
                    self._record_attempt(uid, attempt_index, scheduled_at, equip["name"], "默认鱼饵", king_window, "air")
                    self._save(uid, state)
                    continue
                else:
                    roll = random.random()
                    if roll < 0.30:
                        name = random.choice(self._wastes if hasattr(self, "_wastes") else ["垃圾"])
                        quantity = 1
                    elif roll < 0.90:
                        name = random.choice(list(FISH))
                        quantity = 1
                    else:
                        name = "钓鱼碎片"
                        quantity = random.randint(1, 2)
            if name in POLES:
                name, quantity = "钓鱼碎片", random.randint(1, 2)
                self._add(state, name, quantity)
            else:
                self._add(state, name, quantity)
                if getattr(self, "_types", {}).get(name) == "treasure" and "黄金磁石" in state.get("accessories", []):
                    quality = state.setdefault("treasure_quality", {})
                    quality[name] = max(1, int(quality.get(name, 0))) + 1
            if name in FISH:
                self._record_capture(state, name, caught_at=scheduled_at)
                fish_xp = fishing_xp(name) * max(1, int(quantity))
                earned_xp += fish_xp
                self._add_fishing_xp(state, fish_xp)
            else:
                fish_xp = 0
            attempt_results.append({"time": scheduled_at, "result": name, "quantity": quantity, "xp": fish_xp})
            current_wait_seconds = fishing_wait_for_result(
                name, induce=int(equip.get("induce", 0)), wait_rate=wait_rate
            )
            self._grant_fishing_fragments(state, "ordinary")
            if state.pop("fragment_jackpot", False):
                result_note += "\n触发碎片宝藏！额外获得50个钓鱼碎片！"
            results[name] = results.get(name, 0) + quantity
            if len(results) == 1:
                single_image = self._item_image(name)
            else:
                single_image = None
            self._record_attempt(uid, attempt_index, scheduled_at, equip["name"], "魔法鱼饵" if magic_bait else "默认鱼饵", king_window, "ordinary")
            self._save(uid, state)
        if not axolotl and equip["durable"] <= 0:
            state["equip"] = None
        state["fish_bless"] = int(state.get("fish_bless", 0)) + fish_attempts
        while state["fish_bless"] >= 75:
            state["fish_bless"] -= 75
            self._add(state, "净化书")
        self._save(uid, state)
        if int(state.get("fish_level", 0)) > starting_level:
            result_note += f"\n恭喜你，钓鱼等级提升至{state['fish_level']}级！每日钓鱼次数上限为{daily_attempt_limit(state['fish_level'])}次。"
        detail_lines = ["\n逐竿上钩记录："]
        for index, attempt in enumerate(attempt_results, start=1):
            caught_at = attempt["time"].strftime("%m月%d日 %H:%M:%S")
            quantity = f" ×{attempt['quantity']}" if attempt["quantity"] else ""
            detail_lines.append(
                f"第{index}竿：上钩时间：{caught_at}，【{attempt['result']}】{quantity}，获得经验 {attempt['xp']} XP"
            )
        result_note = f"\n本次获得经验：{earned_xp} XP" + "\n" + "\n".join(detail_lines) + result_note
        if len(results) == 1:
            name, count = next(iter(results.items()))
            result_text = f"恭喜你钓到了{count}{name}\n{result_note}"
        else:
            result_text = f"你进行了{fish_attempts}次钓鱼,结果如下:\n"
            result_text += "".join(f"{name} : {count}\n" for name, count in results.items())
            result_text += result_note
        state["last_fishing_result"] = {
            "text": result_text,
            "image": str(single_image) if len(results) == 1 and single_image else None,
        }
        self._save(uid, state)
        await self._send_fishing_result(event, result_text, single_image if len(results) == 1 else None)

    async def _buy(self, event, state: dict, item_text: str) -> None:
        uid = str(event.sender.user_id); match = re.fullmatch(r"(.+?)(?:\s+(\d+))?", item_text); name = match.group(1) if match else item_text; number = int(match.group(2) or 1) if match else 1
        requested_name = name
        name = POLE_ALIASES.get(name, name)
        has_level_state = "fish_level" in state or "fish_xp" in state
        player_level = int(state.get("fish_level", level_for_xp(int(state.get("fish_xp", 0)))))
        if has_level_state and name in POLES and player_level < rod_unlock_level(name):
            await event.reply(text=f"你的钓鱼等级为{player_level}，{name}需要等级{rod_unlock_level(name)}才能购买")
            return
        if name in POLES:
            number = 1
        if name not in POLES and name not in FISH and name not in ITEMS and name not in SHOP_EQUIPMENT_PRICES or name in ("垃圾", "宝藏诅咒"): await event.reply(text="商店没有这个物品"); return
        discounts, inventory = self._shop_data()
        if name == "木竿":
            initial_wood = self._shop_poles(inventory.get("初始木竿", []), "木竿")
            shop_name = "初始木竿" if initial_wood else "木竿"
        else:
            shop_name = name
        if shop_name not in inventory:
            await event.reply(text="当前商店并没有上架该物品")
            return
        pole_count = sum(len(value) for pole, value in state["pack"].items() if pole in POLES and isinstance(value, list))
        if pole_count > 50:
            await event.reply(text=f"你有{pole_count}支鱼竿,大于50支的玩家不允许购买东西")
            return
        is_pole = name in POLES
        if is_pole and int(state.get("pole_trade_count", 0)) >= 10:
            await event.reply(text="今天鱼竿交易次数已达上限")
            return
        if not is_pole and int(state.get("item_buy_count", 0)) + number > 30:
            number = max(0, 30 - int(state.get("item_buy_count", 0)))
            if number == 0:
                await event.reply(text="一天只能购买30次物品，明天再来吧~")
                return
        if name == "净化书":
            curse_count = int(state["pack"].get("宝藏诅咒", 0))
            if curse_count <= 0:
                await event.reply(text="你身上没有诅咒,不能购买该商品")
                return
            if int(state["pack"].get("净化书", 0)) >= curse_count:
                await event.reply(text="你身上净化书已足够,不能购买该商品")
                return
            if number > curse_count:
                number = curse_count
        if is_pole:
            available = self._shop_poles(inventory[shop_name], name if shop_name != "初始木竿" else "木竿")
            if len(available) < number:
                await event.reply(text="当前商店并没有上架该物品")
                return
            if len(available) > 1 and number == 1:
                choices = {str(i): str(i) for i in range(len(available))}
                listing = "\n".join(f"[{i}]{name}({p['durable']}/{p['maintenance']}/{p['induce']}/{p['favor']}) 价格:{self._pole_price(p, discounts, name if shop_name != '初始木竿' else '木竿')}" for i, p in enumerate(available))
                result = await self._choose(
                    f"找到以下物品:\n{listing}\n————————\n输入对应序号进行购买,或回复“取消”取消",
                    event, choices=choices, timeout=60, timeout_reply="等待超时,取消购买", cancel_reply="已取消购买",
                )
                if result.key is None: return
                selected = available.pop(int(result.key))
                selected_poles = [selected]
            else:
                selected_poles = available[:number]
                del available[:number]
            if shop_name == "初始木竿":
                price = self._shop_price(shop_name, discounts)
            else:
                price = sum(self._pole_price(p, discounts, name) for p in selected_poles)
        else:
            if int(inventory.get(shop_name, 0)) < number:
                await event.reply(text="当前商店并没有上架该物品")
                return
            price = self._shop_price(shop_name, discounts) * number
        coupon_count = min(3, number, int(state.get("half_price_count", 0))) if not is_pole else 0
        if coupon_count:
            unit_price = price // number
            price -= (unit_price // 2) * coupon_count
        coupon_text = f"\n(半价福利还有{int(state.get('half_price_count', 0)) - coupon_count}次)" if coupon_count else ""
        curse = int(state["pack"].get("宝藏诅咒", 0))
        curse_text = f"\n(你身上绑定了{curse}层诅咒)" if curse else ""
        price = price * (100 + curse * 10) // 100
        confirmation = await self._choose(
            f'你确定花费{price}购买{number}个{requested_name}?{coupon_text}{curse_text}\n回答"是"或"否"',
            event, choices={"是": "confirm", "否": "cancel"}, timeout=60,
            timeout_reply="等待超时,取消购买", cancel_reply="已取消购买",
        )
        if confirmation.key != "confirm":
            return
        if not self._economy_change(uid, -price, "mcfish_purchase"):
            await event.reply(text=f"你身上的钱不够支付{curse_text}")
            return
        if coupon_count:
            state["half_price_count"] -= coupon_count
        if is_pole:
            for selected in selected_poles:
                self._add(state, "木竿" if shop_name == "初始木竿" else name, pole=True, pole_data=selected)
            state["pole_trade_count"] = int(state.get("pole_trade_count", 0)) + len(selected_poles)
            state["trade_count"] = int(state.get("trade_count", 0)) + len(selected_poles)
            if state["trade_count"] % 10 == 0:
                self._add(state, "宝藏诅咒")
        else:
            self._add(state, name, number)
            inventory[shop_name] -= number
            if inventory[shop_name] <= 0:
                if shop_name in SHOP_EQUIPMENT_STOCK:
                    inventory[shop_name] = 0
                else:
                    inventory.pop(shop_name, None)
            state["item_buy_count"] = int(state.get("item_buy_count", 0)) + number
            if not coupon_count:
                state["buy_times"] = int(state.get("buy_times", 0)) + 1
                if state["buy_times"] > 20:
                    state["buy_times"] -= 20
                    state["half_price_count"] = 3
        if is_pole:
            if available:
                inventory[shop_name] = available
            else:
                inventory.pop(shop_name, None)
        if hasattr(self, "_db"):
            self._db.execute("UPDATE shop SET inventory = ? WHERE day = ?", (json.dumps(inventory, ensure_ascii=False), date.today().isoformat()))
            self._db.commit()
        self._save(uid, state); await event.reply(text=f"你用{price}购买了{number}{requested_name}")

    def _parse_sell_items(self, item_text: str, prices: dict[str, int]) -> list[tuple[str, int]] | None:
        aliases = {
            "木杆": "木竿",
            "铁杆": "铁竿",
            "金杆": "金竿",
            "钻石杆": "钻石竿",
            "下界合金杆": "下界合金竿",
        }
        names = sorted(set(prices) | set(POLES) | set(aliases), key=len, reverse=True)
        name_pattern = "|".join(re.escape(name) for name in names)
        pattern = re.compile(rf"(?P<name>{name_pattern})(?:\s*(?P<number>\d+))?")
        items = []
        position = 0
        for match in pattern.finditer(item_text):
            if item_text[position:match.start()].strip():
                return None
            name = aliases.get(match.group("name"), match.group("name"))
            number = int(match.group("number") or 1)
            if number <= 0:
                return None
            items.append((name, number))
            position = match.end()
        if not items or item_text[position:].strip():
            return None
        merged: dict[str, int] = {}
        for name, number in items:
            merged[name] = merged.get(name, 0) + number
        return list(merged.items())

    async def _sell(self, event, state: dict, item_text: str) -> None:
        uid = str(event.sender.user_id)
        prices = getattr(self, "_prices", dict(FISH) | {pole: price for pole, (_, price) in POLES.items()} | ITEMS)
        if item_text.strip() == "垃圾":
            waste_names = set(getattr(self, "_wastes", ["垃圾"]))
            wastes = [
                name
                for name, value in state["pack"].items()
                if name in waste_names and int(value) > 0
            ]
            if not wastes:
                await event.reply(text="你的背包不存在该物品", at_sender=False)
                return
            discounts, _ = self._shop_data()
            gross = sum(prices.get(name, 10) * discounts.get(name, 100) // 100 * int(state["pack"].get(name, 0)) for name in wastes)
            price = gross * 8 // 10
            found = "\n".join(
                f"[{index}]{name}  数量: {int(state['pack'][name])}"
                for index, name in enumerate(wastes)
            )
            if len(wastes) > 1:
                await event.reply(text=f"找到以下物品:\n{found}\n————————", at_sender=False)
            result = await self._choose(
                f'是否接受回收站将以{price}收购全部垃圾?\n回答"是"或"否"',
                event,
                choices={"是": "confirm", "否": "cancel"},
                timeout=60,
                timeout_reply="等待超时,取消出售垃圾",
                cancel_reply="已取消出售",
            )
            if result.key != "confirm":
                return
            curse = int(state["pack"].get("宝藏诅咒", 0))
            curse_text = f"\n(你身上绑定了{curse}层诅咒)" if curse else ""
            price = price * (100 - 10 * curse) // 100
            for waste in wastes:
                self._remove(state, waste, int(state["pack"].get(waste, 0)))
            self._economy_change(uid, price, "mcfish_waste_sale")
            self._save(uid, state)
            await event.reply(text=f"出售成功,你赚到了{price}{curse_text}", at_sender=False)
            return
        items = self._parse_sell_items(item_text.strip(), prices)
        if not items:
            await event.reply(text="出售格式错误，请使用：出售物品 数量 物品 数量")
            return
        items = [(name, 1 if name in POLES else number) for name, number in items]
        if any(name in POLES for name, _number in items) and int(state.get("pole_trade_count", 0)) >= 10:
            await event.reply(text="一天只能交易10把鱼竿,明天再来售卖吧")
            return
        for name, number in items:
            if name == "宝藏诅咒":
                await event.reply(text="宝藏诅咒无法交易")
                return
            if name not in prices and name not in POLES:
                await event.reply(text="背包没有这个物品")
                return
            owned = state["pack"].get(name, []) if name in POLES else state["pack"].get(name, 0)
            owned_count = len(owned) if isinstance(owned, list) else int(owned)
            if owned_count < number:
                await event.reply(text="背包数量不足")
                return
        discounts, _ = self._shop_data()
        selected_indices: dict[str, int] = {}
        sold_poles: list[tuple[str, dict, int]] = []
        for name, number in items:
            if name in POLES and number == 1:
                poles = state["pack"].get(name, [])
                if len(poles) > 1:
                    choices = {str(i): str(i) for i in range(len(poles))}
                    listing = "\n".join(f"[{i}]{name}({p.get('durable', 0)}/{p.get('maintenance', 0)}/{p.get('induce', 0)}/{p.get('favor', 0)})" for i, p in enumerate(poles))
                    result = await self._choose(
                        f"找到以下物品:\n{listing}\n————————\n输入对应序号进行出售,或回复“取消”取消",
                        event, choices=choices, timeout=60, timeout_reply="等待超时,取消出售", cancel_reply="已取消出售",
                    )
                    if result.key is None:
                        return
                    selected_indices[name] = int(result.key)
        gross = 0
        for name, number in items:
            if name in POLES:
                poles = state["pack"][name]
                if name in selected_indices:
                    pole = poles[selected_indices[name]]
                    unit_price = self._pole_price(pole, discounts, name)
                    gross += unit_price
                    sold_poles.append((name, pole, unit_price))
                else:
                    for pole in poles[:number]:
                        unit_price = self._pole_price(pole, discounts, name)
                        gross += unit_price
                        sold_poles.append((name, pole, unit_price))
            else:
                unit_price = self._shop_price(name, discounts)
                if getattr(self, "_types", {}).get(name) == "treasure":
                    unit_price = treasure_sale_price(unit_price, gold_magnet="黄金磁石" in state.get("accessories", []))
                gross += unit_price * number
        quote = gross * 8 // 10
        sale_prompt = (
            f'是否接受商店将以{quote}收购{items[0][1]}个{items[0][0]}?\n回答"是"或"否"'
            if len(items) == 1 else
            f'是否接受商店将以{quote}收购以上物品?\n回答"是"或"否"'
        )
        confirmation = await self._choose(
            sale_prompt,
            event, choices={"是": "confirm", "否": "cancel"}, timeout=60,
            timeout_reply="等待超时,取消出售", cancel_reply="已取消出售",
        )
        if confirmation.key != "confirm":
            return
        record_available = int(state["pack"].get("唱片", 0)) - sum(
            number for name, number in items if name == "唱片"
        )
        if record_available > 0:
            record = await self._choose(
                '是否使用唱片让价格翻倍?\n回答"是"或"否"', event,
                choices={"是": "yes", "否": "no"}, timeout=60,
                timeout_reply="等待超时,按原价出售", cancel_reply="已取消出售",
            )
            if record.key == "yes":
                self._remove(state, "唱片")
                gross *= 2
        curse = int(state["pack"].get("宝藏诅咒", 0))
        curse_text = f"\n(你身上绑定了{curse}层诅咒)" if curse else ""
        gross = gross * (100 - 10 * curse) // 100
        for name, number in items:
            self._remove(state, name, number, selected_indices.get(name))
        discounts, inventory = self._shop_data()
        record_multiplier = 2 if record_available > 0 and record.key == "yes" else 1
        for name, number in items:
            if name not in POLES and name != "宝藏诅咒" and name not in getattr(self, "_wastes", ()):
                inventory[name] = min(255, int(inventory.get(name, 0)) + number)
        for name, pole, unit_price in sold_poles:
            final_unit_price = unit_price * record_multiplier * (100 - 10 * curse) // 100
            if final_unit_price >= getattr(self, "_prices", {}).get(name, POLES[name][1]) * 2:
                poles = self._shop_poles(inventory.get(name, []), name)
                if len(poles) <= 5:
                    poles.append(dict(pole))
                    inventory[name] = poles
        if hasattr(self, "_db"):
            self._db.execute(
                "UPDATE shop SET inventory = ? WHERE day = ?",
                (json.dumps(inventory, ensure_ascii=False), date.today().isoformat()),
            )
            self._db.commit()
        price = int(gross * float(self.get_config("sell_tax", 0.8)))
        pole_sales = sum(number for name, number in items if name in POLES)
        state["pole_trade_count"] = int(state.get("pole_trade_count", 0)) + pole_sales
        state["trade_count"] = int(state.get("trade_count", 0)) + pole_sales
        if pole_sales and state["trade_count"] % 10 == 0:
            self._add(state, "宝藏诅咒")
        self._economy_change(uid, price, "mcfish_sale")
        self._save(uid, state)
        if len(items) == 1:
            name, number = items[0]
            await event.reply(text=f"成功出售{name}：{number}个,你赚到了{price}{curse_text}")
        else:
            await event.reply(text=f"成功出售本次物品,你赚到了{price}{curse_text}")

    async def _equip(self, event, state: dict, name: str) -> None:
        has_level_state = "fish_level" in state or "fish_xp" in state
        player_level = int(state.get("fish_level", level_for_xp(int(state.get("fish_xp", 0)))))
        if name in POLE_ALIASES:
            name = POLE_ALIASES[name]
        if has_level_state and name in POLES and player_level < rod_unlock_level(name):
            await event.reply(text=f"你的钓鱼等级为{player_level}，{name}需要等级{rod_unlock_level(name)}才能装备")
            return
        if name in EQUIPMENT:
            if int(state["pack"].get(name, 0)) <= 0:
                await event.reply(text="背包没有这个装备")
                return
            if EQUIPMENT[name]["kind"] == "permanent":
                if name in state["accessories"]:
                    await event.reply(text="这个永久饰品已经装备")
                    return
                if len(state["accessories"]) >= 2:
                    await event.reply(text="最多同时装备2件永久饰品")
                else:
                    state["accessories"].append(name)
                    self._save(str(event.sender.user_id), state)
                    await event.reply(text=f"已装备{name}")
                return
            state.setdefault("active_consumables", {})[name] = int(state["pack"].get(name, 0))
            self._save(str(event.sender.user_id), state)
            await event.reply(text=f"已启用{name}，剩余{state['active_consumables'][name]}次")
            return
        if name == AXOLOTL:
            if not self._remove(state, name):
                await event.reply(text="背包没有这个装备")
                return
            if state.get("equip"):
                old_name = state["equip"]["name"]
                self._add(
                    state,
                    old_name,
                    pole=old_name in POLES,
                    pole_data=state["equip"] if old_name in POLES else None,
                )
            state["equip"] = {"name": AXOLOTL, "durable": 999, "maintenance": 0, "induce": 0, "favor": 0}
            self._save(str(event.sender.user_id), state)
            await event.reply(text="装备成功")
            return
        equipped = list(state["pack"].get(name, [])) if name in POLES else []
        if name not in POLES or not equipped:
            await event.reply(text="背包没有这个装备")
            return
        selected_index = 0
        if len(equipped) > 1:
            listing = "\n".join(
                f"[{index}] {name} : 耐{pole.get('durable', 0)}/修{pole.get('maintenance', 0)}"
                f"/诱{self._enchant_label(pole.get('induce', 0))}/眷顾{self._enchant_label(pole.get('favor', 0))}"
                for index, pole in enumerate(equipped)
            )
            result = await self._choose(
                f"找到以下鱼竿:\n{listing}\n————————\n- 输入对应序号进行装备\n"
                "- 输入“取消”终止本次操作\n- 鱼竿数量请使用钓鱼背包查看",
                event, choices={str(index): str(index) for index in range(len(equipped))},
                timeout=120, timeout_reply="等待超时,取消装备", cancel_reply="已取消装备",
            )
            if result.key is None:
                return
            selected_index = int(result.key)
        selected = equipped[selected_index] if equipped else {"durable": POLES[name][0], "maintenance": 0, "induce": 0, "favor": 0}
        self._remove(state, name, 1, selected_index)
        if state.get("equip"):
            old_name = state["equip"]["name"]
            self._add(state, old_name, pole=old_name in POLES, pole_data=state["equip"] if old_name in POLES else None)
        state["equip"] = {"name": name, **selected}; self._save(str(event.sender.user_id), state); await event.reply(text="装备成功")

    async def _repair(self, event, state: dict) -> None:
        equip = state.get("equip")
        if not equip or equip["name"] == "美西螈":
            await event.reply(text="仅能修复装备中的鱼竿")
            return
        if int(equip.get("maintenance", 0)) >= 10:
            await event.reply(text="装备的鱼竿已经达到修复上限")
            return
        materials = state.get("pack", {}).get(equip["name"], [])
        if not materials:
            await event.reply(text="你的背包不存在相同鱼竿进行修复")
            return
        selected_index = 0
        if len(materials) > 1:
            listing = "\n".join(
                f"[{index}] {equip['name']} : 耐{pole.get('durable', 0)}/修{pole.get('maintenance', 0)}"
                f"/诱{self._enchant_label(pole.get('induce', 0))}/眷顾{self._enchant_label(pole.get('favor', 0))}"
                for index, pole in enumerate(materials)
            )
            result = await self._choose(
                f"找到以下鱼竿:\n{listing}\n————————\n输入对应序号进行修复,或回复“取消”取消",
                event, choices={str(index): str(index) for index in range(len(materials))},
                timeout=120, timeout_reply="等待超时,取消修复", cancel_reply="已取消修复",
            )
            if result.key is None:
                return
            selected_index = int(result.key)
        material = materials[selected_index]
        pole_count = sum(
            len(value) for name, value in state.get("pack", {}).items()
            if name in POLES and isinstance(value, list)
        )
        factor = 8 if pole_count <= 10 else 10
        equip["durable"] = min(
            POLES[equip["name"]][0],
            int(equip.get("durable", 0)) + int(material.get("durable", 0)) * factor // 10,
        )
        message = ""
        for key, label in (("induce", "诱钓"), ("favor", "海之眷顾")):
            if int(material.get(key, 0)) and random.randrange(100) < 50:
                equip[key] = min(3, int(equip.get(key, 0)) + int(material[key]))
                message += f",{label}等级提升至{self._enchant_label(equip[key])}"
        self._remove(state, equip["name"], 1, selected_index)
        equip["maintenance"] = int(equip.get("maintenance", 0)) + 1
        self._save(str(event.sender.user_id), state)
        await event.reply(text=f"鱼竿修复成功,耐久提高至{equip['durable']}{message}")

    async def _enchant(self, event, state: dict, book: str) -> None:
        equip = state.get("equip")
        if not equip or equip["name"] == "美西螈":
            await event.reply(text="仅可对装备中的进行附魔")
            return
        if book not in ("诱钓", "海之眷顾"):
            await event.reply(text="附魔失败了")
            return
        key = "induce" if book == "诱钓" else "favor"
        if int(state["pack"].get(book, 0)) < 1:
            await event.reply(text=f"你的背包不存在{book}进行附魔")
            return
        self._remove(state, book)
        if random.randrange(100) > 50:
            self._save(str(event.sender.user_id), state)
            await event.reply(text="附魔失败了")
            return
        equip[key] = int(equip.get(key, 0)) + 1
        if equip[key] > 3:
            self._save(str(event.sender.user_id), state)
            await event.reply(text=f"{book}等级已达到上限，你浪费了一本附魔书")
            return
        self._save(str(event.sender.user_id), state)
        await event.reply(text=f"附魔成功,{book}等级提高至{self._enchant_label(equip[key])}")

    @staticmethod
    def _craft_indices(response: str, pole_count: int) -> str | None:
        values = response.split()
        if len(values) != 3 or not all(value.isdigit() for value in values):
            return None
        indices = tuple(int(value) for value in values)
        if len(set(indices)) != 3 or any(index < 0 or index >= pole_count for index in indices):
            return None
        return "group:" + ":".join(map(str, indices))

    async def _craft(self, event, state: dict, name: str) -> None:
        has_level_state = "fish_level" in state or "fish_xp" in state
        player_level = int(state.get("fish_level", level_for_xp(int(state.get("fish_xp", 0)))))
        if name in POLE_ALIASES:
            name = POLE_ALIASES[name]
        if has_level_state and name in POLES and player_level < rod_unlock_level(name):
            await event.reply(text=f"你的钓鱼等级为{player_level}，{name}需要等级{rod_unlock_level(name)}才能合成")
            return
        if name in EQUIPMENT:
            required = EQUIPMENT[name]["fragments"]
            owned = int(state["pack"].get("钓鱼碎片", 0))
            if owned < required:
                await event.reply(text=f"合成{name}需要{required}个钓鱼碎片，你只有{owned}个")
                return
            if EQUIPMENT[name]["kind"] == "permanent" and (name in state.get("accessories", []) or int(state["pack"].get(name, 0)) > 0):
                await event.reply(text="同名永久饰品不能重复合成")
                return
            self._remove(state, "钓鱼碎片", required)
            self._add(state, name)
            self._save(str(event.sender.user_id), state)
            uses = EQUIPMENT[name]["uses"]
            suffix = "永久饰品" if uses is None else f"消耗品，{uses}竿"
            await event.reply(text=f"成功合成{name}（{suffix}）")
            return
        source = UPGRADES.get(name)
        poles = state.get("pack", {}).get(source, []) if source else []
        if name not in UPGRADES or len(poles) < 3: await event.reply(text="合成材料不足"); return
        selected_groups: list[tuple[int, int, int]]
        if len(poles) == 3:
            selected_groups = [(0, 1, 2)]
        else:
            choices = {"梭哈": "all", "取消": "cancel"}
            listing = "\n".join(
                f"[{index}] {source} : 耐{pole.get('durable', 0)}/修{pole.get('maintenance', 0)}"
                f"/诱{self._enchant_label(pole.get('induce', 0))}/眷顾{self._enchant_label(pole.get('favor', 0))}"
                for index, pole in enumerate(poles)
            )
            result = await self._choose(
                f"找到以下鱼竿:\n{listing}\n————————\n- 输入3个序号进行合成(用空格分割)\n"
                "- 输入“取消”，终止本次合成\n- 输入“梭哈”，合成所有鱼竿",
                event, choices=choices, timeout=120, timeout_reply="等待超时,取消合成", cancel_reply="已取消合成",
                parse_response=lambda response: self._craft_indices(response, len(poles)),
            )
            if result.key is None:
                return
            if result.key == "all":
                batch_count = len(poles) // 3
                selected_groups = [
                    (index, index + 1, index + 2)
                    for index in range(0, batch_count * 3, 3)
                ][::-1]
            elif result.key.startswith("group:"):
                selected_groups = [tuple(map(int, result.key[6:].split(":")))]
            else:
                return
        success = 0
        failure = 0
        last_attribute = ""
        for group in selected_groups:
            selected = [poles[index] for index in group]
            for index in sorted(group, reverse=True):
                self._remove(state, source, 1, index)
            attribute = {
                "durable": POLES[name][0],
                "maintenance": 0,
                "induce": sum(int(p.get("induce", 0)) for p in selected) // 3,
                "favor": sum(int(p.get("favor", 0)) for p in selected) // 3,
            }
            last_attribute = f"{attribute['durable']}/0/{attribute['induce']}/{attribute['favor']}"
            if random.randrange(100) < 90:
                self._add(state, name, pole=True, pole_data=attribute)
                success += 1
            else:
                failure += 1
        self._save(str(event.sender.user_id), state)
        if len(selected_groups) > 1:
            remaining = len(poles) % 3
            message = f"合成完成，成功：{success} 个，失败：{failure} 个"
            if remaining:
                message += f"，剩余 {remaining} 根材料未参与合成"
        elif success:
            message = f"成功合成：1 个{name}\n属性: {last_attribute}"
        else:
            message = "合成失败,材料已销毁"
        await event.reply(text=message)

    async def _purify(self, event, state: dict, text: str) -> None:
        number = int(re.search(r"(\d+)$", text).group(1)) if re.search(r"(\d+)$", text) else 1
        curse_count = int(state["pack"].get("宝藏诅咒", 0))
        if curse_count == 0:
            await event.reply(text="你没有绑定任何诅咒"); return
        number = min(number, curse_count)
        if int(state["pack"].get("净化书", 0)) < number:
            await event.reply(text="你没有足够的解除诅咒的道具"); return
        self._remove(state, "宝藏诅咒", number)
        self._remove(state, "净化书", number)
        self._save(str(event.sender.user_id), state); await event.reply(text="消除成功")
