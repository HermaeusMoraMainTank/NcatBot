"""CS2 开箱模块（CaseOpening 插件内部）。

指令由 CaseOpening 统一分发；本类不继承插件基类，通过 owner 借用
配置、经济、发消息能力，便于以后和抽卡等模块并列扩展。
"""

from __future__ import annotations

import asyncio
import math
import random
import re
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any, ClassVar

from common.case_data import (
    CS_FILE,
    PRICES_730,
    SNAPSHOT_DIR,
    CrateInfo,
    ImageCache,
    PriceBook,
    SkinItem,
    fetch_skinport,
    load_cs_snapshot,
    save_prices,
)
from common.case_data.render import render_grid, render_panel
from common.case_data.rules import (
    RARE_ODDS,
    STATTRAK_CHANCE,
    TIER_ODDS,
    TIER_ORDER,
    WEAR_BANDS,
    TradeupInput,
    collection_weights,
    is_tradeup_compatible,
    market_hash_name,
    next_tier,
    rarity_label,
    roll_float,
    tradeup_float,
    wear_for_float,
    wear_label,
    wear_probabilities,
)

from .config import CS2_KEYS, DEFAULT_CONFIG

RARITY_LABEL = {
    "Consumer Grade": "消费级",
    "Industrial Grade": "工业级",
    "Mil-Spec Grade": "军规级",
    "Restricted": "受限",
    "Classified": "保密",
    "Covert": "隐秘",
    "Extraordinary": "非凡",
}

#: 箱子中文叫法（社区常用别名，仅用于搜索匹配）
CASE_ALIASES: dict[str, tuple[str, ...]] = {
    "Kilowatt Case": ("千瓦", "千瓦箱"),
    "Fever Case": ("狂热", "热病", "发热"),
    "Gallery Case": ("画廊", "美术馆"),
    "Fracture Case": ("裂空", "断裂"),
    "Dreams & Nightmares Case": ("梦境与噩梦", "梦魇", "梦境"),
    "Recoil Case": ("反冲", "后坐力"),
    "Prisma 2 Case": ("棱彩2", "棱彩二号", "棱镜2"),
    "Danger Zone Case": ("危险地带", "危险区"),
    "Snakebite Case": ("蛇噬", "蛇咬"),
    "Revolution Case": ("革命",),
    "CS:GO Weapon Case": ("反恐精英", "csgo武器箱"),
    "Operation Bravo Case": ("英勇", "英勇大行动"),
    "eSports 2013 Case": ("电竞2013",),
    "Operation Hydra Case": ("九头蛇",),
    "Operation Breakout Weapon Case": ("突围",),
    "Huntsman Weapon Case": ("猎杀者",),
    "Operation Riptide Case": ("激流",),
    "Operation Broken Fang Case": ("狂牙",),
    "Chroma Case": ("幻彩",),
    "Operation Phoenix Weapon Case": ("凤凰",),
}


async def _none() -> None:
    return None

from .store_cs2 import CaseStore


class Cs2Case:
    """CS2 开箱逻辑；由 CaseOpening 注入配置、经济与消息能力。"""

    _RARITY_ALIAS: ClassVar[dict[str, str]] = {
        "军规": "Mil-Spec Grade",
        "军规级": "Mil-Spec Grade",
        "受限": "Restricted",
        "保密": "Classified",
    }

    def __init__(self, owner) -> None:
        self.plugin = owner
        self.loan = owner.loan
        self.data_dir = Path(owner.data_dir) / "cs2"
        self.image_dir = self.data_dir / "images"
        self.render_dir = self.data_dir / "cards"
        self.render_dir.mkdir(parents=True, exist_ok=True)
        self.cases, self.items, _ = load_cs_snapshot(SNAPSHOT_DIR / CS_FILE)
        self.price_path = self.data_dir / "prices_730.json.gz"
        self.prices = (
            PriceBook.from_file(self.price_path)
            if self.price_path.exists()
            else PriceBook.from_file(SNAPSHOT_DIR / PRICES_730)
        )
        self.store = CaseStore(self.data_dir / "csgo_case.db")
        self.images = ImageCache(self.image_dir)
        self._ev_cache: dict[str, float | None] = {}
        self._rng = random.Random()

    # ---------------- 借 owner 的能力 ----------------
    def cfg(self, key: str):
        plugin = getattr(self, "plugin", None)
        if plugin is not None:
            return plugin.cs_cfg(key)
        return DEFAULT_CONFIG.get(CS2_KEYS.get(key, key), DEFAULT_CONFIG.get(key))

    def money(self, coins: int) -> str:
        return self.plugin.money(coins)

    def _economy(self):
        return self.plugin._economy()

    def _charge(self, user_id: str, coins: int, reason: str) -> bool:
        return self.plugin._charge(user_id, coins, reason)

    def _credit(self, user_id: str, coins: int, reason: str) -> bool:
        return self.plugin._credit(user_id, coins, reason)

    async def _send(self, event, text: str | None = None, image: Path | None = None) -> None:
        await self.plugin._send(event, text, image)

    def get_plugin(self, name: str):
        return self.plugin.get_plugin(name)

    def close(self) -> None:
        self.store.close()

    async def dispatch(self, event, text: str) -> None:
        if text.startswith("cs箱子"):
            await self._list_cases(event, text[len("cs箱子"):].strip())
        elif text.startswith("cs详情"):
            await self._detail(event, text[len("cs详情"):].strip())
        elif text.startswith("cs价值表"):
            await self._value_table(event, text[len("cs价值表"):].strip())
        elif text.startswith("cs开箱"):
            await self._open(event, text[len("cs开箱"):].strip())
        elif text.startswith("cs背包"):
            await self._inventory(event, text[len("cs背包"):].strip())
        elif text.startswith("cs物品"):
            await self._item_detail(event, text[len("cs物品"):].strip())
        elif text.startswith("cs出售"):
            await self._sell(event, text[len("cs出售"):].strip())
        elif text.startswith("cs汰换预览"):
            await self._tradeup(event, text[len("cs汰换预览"):].strip(), preview=True)
        elif text.startswith("cs汰换"):
            await self._tradeup(event, text[len("cs汰换"):].strip(), preview=False)
        elif text.startswith("cs查价"):
            await self._price_query(event, text[len("cs查价"):].strip())
        elif text.startswith("cs战绩"):
            await self._stats(event)
        elif text.startswith("cs榜"):
            await self._leaderboard(event)

    def _coins(self, cny: float | None) -> int:
        if cny is None:
            return 0
        return math.ceil(float(cny) * float(self.cfg("coin_per_cny")))

    def _pay_for_open(self, user_id: str, coins: int, reason: str) -> bool:
        """开箱付款：先用借贷额度，不够再扣钱包。"""
        if coins <= 0:
            return True
        credit = self.loan.status(user_id)["credit"]
        used = min(credit, coins)
        rest = coins - used
        if rest and not self._charge(user_id, rest, reason):
            return False
        if used:
            self.loan.spend_credit(user_id, used)
        return True

    def _credit_with_loan(self, user_id: str, coins: int, reason: str) -> int:
        """出售所得：先还贷，剩余进钱包，返回实际进钱包的数额。"""
        if coins <= 0:
            return 0
        debt = self.loan.status(user_id)["debt"]
        if debt > 0:
            paid = min(debt, coins)
            self.loan.repay(user_id, paid)
            coins -= paid
        if coins:
            self._credit(user_id, coins, reason)
        return coins

    def _case_key_price(self) -> float:
        return float(self.cfg("key_price_usd")) * float(self.cfg("usd_cny"))

    def _case_price(self, crate: CrateInfo) -> float | None:
        return self.prices.buy_price(crate.market_hash_name)

    def _key_price(self, crate: CrateInfo) -> float:
        """钥匙按 Valve 定价；纪念包不需要钥匙。"""
        if crate.tier_odds:
            return 0.0
        key = crate.key_name or "CS:GO Case Key"
        market = self.prices.buy_price(key)
        if key == "CS:GO Case Key" and market:
            return market
        return self._case_key_price()

    def _sell_price(
        self, name: str, wear: str, stattrak: bool, souvenir: bool = False
    ) -> float | None:
        market = market_hash_name(name, wear or None, stattrak=stattrak, souvenir=souvenir)
        return self.prices.sell_price(market, float(self.cfg("sell_fee")))

    def _price_of(
        self, item: SkinItem, wear: str, stattrak: bool, souvenir: bool = False
    ) -> float | None:
        return self._sell_price(item.name, wear, stattrak, souvenir)

    def _buy_price_of(self, item: SkinItem, wear: str, stattrak: bool) -> float | None:
        name = market_hash_name(item.name, wear or None, stattrak=stattrak)
        return self.prices.buy_price(name)

    # ------------------------------------------------------------------ 概率与 EV

    def _case_ev(self, crate: CrateInfo) -> float | None:
        """按磨损区间概率加权的期望回收价（Yuan）。结果缓存。"""
        if crate.name in self._ev_cache:
            return self._ev_cache[crate.name]
        souvenir = bool(crate.tier_odds)
        odds_table = crate.tier_odds or TIER_ODDS
        total = 0.0
        for tier, odds in odds_table.items():
            ids = crate.items_of(tier)
            if not ids:
                continue
            values = [
                self._item_ev(self.items[i], stattrak_ok=not souvenir, souvenir=souvenir)
                for i in ids
                if i in self.items
            ]
            values = [v for v in values if v is not None]
            if values:
                total += odds * (sum(values) / len(values))
        if not souvenir:
            rares = [self.items[i] for i in crate.rare if i in self.items]
            values = [self._item_ev(item, stattrak_ok=item.category != "Gloves") for item in rares]
            values = [v for v in values if v is not None]
            if values:
                total += RARE_ODDS * (sum(values) / len(values))
        result = round(total, 2) if total else None
        self._ev_cache[crate.name] = result
        return result

    def _item_ev(
        self, item: SkinItem, *, stattrak_ok: bool = True, souvenir: bool = False
    ) -> float | None:
        if item.min_float is None or item.max_float is None:
            normal = self._price_of(item, "", False, souvenir)
            if normal is None:
                return None
            stat = self._price_of(item, "", True) if item.stattrak and stattrak_ok else None
            return 0.9 * normal + 0.1 * stat if stat else normal
        probs = wear_probabilities(item.min_float, item.max_float)
        if not probs:
            return None

        def weighted(stattrak: bool) -> float | None:
            prices = {
                wear: self._price_of(item, wear, stattrak, souvenir) for wear in probs
            }
            known = [value for value in prices.values() if value is not None]
            if not known:
                return None
            fallback = sum(known) / len(known)
            return sum(prob * (prices[wear] or fallback) for wear, prob in probs.items())

        normal = weighted(False)
        if normal is None:
            return None
        stat = weighted(True) if item.stattrak and stattrak_ok else None
        return 0.9 * normal + 0.1 * stat if stat else normal

    def _case_return(self, crate: CrateInfo) -> tuple[float | None, float | None]:
        ev = self._case_ev(crate)
        case_price = self._case_price(crate)
        if ev is None or not case_price:
            return ev, None
        cost = case_price + self._key_price(crate)
        return ev, ev / cost if cost else None

    def _case_blocked(self, crate: CrateInfo) -> str | None:
        """只看有没有报价；回本率不再作为下架依据。"""
        ev, _ = self._case_return(crate)
        if ev is None:
            return "缺少市场报价，暂不可开"
        return None

    # ------------------------------------------------------------------ 开箱

    def _roll_case(self, crate: CrateInfo) -> dict[str, Any]:
        souvenir = bool(crate.tier_odds)
        if souvenir:
            # 纪念包：六档概率来自运营抄录的 Valve 公示模型，档内均分
            table = [(tier, odds) for tier, odds in crate.tier_odds.items() if crate.items_of(tier)]
            total = sum(odds for _, odds in table) or 1.0
            roll = self._rng.random() * total
            cumulative = 0.0
            chosen = table[-1][0]
            for tier, odds in table:
                cumulative += odds
                if roll < cumulative:
                    chosen = tier
                    break
            skin_id = self._rng.choice(crate.items_of(chosen))
            tier_name = chosen
        else:
            roll = self._rng.random()
            cumulative = 0.0
            chosen = None
            for tier in TIER_ORDER:
                cumulative += TIER_ODDS[tier]
                if roll < cumulative and crate.items_of(tier):
                    chosen = tier
                    break
            if chosen:
                skin_id = self._rng.choice(crate.items_of(chosen))
                tier_name = chosen
            else:
                if not crate.rare:
                    skin_id = self._rng.choice(crate.items_of("Covert"))
                    tier_name = "Covert"
                else:
                    skin_id = self._rng.choice(crate.rare)
                    tier_name = "Extraordinary"
        item = self.items.get(skin_id)
        if item is None:
            raise RuntimeError(f"快照缺少皮肤 {skin_id}")
        stattrak = bool(
            not souvenir
            and item.stattrak
            and item.category != "Gloves"
            and self._rng.random() < STATTRAK_CHANCE
        )
        if item.min_float is None or item.max_float is None:
            float_value = 0.0
            wear = ""
        else:
            float_value = roll_float(item.min_float, item.max_float, self._rng)
            wear = wear_for_float(float_value)
        price = self._price_of(item, wear, stattrak, souvenir)
        return {
            "skin_id": item.id,
            "name": item.name,
            "name_cn": item.name_cn,
            "wear": wear,
            "float": float_value,
            "stattrak": stattrak,
            "rarity": item.rarity_name if tier_name != "Extraordinary" else "Extraordinary",
            "color": item.color,
            "price_cny": price,
            "image_url": item.image,
            "tier": tier_name,
            "souvenir": souvenir,
        }

    def _case_number(self, crate: CrateInfo) -> int:
        return list(self.cases).index(crate.name) + 1 if crate.name in self.cases else 0

    def _resolve_case(self, query: str) -> CrateInfo | None:
        """支持：序号（1 起）、中文名、英文名、中文别名、英文片段。"""
        query = query.strip().lower()
        if not query:
            return None
        if query.isdigit():
            index = int(query)
            names = list(self.cases)
            if 1 <= index <= len(names):
                return self.cases[names[index - 1]]
        for crate in self.cases.values():
            if crate.name.lower() == query or crate.cn_name.lower() == query:
                return crate
        for crate in self.cases.values():
            for alias in CASE_ALIASES.get(crate.name, ()):
                if alias == query:
                    return crate
        exact_cn = [crate for crate in self.cases.values() if query in crate.cn_name.lower()]
        if len(exact_cn) == 1:
            return exact_cn[0]
        matches = [crate for crate in self.cases.values() if query in crate.name.lower()]
        if len(matches) == 1:
            return matches[0]
        alias_matches = [
            crate
            for crate in self.cases.values()
            if any(query in alias for alias in CASE_ALIASES.get(crate.name, ()))
        ]
        return alias_matches[0] if len(alias_matches) == 1 else None

    async def _list_cases(self, event, arg: str) -> None:
        page = int(arg) if arg.isdigit() else 1
        size = 8
        names = list(self.cases)
        chunk = names[(page - 1) * size : page * size]
        if not chunk:
            await self._send(event, "没有更多箱子了")
            return
        images = await asyncio.gather(*[self.images.get(self.cases[name].image) for name in chunk])
        rows = []
        for name, image_path in zip(chunk, images):
            crate = self.cases[name]
            case_price = self._case_price(crate)
            rows.append(
                {
                    "title": f"{self._case_number(crate)}. {crate.cn_name}",
                    "meta": (
                        f"纪念包 ¥{case_price:.2f} · 无钥匙"
                        if case_price and crate.tier_odds
                        else f"箱 ¥{case_price:.2f} + 钥匙 ¥{self._key_price(crate):.2f}"
                        if case_price
                        else "暂无报价"
                    ),
                    "right": f"{self._coins(case_price + self._key_price(crate))} 币/次"
                    if case_price
                    else "—",
                    "color": "#d9a441",
                    "image": image_path,
                }
            )
        total = len(names)
        path = render_panel(
            self.render_dir / f"list_{event.sender.user_id}_{int(time.time())}.png",
            title="CS2 武器箱",
            subtitle=f"第 {page}/{math.ceil(total / size)} 页 · 共 {total} 个 · 价格 CNY 实时",
            rows=rows,
            footer="开箱 = 箱价 + 钥匙；cs详情 <编号> 看概率，cs价值表 <编号> 看每件价值",
        )
        await self._send(event, None, path)

    async def _detail(self, event, arg: str) -> None:
        crate = self._resolve_case(arg)
        if crate is None:
            await self._send(event, "用法：cs详情 <箱子名|编号>（可先用 cs箱子 查看列表）")
            return
        lines = [
            f"{self._case_number(crate)}. {crate.cn_name}",
            ("纪念包 · 无需钥匙" if crate.tier_odds else f"上线 {crate.first_sale_date}"),
        ]
        if crate.tier_odds:
            lines.append("概率：Valve 公示纪念包模型（六档）")
        for tier in list(crate.tier_odds) or list(TIER_ORDER):
            ids = crate.items_of(tier)
            if not ids:
                continue
            odds = crate.tier_odds.get(tier, TIER_ODDS.get(tier, 0.0))
            lines.append(f"{RARITY_LABEL.get(tier, tier)} {odds*100:.3f}% · {len(ids)} 件")
        if not crate.tier_odds:
            lines.append(f"罕见（刀/手套） {RARE_ODDS*100:.2f}% · {len(crate.rare)} 件")
        if crate.rare:
            sample = "、".join(self.items[i].name for i in crate.rare[:3] if i in self.items)
            lines.append(f"  例如：{sample}")
        case_price = self._case_price(crate)
        lines.append(
            f"箱价 ¥{case_price:.2f}（{self._coins(case_price)} 币）· 钥匙 ¥{self._key_price(crate):.2f}"
            if case_price
            else "箱价：暂无报价"
        )
        blocked = self._case_blocked(crate)
        if blocked:
            lines.append(f"⚠ {blocked}")
        lines.append(
            "概率来源：Valve 纪念包公示（六档，档内均分）"
            if crate.tier_odds
            else "概率来源：Valve 公示 79.92/15.98/3.20/0.64/0.26"
        )
        await self._send(event, "\n".join(lines))

    def _value_range(
        self, item: SkinItem, souvenir: bool = False
    ) -> tuple[float | None, float | None]:
        """返回该皮肤在所有磨损档上的最低/最高回收价（不区分 StatTrak）。"""
        if item.min_float is None or item.max_float is None:
            price = self._price_of(item, "", False, souvenir)
            return (price, price) if price else (None, None)
        values = [self._price_of(item, wear, False, souvenir) for wear, _, _ in WEAR_BANDS]
        known = [value for value in values if value is not None]
        if not known:
            return (None, None)
        return (min(known), max(known))

    async def _value_table(self, event, arg: str) -> None:
        parts = arg.split()
        page = int(parts[-1]) if parts and parts[-1].isdigit() else 1
        query = " ".join(parts[:-1]) if len(parts) > 1 and parts[-1].isdigit() else arg
        crate = self._resolve_case(query)
        if crate is None:
            await self._send(event, "用法：cs价值表 <箱子|编号> [页码]")
            return
        entries: list[tuple[str, SkinItem]] = []
        for tier in TIER_ORDER:
            for skin_id in crate.items_of(tier):
                item = self.items.get(skin_id)
                if item:
                    entries.append((tier, item))
        for skin_id in crate.rare:
            item = self.items.get(skin_id)
            if item:
                entries.append(("Extraordinary", item))
        size = 12
        total_pages = max(1, math.ceil(len(entries) / size))
        page = min(max(1, page), total_pages)
        chunk = entries[(page - 1) * size : page * size]
        images = await asyncio.gather(*[self.images.get(item.image) for _, item in chunk])
        rows = []
        for (tier, item), image_path in zip(chunk, images):
            low, high = self._value_range(item, bool(crate.tier_odds))
            wear_text = f"¥{low:.2f}~¥{high:.2f}" if low is not None and high is not None else "暂无报价"
            if low is not None and high is not None and abs(high - low) < 0.01:
                wear_text = f"¥{low:.2f}"
            rows.append(
                {
                    "title": item.name_cn or item.name,
                    "meta": f"{RARITY_LABEL.get(tier, rarity_label(tier))} · {item.collection_name_cn or item.collection_name or item.weapon} · {wear_text}",
                    "right": f"{self._coins(low)} 币起" if low is not None else "—",
                    "color": item.color,
                    "image": image_path,
                }
            )
        header = await self.images.get(crate.image)
        path = render_panel(
            self.render_dir / f"value_{event.sender.user_id}_{int(time.time())}.png",
            title=f"{crate.cn_name} · 价值表",
            subtitle=f"第 {page}/{total_pages} 页 · 共 {len(entries)} 件 · CNY 实时回收价",
            rows=rows,
            footer="价格随市场波动；带 StatTrak 的出货按同名挂牌价另行计算",
            header_image=header,
        )
        await self._send(event, None, path)

    # ------------------------------------------------------------------ 买

    # ------------------------------------------------------------------ 开

    async def _open(self, event, arg: str) -> None:
        user_id = str(event.sender.user_id)
        group_id = str(event.group_id)
        parts = arg.split()
        if not parts:
            await self._send(event, "用法：cs开箱 <箱子|编号> [1-10]")
            return
        crate = self._resolve_case(parts[0])
        if crate is None:
            await self._send(event, f"找不到箱子：{parts[0]}")
            return
        count = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
        count = max(1, min(count, int(self.cfg("max_batch"))))
        blocked = self._case_blocked(crate)
        if blocked:
            await self._send(event, blocked)
            return
        if self.store.count(user_id) + count > 200:
            await self._send(event, "背包已满（200 件），先 cs出售 一些再来")
            return

        case_price = self._case_price(crate) or 0.0
        key_price = self._key_price(crate)
        unit_case = self._coins(case_price)
        unit_key = self._coins(key_price) if key_price else 0
        unit_cost = unit_case + unit_key
        cost = unit_cost * count
        parts = [f"箱 {self.money(unit_case)}"]
        if unit_key:
            parts.append(f"钥匙 {self.money(unit_key)}")
        await self._send(
            event,
            f"本次开箱 {crate.cn_name} × {count}："
            + " + ".join(parts)
            + f" = 合计 {self.money(cost)}",
        )
        if cost and not self._pay_for_open(user_id, cost, "cs_open"):
            await self._send(event, f"余额不足，本次需要 {self.money(cost)}")
            return

        results = []
        for _ in range(count):
            rolled = self._roll_case(crate)
            item_id = self.store.add_item(
                user_id,
                group_id,
                skin_id=rolled["skin_id"],
                name=rolled["name"],
                name_cn=rolled["name_cn"],
                wear=rolled["wear"],
                float_value=rolled["float"],
                stattrak=rolled["stattrak"],
                rarity=rolled["rarity"],
                color=rolled["color"],
                market_hash_name=market_hash_name(
                    rolled["name"],
                    rolled["wear"] or None,
                    stattrak=rolled["stattrak"],
                    souvenir=rolled.get("souvenir", False),
                ),
                price_cny=rolled["price_cny"],
            )
            self.store.log_open(
                user_id,
                crate.name,
                unit_cost,
                rolled["name_cn"] or rolled["name"],
                rolled["rarity"],
                rolled["price_cny"],
            )
            rolled["item_id"] = item_id
            results.append(rolled)

        paths = await self._download_images([r["image_url"] for r in results])
        rows = []
        for rolled, image_path in zip(results, paths):
            base_name = rolled["name_cn"] or rolled["name"]
            label = f"StatTrak™ {base_name}" if rolled["stattrak"] else base_name
            rows.append(
                {
                    "title": f"#{rolled['item_id']} {label}",
                    "meta": f"{rarity_label(rolled['rarity'])} · {wear_label(rolled['wear'])} · 磨损 {rolled['float']:.4f}",
                    "right": f"¥{rolled['price_cny']:.2f}" if rolled["price_cny"] else "无市价",
                    "color": rolled["color"],
                    "image": image_path,
                }
            )
        header = await self.images.get(crate.image)
        path = render_panel(
            self.render_dir / f"open_{user_id}_{int(time.time())}.png",
            title=f"{crate.cn_name} × {count}",
            subtitle=f"本次花费 {self.money(cost)}",
            rows=rows,
            footer="概率：军规级 79.92% / 受限 15.98% / 保密 3.20% / 隐秘 0.64% / 非凡（刀/手套）0.26% · StatTrak 约 10%",
            header_image=header,
        )
        await self._send(event, f"{crate.cn_name} × {count}：花费 {self.money(cost)}", path)

    def liquidate(self, user_id: str) -> tuple[int, int]:
        """变卖全部物品：按当前回收价折算金币，返回 (金币, 件数)。物品会被清空。"""
        rows = self.store.items_sorted(user_id, limit=200)
        coins = 0
        count = 0
        for row in rows:
            price = self._sell_price(row["name"], row["wear"], bool(row["stattrak"]))
            if price:
                coins += self._coins(price)
            count += 1
        self.store.clear_user(user_id)
        return coins, count

    async def _download_images(self, urls: Iterable[str | None]) -> list[Path | None]:
        urls = list(urls)
        tasks = [self.images.get(url) for url in urls if url]
        if not tasks:
            return [None] * len(urls)
        fetched = await asyncio.gather(*tasks)
        iterator = iter(fetched)
        return [next(iterator) if url else None for url in urls]

    # ------------------------------------------------------------------ 背包 / 详情

    async def _inventory(self, event, arg: str) -> None:
        user_id = str(event.sender.user_id)
        page = int(arg) if arg.isdigit() else 1
        size = int(self.cfg("page_size"))
        total = self.store.count(user_id)
        if total == 0:
            await self._send(event, "背包是空的，试试 cs开箱 <箱子|编号>")
            return
        ordered = self.store.items_sorted(user_id, limit=200)
        rows_raw = ordered[(page - 1) * size : page * size]
        if not rows_raw:
            await self._send(event, "没有更多物品了")
            return
        images = await asyncio.gather(
            *[
                self.images.get(self.items[row["skin_id"]].image) if row["skin_id"] in self.items else _none()
                for row in rows_raw
            ]
        )
        items = []
        seq_offset = (page - 1) * size
        for index, (row, image_path) in enumerate(zip(rows_raw, images)):
            base_name = row.get("name_cn") or row["name"]
            name = f"StatTrak™ {base_name}" if row["stattrak"] else base_name
            items.append(
                {
                    "badge": f"#{seq_offset + index + 1}",
                    "title": name,
                    "meta": f"{RARITY_LABEL.get(row['rarity'], rarity_label(row['rarity']))} · {wear_label(row['wear'])}",
                    "right": f"¥{row['price_cny']:.2f}" if row["price_cny"] else "无市价",
                    "color": row["color"],
                    "image": image_path,
                }
            )
        path = render_grid(
            self.render_dir / f"bag_{user_id}_{int(time.time())}.png",
            title=f"我的 CS2 背包（{total} 件）",
            subtitle=f"第 {page}/{math.ceil(total / size)} 页 · 每页 {size} 件",
            items=items,
            footer="cs物品 <序号> 详情 · cs出售 <序号|all> 回收 · cs汰换 序号×10 或 军规",
        )
        await self._send(event, None, path)

    async def _item_detail(self, event, arg: str) -> None:
        user_id = str(event.sender.user_id)
        if not arg.isdigit():
            await self._send(event, "用法：cs物品 <序号>")
            return
        ordered = self.store.items_sorted(user_id, limit=200)
        index = int(arg) - 1
        row = ordered[index] if 0 <= index < len(ordered) else None
        if row is None:
            await self._send(event, "没有这个序号的物品（序号见 cs背包）")
            return
        base_name = row.get("name_cn") or row["name"]
        name = f"StatTrak™ {base_name}" if row["stattrak"] else base_name
        sell = self._sell_price(row["name"], row["wear"], bool(row["stattrak"]))
        lines = [
            f"序号 {arg} · {name}",
            f"稀有度：{RARITY_LABEL.get(row['rarity'], row['rarity'])}",
            f"外观：{wear_label(row['wear'])}（{row['wear'] or '无磨损'}）· 磨损值 {row['float_value']:.6f}",
            f"市场名：{row['market_hash_name']}",
            f"当前回收价：¥{sell:.2f}（{self._coins(sell)} 币）" if sell else "当前回收价：暂无市价",
            f"获得时间：{time.strftime('%Y-%m-%d %H:%M', time.localtime(row['acquired_at']))}",
        ]
        await self._send(event, "\n".join(lines))

    async def _sell(self, event, arg: str) -> None:
        user_id = str(event.sender.user_id)
        parts = arg.split()
        if not parts:
            await self._send(event, "用法：cs出售 <编号|all>")
            return
        target = parts[0]
        ordered = self.store.items_sorted(user_id, limit=200)
        if target.lower() == "all":
            rows = ordered
        elif target.isdigit():
            index = int(target) - 1
            rows = [ordered[index]] if 0 <= index < len(ordered) else []
        else:
            await self._send(event, "用法：cs出售 <序号|all>")
            return
        if not rows:
            await self._send(event, "没有这个序号的物品（序号见 cs背包）")
            return
        sold = 0
        gained = 0
        paid_to_loan = 0
        skipped = 0
        for row in rows:
            item = SkinItem(
                id=row["skin_id"],
                name=row["name"],
                weapon="",
                category="",
                rarity_id="",
                rarity_name=row["rarity"],
                color=row["color"],
                min_float=None,
                max_float=None,
                stattrak=False,
                souvenir=False,
                paint_index=None,
                image=None,
            )
            price = self._price_of(item, row["wear"], bool(row["stattrak"]))
            if not price:
                skipped += 1
                continue
            coins = self._coins(price)
            if coins <= 0:
                skipped += 1
                continue
            if not self.store.remove_items(user_id, [row["id"]]):
                continue
            landed = self._credit_with_loan(user_id, coins, "cs_sell")
            self.store.log_market(user_id, row["name"], "sell", coins)
            sold += 1
            gained += coins
            paid_to_loan += coins - landed
        text = f"已出售 {sold} 件，共 {gained} 金币"
        if paid_to_loan:
            text += f"（其中 {paid_to_loan} 自动还贷）"
        if skipped:
            text += f"；{skipped} 件无市价未处理"
        await self._send(event, text)

    # ------------------------------------------------------------------ 汰换

    def _parse_tradeup_arg(self, arg: str) -> tuple[list[int] | None, str | None]:
        """返回 (编号列表, 稀有度) 二选一；无法解析时返回 (None, None)。

        支持：``1 2 3`` / ``1,2,3`` / ``12-21``（区间）/ ``军规``（自动挑 10 件）。
        """
        text = arg.strip()
        if not text:
            return None, None
        for word, tier in self._RARITY_ALIAS.items():
            if word in text:
                return None, tier
        ids: list[int] = []
        for token in re.split(r"[,\s]+", text):
            if not token:
                continue
            if "-" in token:
                left, _, right = token.partition("-")
                if left.isdigit() and right.isdigit():
                    start, end = int(left), int(right)
                    if 0 < end - start < 200:
                        ids.extend(range(start, end + 1))
                        continue
            if token.isdigit():
                ids.append(int(token))
        return (ids, None) if ids else (None, None)

    def _auto_pick(self, user_id: str, tier: str) -> list[dict[str, Any]]:
        """按稀有度自动挑 10 件：优先非 StatTrak、按当前回收价从低到高。"""
        rows = [
            row
            for row in self.store.items_sorted(user_id, limit=200)
            if row["rarity"] == tier
        ]
        plain = [row for row in rows if not row["stattrak"]]
        pool = plain if len(plain) >= 10 else rows
        pool.sort(
            key=lambda row: self._sell_price(row["name"], row["wear"], bool(row["stattrak"])) or 0
        )
        return pool[:10]

    def _eligible_collections(self, tier: str) -> dict[str, list[SkinItem]]:
        target = next_tier(tier)
        if target is None:
            return {}
        result: dict[str, list[SkinItem]] = {}
        for item in self.items.values():
            if item.rarity_name != target or not item.collection_id:
                continue
            if not is_tradeup_compatible(item.category):
                continue
            result.setdefault(item.collection_id, []).append(item)
        return result

    async def _tradeup(self, event, arg: str, *, preview: bool) -> None:
        user_id = str(event.sender.user_id)
        ids, auto_tier = self._parse_tradeup_arg(arg)
        if auto_tier:
            rows = self._auto_pick(user_id, auto_tier)
            if len(rows) < 10:
                await self._send(
                    event,
                    f"背包里「{RARITY_LABEL.get(auto_tier, auto_tier)}」只有 {len(rows)} 件，汰换需要 10 件",
                )
                return
            ids = [row["id"] for row in rows]
        elif not ids:
            await self._send(
                event,
                "用法：cs汰换 1 2 3 ... 10 / cs汰换 1-10 / cs汰换 军规（序号见 cs背包）",
            )
            return
        if len(ids) != 10:
            await self._send(event, f"汰换合同需要正好 10 个序号，你给了 {len(ids)} 个")
            return
        perm = self.store.items_sorted(user_id, limit=200)
        mapped: list[int] = []
        missing: list[str] = []
        for seq in ids:
            if 1 <= seq <= len(perm):
                mapped.append(int(perm[seq - 1]["id"]))
            else:
                missing.append(str(seq))
        if missing:
            await self._send(
                event,
                "这些序号超出你的背包范围：" + "、".join(missing[:10]) + "（序号见 cs背包）",
            )
            return
        rows = self.store.items_by_ids(user_id, mapped)
        if len(rows) != 10:
            await self._send(event, "这些物品已经不在背包里了")
            return
        tiers = {row["rarity"] for row in rows}
        stattraks = {bool(row["stattrak"]) for row in rows}
        if len(tiers) != 1:
            await self._send(event, "10 件必须是同一稀有度")
            return
        if len(stattraks) != 1:
            await self._send(event, "StatTrak 与普通物品不能混用")
            return
        tier = tiers.pop()
        if tier not in TIER_ORDER or tier == "Covert":
            await self._send(event, "隐秘及以上不能参与汰换（刀/手套不可通过合同获得）")
            return
        eligible = self._eligible_collections(tier)
        inputs = [
            TradeupInput(
                float_value=float(row["float_value"]),
                tier=tier,
                stattrak=bool(row["stattrak"]),
                collection_id=self.items[row["skin_id"]].collection_id if row["skin_id"] in self.items else "",
            )
            for row in rows
        ]
        weights = collection_weights(inputs, {key: len(value) for key, value in eligible.items()})
        if not weights:
            await self._send(event, "这些物品所在的收藏品没有更高一档的皮肤，无法汰换")
            return
        total = sum(weights.values())
        distribution = {key: value / total for key, value in weights.items()}

        if preview:
            target_tier = next_tier(tier) or ""
            tier_cn = RARITY_LABEL.get(tier, tier)
            target_cn = RARITY_LABEL.get(target_tier, target_tier)
            lines = [f"汰换预览：{tier_cn} → {target_cn}（{len(rows)} 件）"]
            for collection_id, prob in sorted(distribution.items(), key=lambda kv: -kv[1]):
                pool = eligible[collection_id]
                collection_name = pool[0].collection_name_cn or pool[0].collection_name or collection_id
                values = []
                for item in pool:
                    out_float = tradeup_float(
                        inputs, float(item.min_float or 0), float(item.max_float or 1)
                    )
                    out_wear = wear_for_float(out_float) if item.min_float is not None else ""
                    price = self._price_of(item, out_wear, stattraks and item.stattrak)
                    if price:
                        values.append(price)
                avg = sum(values) / len(values) if values else 0.0
                lines.append(
                    f"{collection_name}：{prob:.0%} 概率 · {len(pool)} 件 · 出货均价 ¥{avg:.2f}"
                )
            lines.append(
                "发送 cs汰换 军规/受限/保密 可直接执行，或带上这 10 个序号"
            )
            await self._send(event, "\n".join(lines))
            return

        collection_id = self._rng.choices(
            list(distribution), weights=[distribution[key] for key in distribution]
        )[0]
        pool = eligible[collection_id]
        target = self._rng.choice(pool)
        float_value = tradeup_float(
            inputs, float(target.min_float or 0), float(target.max_float or 1)
        )
        wear = wear_for_float(float_value) if target.min_float is not None else ""
        stattrak = stattraks.pop()
        price = self._price_of(target, wear, stattrak and target.stattrak)

        if not self.store.remove_items(user_id, ids):
            await self._send(event, "汰换失败：物品已被处理")
            return
        item_id = self.store.add_item(
            user_id,
            str(event.group_id),
            skin_id=target.id,
            name=target.name,
            name_cn=target.name_cn,
            wear=wear,
            float_value=float_value,
            stattrak=stattrak and target.stattrak,
            rarity=target.rarity_name,
            color=target.color,
            market_hash_name=market_hash_name(target.name, wear or None, stattrak=stattrak and target.stattrak),
            price_cny=price,
            source="tradeup",
        )
        rows_out = [
            {
                "title": f"#{item_id} {target.name_cn or target.name}",
                "meta": f"{target.collection_name_cn or target.collection_name} · {wear_label(wear)} · 磨损 {float_value:.4f}",
                "right": f"¥{price:.2f}" if price else "无市价",
                "color": target.color,
            }
        ]
        path = render_panel(
            self.render_dir / f"tradeup_{user_id}_{int(time.time())}.png",
            title="汰换合同完成",
            subtitle=(
                f"10 × {RARITY_LABEL.get(tier, tier)} → 1 × "
                f"{RARITY_LABEL.get(target.rarity_name, target.rarity_name)}"
            ),
            rows=rows_out,
            footer=f"选中收藏品：{target.collection_name}（{distribution[collection_id]:.0%}）",
        )
        await self._send(
            event,
            f"已消耗 10 件{RARITY_LABEL.get(tier, tier)}，产出「{target.name_cn or target.name}」"
            f"（{wear_label(wear)}）",
            path,
        )

    # ------------------------------------------------------------------ 借贷 / 重生

    async def _price_query(self, event, arg: str) -> None:
        query = arg.strip().lower()
        if not query:
            await self._send(event, "用法：cs查价 <关键词>")
            return
        crate = self._resolve_case(query)
        needles = [query]
        if crate is not None:
            needles.insert(0, crate.market_hash_name.lower())
        hits: list[str] = []
        for name in self.prices.quotes:
            lowered = name.lower()
            if any(needle in lowered for needle in needles):
                hits.append(name)
            if len(hits) >= 8:
                break
        if not hits:
            await self._send(event, "没有找到匹配的市场名")
            return
        lines = ["市场查价（Skinport CNY）"]
        for name in hits:
            quote = self.prices.quote(name)
            if not quote:
                continue
            lines.append(
                f"{name}：买 ¥{quote.min_price:.2f} / 中位 ¥{(quote.median_price or 0):.2f} / 在售 {quote.quantity}"
            )
        await self._send(event, "\n".join(lines))

    async def _stats(self, event) -> None:
        user_id = str(event.sender.user_id)
        stats = self.store.stats(user_id)
        lines = [
            f"开箱战绩 · {user_id}",
            f"开箱次数：{stats['opens']}",
            f"累计花费：{stats['spent']} 金币",
        ]
        best = stats.get("best")
        if best:
            lines.append(
                f"最贵掉落：{best['item_name']}（{best['rarity']}）"
                + (f" ¥{best['price_cny']:.2f}" if best.get("price_cny") else "")
            )
        loan_status = self.loan.status(user_id)
        lines.append(f"欠款：{loan_status['debt']} 金币 · 重生：{loan_status['rebirths']} 次")
        lines.append(f"背包：{self.store.count(user_id)} 件")
        await self._send(event, "\n".join(lines))

    async def _leaderboard(self, event) -> None:
        rows = self.store.leaderboard(5)
        if not rows:
            await self._send(event, "还没有人开过箱")
            return
        lines = ["CS2 开箱榜（按次数）"]
        for index, row in enumerate(rows, start=1):
            best = self.store.best_drop(str(row["user_id"]))
            suffix = f" · 最贵 {best['item_name']}" if best else ""
            lines.append(f"{index}. {row['user_id']}：{row['opens']} 次 / {row['spent']} 币{suffix}")
        await self._send(event, "\n".join(lines))

    # ------------------------------------------------------------------ 后台任务

    async def _refresh_prices(self) -> None:
        try:
            quotes = await fetch_skinport(730)
        except Exception:  # noqa: BLE001 - 拉价失败保持旧价
            return
        if not quotes:
            return
        merged = dict(self.prices.quotes)
        merged.update(quotes)
        self.prices = PriceBook(merged, time.time())
        self._ev_cache.clear()
        save_prices(self.price_path, merged, fetched_at=time.time())
