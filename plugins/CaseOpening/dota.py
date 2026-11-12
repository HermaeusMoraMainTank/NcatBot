"""Dota 2 宝瓶模块（CaseOpening 插件内部）。"""

from __future__ import annotations

import asyncio
import math
import random
import time
from pathlib import Path
from typing import Any

from common.case_data import (
    DOTA_FILE,
    PRICES_570,
    SNAPSHOT_DIR,
    ImageCache,
    PriceBook,
    Treasure,
    load_dota_snapshot,
)
from common.case_data.render import render_grid, render_panel
from common.case_data.rules import (
    advance_counters,
    guaranteed_row,
    odds_for_open,
    pity_row,
    regular_pick,
    roll_dota_bonus,
)

from .config import DEFAULT_CONFIG, DOTA_KEYS

TIER_LABEL = {
    "Regular": "普通",
    "Rare": "珍稀",
    "Very Rare": "非常珍稀",
    "Ultra Rare": "珍稀超凡",
    "Cosmically Rare": "宇宙珍稀",
    "Bonus": "累计奖励",
}

TIER_COLOR = {
    "Regular": "#8fa3bd",
    "Rare": "#9fde4d",
    "Very Rare": "#fd9e56",
    "Ultra Rare": "#fddb88",
    "Cosmically Rare": "#b85af3",
    "Bonus": "#7fd1ff",
}

from .store_dota import TreasureStore


class DotaCase:
    """Dota 2 宝瓶逻辑；由 CaseOpening 注入配置、经济与消息能力。"""

    def __init__(self, owner) -> None:
        self.plugin = owner
        self.loan = owner.loan
        self.data_dir = Path(owner.data_dir) / "dota"
        self.image_dir = self.data_dir / "images"
        self.render_dir = self.data_dir / "cards"
        self.render_dir.mkdir(parents=True, exist_ok=True)
        self.treasures, _ = load_dota_snapshot(SNAPSHOT_DIR / DOTA_FILE)
        self.prices = PriceBook.from_file(SNAPSHOT_DIR / PRICES_570)
        self.store = TreasureStore(self.data_dir / "dota_treasure.db")
        self.images = ImageCache(self.image_dir)
        self._rng = random.Random()
        self.name_cn = self._load_name_map()

    def cfg(self, key: str):
        plugin = getattr(self, "plugin", None)
        if plugin is not None:
            return plugin.dota_cfg(key)
        return DEFAULT_CONFIG.get(DOTA_KEYS.get(key, key), DEFAULT_CONFIG.get(key))

    def money(self, coins: int) -> str:
        return self.plugin.money(coins)

    def _economy(self):
        return self.plugin._economy()

    def _charge(self, user_id: str, coins: int, reason: str) -> bool:
        return self.plugin._charge(user_id, coins, reason)

    def _credit(self, user_id: str, coins: int, reason: str) -> bool:
        return self.plugin._credit(user_id, coins, reason)

    def _pay_for_open(self, user_id: str, coins: int, reason: str) -> bool:
        return self.plugin._pay_for_open(user_id, coins, reason)

    async def _send(self, event, text: str | None = None, image: Path | None = None) -> None:
        await self.plugin._send(event, text, image)

    def get_plugin(self, name: str):
        return self.plugin.get_plugin(name)

    def close(self) -> None:
        self.store.close()

    async def dispatch(self, event, text: str) -> None:
        if text.startswith("dota宝瓶"):
            await self._list(event, text[len("dota宝瓶"):].strip())
        elif text.startswith("dota详情"):
            await self._detail(event, text[len("dota详情"):].strip())
        elif text.startswith("dota开瓶"):
            await self._open(event, text[len("dota开瓶"):].strip())
        elif text.startswith("dota一轮"):
            await self._pity(event, text[len("dota一轮"):].strip())
        elif text.startswith("dota饰品"):
            await self._inventory(event, text[len("dota饰品"):].strip())
        elif text.startswith("dota卖"):
            await self._sell(event, text[len("dota卖"):].strip())
        elif text.startswith("dota战绩"):
            await self._stats(event)
        elif text.startswith("dota榜"):
            await self._leaderboard(event)

    def _load_name_map(self) -> dict[str, str]:
        """可选的中英对照表：data/CaseOpening/dota/names_cn.json（{英文名: 中文名}）。"""
        import json

        path = self.data_dir / "names_cn.json"
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - 坏文件就不加载
            return {}
        return {str(k): str(v) for k, v in data.items() if k and v}

    def _cn(self, name: str) -> str:
        return getattr(self, "name_cn", {}).get(name, name)

    def _coins(self, cny: float | None) -> int:
        if cny is None:
            return 0
        return math.ceil(float(cny) * float(self.cfg("coin_per_cny")))

    def _pay_for_open(self, user_id: str, coins: int, reason: str) -> bool:
        """开瓶付款：先用借贷额度，不够再扣钱包。"""
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

    def _resolve(self, query: str) -> tuple[Treasure | None, list[str]]:
        """支持：序号（1 起，对应 dota宝瓶 列表编号）、中文名、英文名、片段。"""
        query = query.strip().lower()
        if not query:
            return None, []
        if query.isdigit():
            names = list(self.treasures)
            index = int(query)
            if 1 <= index <= len(names):
                return self.treasures[names[index - 1]], []
            return None, [f"编号只有 1–{len(names)}，先发 dota宝瓶 看列表"]
        exact = [
            treasure
            for treasure in self.treasures.values()
            if treasure.name.lower() == query or treasure.cn_name == query
        ]
        if len(exact) == 1:
            return exact[0], []
        hits = [
            treasure
            for treasure in self.treasures.values()
            if query in treasure.name.lower() or query in treasure.cn_name.lower()
        ]
        if len(hits) == 1:
            return hits[0], []
        return None, [f"{t.cn_name}（{t.name}）" for t in hits[:6]]

    def _cosmic(self, treasure: Treasure) -> float:
        table = treasure.odds.get("Cosmically Rare") or []
        return float(table[0]) if table else 0.0

    def _increment_odds(self, treasure: Treasure) -> dict[str, list[float]]:
        return {tier: table for tier, table in treasure.odds.items() if tier != "Cosmically Rare"}

    async def _list(self, event, arg: str) -> None:
        page = int(arg) if arg.isdigit() else 1
        size = 8
        names = list(self.treasures)
        chunk = names[(page - 1) * size : page * size]
        if not chunk:
            await self._send(event, "没有更多宝瓶了")
            return
        images = await asyncio.gather(*[self.images.get(self.treasures[name].icon) for name in chunk])
        rows = []
        for offset, (name, image_path) in enumerate(zip(chunk, images)):
            treasure = self.treasures[name]
            tiers = "、".join(
                f"{TIER_LABEL.get(tier, tier)}{len(items)}" for tier, items in treasure.contents.items()
            )
            rows.append(
                {
                    "title": f"{(page - 1) * size + offset + 1}. {treasure.cn_name}",
                    "meta": f"¥{treasure.price_cny:.0f} · {treasure.releasedate} · {tiers}",
                    "right": f"{self._coins(treasure.price_cny)} 币",
                    "color": TIER_COLOR["Ultra Rare"] if treasure.marketable else "#7fb2ff",
                    "image": image_path,
                }
            )
        path = render_panel(
            self.render_dir / f"list_{event.sender.user_id}_{int(time.time())}.png",
            title="Dota 2 宝瓶",
            subtitle=f"第 {page}/{math.ceil(len(names) / size)} 页 · 共 {len(names)} 个 · 全部可在游戏内购买",
            rows=rows,
            footer="dota详情 <名字> 看递增概率；dota一轮 <名字> 看本轮进度",
        )
        await self._send(event, None, path)

    async def _detail(self, event, arg: str) -> None:
        treasure, hits = self._resolve(arg)
        if treasure is None:
            if hits:
                await self._send(event, "匹配到多个，请写更精确的名字：\n" + "\n".join(hits))
            else:
                await self._send(event, "用法：dota详情 <名字>（dota宝瓶 可看列表）")
            return
        lines = [
            f"{treasure.cn_name}",
            f"上线：{treasure.releasedate} · 国服售价 ¥{treasure.price_cny:.0f}（{self._coins(treasure.price_cny)} 币）",
            f"可交易：{'是' if treasure.marketable else '否'} · 内容{'可上架' if treasure.marketable else '不可上架'}",
        ]
        for tier, items in treasure.contents.items():
            if tier == "Regular":
                continue
            labels = "、".join(self._cn(item.name) for item in items)
            row = guaranteed_row(treasure.odds.get(tier) or [])
            tail = f" · 第 {row} 瓶必出" if row else ""
            lines.append(f"{TIER_LABEL.get(tier, tier)}（{len(items)} 件）{tail}\n  {labels}")
        regular = treasure.regular_items()
        if regular:
            lines.append(f"普通（{len(regular)} 件）：{'、'.join(self._cn(item.name) for item in regular)}")
        if treasure.bonus_rewards:
            bonus = "；".join(
                f"开 {row['opens']} 瓶送 {'、'.join(row['items'])}" for row in treasure.bonus_rewards
            )
            lines.append(f"累计奖励：{bonus}")
        first = treasure.odds.get("Rare") or []
        if first:
            lines.append(f"首瓶珍稀概率：{first[0]*100:.2f}%（逐瓶递增，命中清零）")
        cosmic = self._cosmic(treasure)
        if cosmic:
            lines.append(f"宇宙珍稀：固定 1/{int(1/cosmic):,}")
        await self._send(event, "\n".join(lines))

    # ------------------------------------------------------------------ 买 / 开

    async def _open(self, event, arg: str) -> None:
        user_id = str(event.sender.user_id)
        group_id = str(event.group_id)
        parts = arg.split()
        if not parts:
            await self._send(event, "用法：dota开瓶 <名字> [1-10]")
            return
        count = int(parts[-1]) if parts[-1].isdigit() else 1
        name = " ".join(parts[:-1]) if parts[-1].isdigit() else arg
        treasure, hits = self._resolve(name)
        if treasure is None:
            await self._send(
                event,
                "找不到宝瓶：编号或名字没对上，先发 dota宝瓶 看列表（第一列是编号）"
                + ("\n" + "\n".join(hits) if hits else ""),
            )
            return
        limit = int(self.cfg("max_batch"))
        raw_count = count
        count = max(1, min(count, limit))
        if self.store.count(user_id) + count > 200:
            await self._send(event, "背包已满（200 件），先 dota卖 处理一些")
            return
        unit = self._coins(treasure.price_cny)
        if not unit:
            await self._send(event, "该宝瓶未核价，暂不可开")
            return
        cost = unit * count
        await self._send(
            event,
            f"本次开瓶「{treasure.cn_name}」× {count}："
            f"{count} × {self.money(unit)} = 合计 {self.money(cost)}",
        )
        if cost and not self._pay_for_open(user_id, cost, "dota_open"):
            await self._send(event, f"余额不足，本次需要 {self.money(cost)}")
            return

        results: list[dict[str, Any]] = []
        bonus_items: list[str] = []
        for _ in range(count):
            counters = self.store.pity(user_id, treasure.name)

            # 普通档必定给一件（有重复保护）
            regular_names = [item.name for item in treasure.regular_items()]
            if not regular_names:
                regular_names = [i.name for items in treasure.contents.values() for i in items]
            name_pick = regular_pick(
                regular_names, self.store.collected(user_id, treasure.name), self._rng
            )
            regular_item = next(
                (item for items in treasure.contents.values() for item in items if item.name == name_pick),
                None,
            )
            self.store.mark_collected(user_id, treasure.name, name_pick)

            # 额外档独立判定，可以同时命中多个（例如第 50 瓶珍稀与珍稀超凡同时 100%）
            hits = roll_dota_bonus(
                self._increment_odds(treasure), counters, self._cosmic(treasure), self._rng
            )
            drops: list[tuple[str, Any]] = [("Regular", regular_item)]
            for tier in hits:
                pool = treasure.contents.get(tier) or []
                if pool:
                    drops.append((tier, self._rng.choice(pool)))

            for tier, picked in drops:
                if picked is None:
                    continue
                self.store.log_open(user_id, treasure.name, unit, tier, picked.name)
                item_id = self.store.add_item(
                    user_id,
                    group_id,
                    treasure=treasure.name,
                    item_name=picked.name,
                    tier=tier,
                    icon=picked.icon,
                    marketable=treasure.marketable,
                    price_cny=self._item_price(picked.name) if treasure.marketable else None,
                    trade_lock_days=0,
                )
                results.append(
                    {
                        "id": item_id,
                        "name": picked.name,
                        "tier": tier,
                        "icon": picked.icon,
                    }
                )

            updated = advance_counters(counters, self._increment_odds(treasure), hits)
            for tier_key, value in updated.items():
                self.store.set_pity(user_id, treasure.name, tier_key, value)

        opened_total = self.store.opens(user_id, treasure.name)
        claimed = self.store.claimed_bonus(user_id, treasure.name)
        for reward in treasure.bonus_rewards:
            threshold = int(reward.get("opens") or 0)
            if not threshold or threshold in claimed or opened_total < threshold:
                continue
            if self.store.claim_bonus(user_id, treasure.name, threshold):
                for bonus_name in reward.get("items") or []:
                    icon = next(
                        (
                            item.icon
                            for items in treasure.contents.values()
                            for item in items
                            if item.name == bonus_name
                        ),
                        None,
                    )
                    self.store.add_item(
                        user_id,
                        group_id,
                        treasure=treasure.name,
                        item_name=bonus_name,
                        tier="Bonus",
                        icon=icon,
                        marketable=False,
                        price_cny=None,
                        trade_lock_days=0,
                        source="bonus",
                    )
                    bonus_items.append(bonus_name)

        images = await asyncio.gather(*[self.images.get(row["icon"]) for row in results if row["icon"]])
        image_iter = iter(images)
        rows = []
        for row in results:
            tier = row["tier"]
            rows.append(
                {
                    "title": f"#{row['id']} {self._cn(row['name'])}",
                    "meta": f"{TIER_LABEL.get(tier, tier)}" + ("" if treasure.marketable else " · 不可上架"),
                    "right": "收藏" if not treasure.marketable else "",
                    "color": TIER_COLOR.get(tier, "#8fa3bd"),
                    "image": next(image_iter) if row["icon"] else None,
                }
            )
        spend = unit * count
        footer = f"成本 {self.money(spend)} · 内容不可交易，开瓶为纯消耗"
        if bonus_items:
            footer += f" · 累计奖励：{'、'.join(bonus_items)}"
        header = await self.images.get(treasure.icon)
        path = render_panel(
            self.render_dir / f"open_{user_id}_{int(time.time())}.png",
            title=f"{treasure.cn_name} × {count}",
            subtitle=f"国服售价 ¥{treasure.price_cny:.0f}/瓶 · 每瓶 1 件",
            rows=rows,
            footer=footer,
            header_image=header,
        )
        summary = f"「{treasure.cn_name}」× {count}：成本 {self.money(spend)}"
        if raw_count > limit:
            summary += f"（单次上限 {limit}，已按 {count} 瓶算）"
        if bonus_items:
            summary += f"，累计奖励 {'、'.join(bonus_items)}"
        await self._send(event, summary, path)

    def _item_price(self, name: str) -> float | None:
        return self.prices.sell_price(name, float(self.cfg("sell_fee")))

    # ------------------------------------------------------------------ 借贷 / 重生

    async def _pity(self, event, arg: str) -> None:
        user_id = str(event.sender.user_id)
        treasure, hits = self._resolve(arg.split()[0] if arg.split() else "")
        if treasure is None:
            await self._send(event, "用法：dota一轮 <名字>" + ("\n" + "\n".join(hits) if hits else ""))
            return
        counters = self.store.pity(user_id, treasure.name)
        collected = self.store.collected(user_id, treasure.name)
        regular_total = len(treasure.regular_items())
        lines = [f"{treasure.cn_name} · 本轮进度"]
        for tier in ("Rare", "Very Rare", "Ultra Rare"):
            table = treasure.odds.get(tier) or []
            if not table:
                continue
            counter = counters.get(tier, 1)
            row = pity_row(table, counter)
            prob = odds_for_open(table, counter)
            guard = guaranteed_row(table)
            shown = f"{prob*100:.4f}%" if 0 < prob < 0.001 else f"{prob*100:.2f}%"
            lines.append(
                f"{TIER_LABEL.get(tier, tier)}：本轮第 {row} 次 → {shown}"
                + (f"（第 {guard} 次必出）" if guard else "")
            )
        cosmic = self._cosmic(treasure)
        if cosmic:
            lines.append(f"宇宙珍稀：固定 {cosmic*100:.4f}%")
        lines.append(f"已收集普通：{len(collected)}/{regular_total}")
        lines.append(f"累计开启：{self.store.opens(user_id, treasure.name)} 瓶")
        await self._send(event, "\n".join(lines))

    # ------------------------------------------------------------------ 背包

    async def _inventory(self, event, arg: str) -> None:
        user_id = str(event.sender.user_id)
        page = int(arg) if arg.isdigit() else 1
        size = int(self.cfg("page_size"))
        total = self.store.count(user_id)
        if total == 0:
            await self._send(event, "背包是空的，试试 dota开瓶 <宝瓶>")
            return
        ordered = self.store.items_sorted(user_id, limit=200)
        rows_raw = ordered[(page - 1) * size : page * size]
        if not rows_raw:
            await self._send(event, "没有更多物品了")
            return
        images = await asyncio.gather(*[self.images.get(row["icon"]) for row in rows_raw])
        items = []
        seq_offset = (page - 1) * size
        for index, (row, image_path) in enumerate(zip(rows_raw, images)):
            treasure_cn = self.treasures[row["treasure"]].cn_name if row["treasure"] in self.treasures else row["treasure"]
            meta = f"{TIER_LABEL.get(row['tier'], row['tier'])} · {treasure_cn}"
            items.append(
                {
                    "badge": f"#{seq_offset + index + 1}",
                    "title": self._cn(row["item_name"]),
                    "meta": meta,
                    "right": "可卖" if row["marketable"] else "收藏",
                    "color": TIER_COLOR.get(row["tier"], "#8fa3bd"),
                    "image": image_path,
                }
            )
        path = render_grid(
            self.render_dir / f"bag_{user_id}_{int(time.time())}.png",
            title=f"我的 Dota 2 饰品（{total} 件）",
            subtitle=f"第 {page}/{math.ceil(total / size)} 页 · 每页 {size} 件",
            items=items,
            footer="不可上架内容只能收藏 · 序号看卡片角标 · dota一轮 查看本轮进度",
        )
        await self._send(event, None, path)

    # ------------------------------------------------------------------ 出售

    def liquidate(self, user_id: str) -> tuple[int, int]:
        """变卖可上架饰品（跳过不可上架的），返回 (金币, 件数)。物品会被清空。"""
        rows = self.store.items_sorted(user_id, limit=200)
        coins = 0
        count = 0
        for row in rows:
            if row["marketable"]:
                price = self._item_price(row["item_name"])
                if price:
                    coins += self._coins(price)
            count += 1
        self.store.clear_user(user_id)
        return coins, count

    async def _sell(self, event, arg: str) -> None:
        user_id = str(event.sender.user_id)
        parts = arg.split()
        if not parts or not parts[0].isdigit():
            await self._send(event, "用法：dota卖 <编号>")
            return
        ordered = self.store.items_sorted(user_id, limit=200)
        index = int(parts[0]) - 1
        row = ordered[index] if 0 <= index < len(ordered) else None
        if row is None:
            await self._send(event, "没有这个序号的物品（序号见 dota饰品）")
            return
        if not row["marketable"]:
            await self._send(event, "这件物品在官方规则下不可交易，只能收藏或赠送")
            return
        price = self._item_price(row["item_name"])
        if not price:
            await self._send(event, "暂无市场报价，无法出售")
            return
        coins = self._coins(price)
        if not self.store.remove_items(user_id, [row["id"]]):
            await self._send(event, "出售失败：物品已被处理")
            return
        landed = self._credit_with_loan(user_id, coins, "dota_sell")
        extra = f"（其中 {coins - landed} 自动还贷）" if coins > landed else ""
        await self._send(
            event,
            f"已出售 {self._cn(row['item_name'])}，共 {coins} 金币{extra}",
        )

    async def _leaderboard(self, event) -> None:
        rows = self.store.leaderboard(5)
        if not rows:
            await self._send(event, "还没有人开过瓶")
            return
        lines = ["Dota 2 开瓶榜（按次数）"]
        for index, row in enumerate(rows, start=1):
            lines.append(f"{index}. {row['user_id']}：{row['opens']} 瓶 / {row['spent']} 币")
        await self._send(event, "\n".join(lines))

    async def _stats(self, event) -> None:
        user_id = str(event.sender.user_id)
        stats = self.store.stats(user_id)
        lines = [
            f"开瓶战绩 · {user_id}",
            f"开瓶次数：{stats['opens']}",
            f"累计花费：{stats['spent']} 金币",
            f"稀有命中：{stats['rare']} 次",
        ]
        best = stats.get("best")
        if best:
            lines.append(
                f"最好掉落：{self._cn(best['item_name'])}（{TIER_LABEL.get(best['tier'], best['tier'])}）· "
                f"{self.treasures[best['treasure']].cn_name if best['treasure'] in self.treasures else best['treasure']}"
            )
        loan_status = self.loan.status(user_id)
        lines.append(f"欠款：{loan_status['debt']} 金币 · 重生：{loan_status['rebirths']} 次")
        lines.append(f"背包：{self.store.count(user_id)} 件")
        await self._send(event, "\n".join(lines))
