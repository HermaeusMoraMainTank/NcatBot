"""CS2 开箱 / Dota 2 开瓶 合并插件。

`CaseOpening` 只负责：生命周期、经济与借贷、统一帮助、指令分发；
具体玩法拆在 `cs2.py` 与 `dota.py` 两个模块里，以后加抽卡只需再挂一个模块。
"""

from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path

from common.case_data.loan import MAX_LOAN, LoanStore
from common.case_data.render import render_panel
from common.utils.CommonUtil import CommonUtil
from common.utils.plugin_commands import format_help, is_help_message

from ncatbot.core import registrar
from ncatbot.event.qq import GroupMessageEvent as GroupMessage
from ncatbot.plugin import NcatBotPlugin
from ncatbot.types import Image, MessageArray
from ncatbot.utils import get_log

from .config import CS2_KEYS, DEFAULT_CONFIG, DOTA_KEYS
from .cs2 import Cs2Case
from .dota import DotaCase

_log = get_log()

#: 指令生成的卡片/面板在本地保留多久（秒）；报错排查窗口，超时即清理
CARD_KEEP_SECONDS = 3600

HELP_TITLE = "开箱 / 开瓶 指令"

HELP = format_help(
    "开箱 / 开瓶",
    [
        "CS2 与 Dota 是两套指令，帮助是同一份；借贷两边共用",
        "开箱帮助 / 开瓶帮助 / 箱子帮助 / cs帮助 / dota帮助 都返回这张说明",
    ],
)

#: 统一帮助的分组内容（标题, [(指令, 说明)...]）
HELP_SECTIONS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "CS2 开箱",
        (
            ("cs箱子 [页码]", "箱子列表：编号、中文名、箱价、钥匙价"),
            ("cs详情 <箱子|编号>", "内容分档、概率、钥匙、价格"),
            ("cs价值表 <箱子|编号> [页]", "每件物品的图与回收价区间"),
            ("cs开箱 <箱子|编号> [1-10]", "买入并立刻开箱，返回产物"),
            ("cs背包 [页码]", "物品网格：按价格排序，序号在角标"),
            ("cs物品 <序号>", "外观 / 磨损值 / 回收价"),
            ("cs出售 <序号|all>", "按市价 ×0.85 回收，所得优先还贷"),
            ("cs汰换 <序号×10|1-10|军规>", "10 件同档 → 下一档 1 件"),
            ("cs汰换预览 <同上>", "配方与出货概率，不执行"),
            ("cs查价 <关键词>", "查 Skinport 挂单价"),
            ("cs战绩 / cs榜", "个人统计 / 开箱次数排行"),
        ),
    ),
    (
        "Dota 2 宝瓶",
        (
            ("dota宝瓶 [页码]", "列表：图标、中文名、国服售价"),
            ("dota详情 <名字|编号>", "内容分层、递增概率、累计奖励"),
            ("dota开瓶 <名字|编号> [1-10]", "买入并立刻开瓶，返回产物"),
            ("dota一轮 <名字|编号>", "本轮各档第几次、下次概率、还差几瓶必出"),
            ("dota饰品 [页码]", "饰品网格：按价格排序，序号在角标"),
            ("dota卖 <序号>", "出售可交易物品（7 天锁），所得优先还贷"),
            ("dota战绩 / dota榜", "个人统计 / 开瓶次数排行"),
        ),
    ),
    (
        "借贷（不需要前缀）",
        (
            ("借贷", "可用额度、欠款、历史借还、重生次数"),
            ("借钱 / 借款 <金额>", "单次 ≤ 100 万，不限次数；上一笔没还清不能再借"),
            ("还款 / 还钱 <金额>", "用钱包余额还款"),
            ("重生 [确认]", "变卖背包抵债 + 清零余额 + 清空两边背包，需二次确认"),
        ),
    ),
)

COMMAND_RE = None  # 由 handle_message 内的前缀判断替代


class CaseOpening(NcatBotPlugin):
    name = "CaseOpening"
    version = "1.0.0"
    description = "CS2 开箱 + Dota 2 开瓶 + 共享开箱贷"

    #: 启动时补齐缺失图标；单元测试把它关掉，避免测试打网络
    warm_images_on_load = True

    async def on_load(self) -> None:
        self.init_defaults(DEFAULT_CONFIG)
        self.data_dir = Path(self.workspace) / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.loan = LoanStore(Path(self.workspace).parent / "case_loan" / "loan.db")
        self._rebirth_pending: dict[str, float] = {}
        self._migrate_legacy()
        self.cs2 = Cs2Case(self)
        self.dota = DotaCase(self)
        self._cleanup_cards()
        self._cleanup_images()
        self._warm_task = (
            asyncio.create_task(self._warm_images()) if self.warm_images_on_load else None
        )
        self.add_scheduled_task("refresh_prices", "6h", callback=self.cs2._refresh_prices)
        self.add_scheduled_task("cleanup_cards", "1h", callback=self._cleanup_cards)
        self.add_scheduled_task("cleanup_images", "1d", callback=self._cleanup_images)

    def _migrate_legacy(self) -> None:
        """把旧的两个插件数据目录迁移到 CaseOpening 下（只迁一次）。"""
        base = Path(self.workspace).parent
        pairs = (
            (base / "CsgoCase" / "data", self.data_dir / "cs2"),
            (base / "DotaTreasure" / "data", self.data_dir / "dota"),
        )
        for old, new in pairs:
            if not old.is_dir():
                continue
            new.mkdir(parents=True, exist_ok=True)
            for item in old.iterdir():
                target = new / item.name
                if target.exists():
                    continue
                try:
                    shutil.move(str(item), str(target))
                except OSError:
                    continue

    async def on_close(self) -> None:
        warm = getattr(self, "_warm_task", None)
        if warm is not None and not warm.done():
            warm.cancel()
        self.cs2.close()
        self.dota.close()
        self.loan.close()

    # ------------------------------------------------------------------ 配置 / 经济

    def cfg(self, key: str):
        return self.get_config(key, DEFAULT_CONFIG[key])

    def cs_cfg(self, key: str):
        return self.cfg(CS2_KEYS.get(key, key))

    def dota_cfg(self, key: str):
        return self.cfg(DOTA_KEYS.get(key, key))

    def rmb(self, coins: int) -> float:
        rate = float(self.cfg("coin_per_cny")) or 1.0
        return coins / rate

    def money(self, coins: int) -> str:
        """金币 + 实际人民币，例：5,400 币（¥27.00）。"""
        return f"{int(coins):,} 币（¥{self.rmb(int(coins)):,.2f}）"

    def _economy(self):
        plugin = self.get_plugin("GroupEconomy")
        return getattr(plugin, "store", None) if plugin is not None else None

    def _charge(self, user_id: str, coins: int, reason: str) -> bool:
        if coins <= 0:
            return True
        economy = self._economy()
        if economy is None:
            return False
        return bool(economy.change_balance(user_id, -coins, reason=reason)["ok"])

    def _credit(self, user_id: str, coins: int, reason: str) -> bool:
        if coins <= 0:
            return True
        economy = self._economy()
        if economy is None:
            return False
        return bool(economy.change_balance(user_id, coins, reason=reason)["ok"])

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

    async def _send(self, event, text: str | None = None, image: Path | None = None) -> None:
        if text:
            await event.reply(text=text, at_sender=False)
        if image is not None:
            await self.api.qq.post_group_msg(
                group_id=event.group_id,
                rtf=MessageArray([Image(file=str(image))]),
            )

    # ------------------------------------------------------------------ 帮助

    async def _help_card(self, event) -> None:
        rows: list[dict[str, str]] = []
        for title, items in HELP_SECTIONS:
            rows.append({"section": title})
            for command, desc in items:
                rows.append(
                    {"title": command, "meta": desc, "color": "#d9a441", "compact": True}
                )
        path = render_panel(
            self.cs2.render_dir / f"help_{event.sender.user_id}_{int(time.time())}.png",
            title=HELP_TITLE,
            subtitle="CS2 与 Dota 是两套指令，帮助是同一份；借贷两边共用",
            rows=rows,
            footer="箱/瓶可写编号或名字 · 单次上限 CS2 10 个 / Dota 10 瓶 · 无每日次数限制",
        )
        await self._send(event, None, path)

    # ------------------------------------------------------------------ 借贷 / 重生

    @staticmethod
    def _stop_propagation(event) -> None:
        data = getattr(event, "_data", None)
        if data is None:
            return
        try:
            object.__setattr__(data, "_propagation_stopped", True)
        except Exception:  # noqa: BLE001 - 拿不到就算了，不影响主流程
            return

    async def _loan_card(
        self,
        event,
        *,
        title: str,
        rows: list[dict],
        subtitle: str = "",
        footer: str = "",
    ) -> None:
        path = render_panel(
            self.cs2.render_dir / f"loan_{event.sender.user_id}_{time.time_ns()}.png",
            title=title,
            subtitle=subtitle,
            rows=rows,
            footer=footer,
        )
        await self._send(event, None, path)

    async def _loan_status(self, event) -> None:
        user_id = str(event.sender.user_id)
        status = self.loan.status(user_id)
        economy = self._economy()
        balance = int(economy.account(user_id)["balance"]) if economy else 0
        clean = status["can_borrow"]
        last_rebirth = (
            time.strftime("%Y-%m-%d", time.localtime(status["last_rebirth_at"]))
            if status["rebirths"]
            else "—"
        )
        rows = [
            {"section": "当前"},
            {
                "title": "可用额度",
                "meta": f"¥{self.rmb(status['credit']):,.2f} · 只能用于开箱 / 开瓶",
                "right": f"{status['credit']} 币",
                "color": "#d9a441",
                "compact": True,
            },
            {
                "title": "待还欠款",
                "meta": f"¥{self.rmb(status['debt']):,.2f} · 出售所得优先还贷",
                "right": f"{status['debt']} 币",
                "color": "#eb4b4b" if status["debt"] else "#4b69ff",
                "compact": True,
            },
            {
                "title": "钱包余额",
                "meta": f"¥{self.rmb(balance):,.2f} · 可用「还款 <金额>」扣除",
                "right": f"{balance} 币",
                "color": "#7fb2ff",
                "compact": True,
            },
            {
                "title": "借款状态",
                "meta": "上一笔没还清不能再借；还清或重生后恢复",
                "right": "可借" if clean else "未还清",
                "color": "#9fde4d" if clean else "#fd9e56",
                "compact": True,
            },
            {"section": "历史"},
            {
                "title": "累计欠款",
                "meta": f"最高欠款 {status['peak_debt']} · 借款 {status['loan_count']} 次",
                "right": f"{status['total_debt']} 币",
                "color": "#8fa3bd",
                "compact": True,
            },
            {
                "title": "累计已还",
                "meta": "含出售抵扣与手动还款",
                "right": f"{status['total_repaid']} 币",
                "color": "#8fa3bd",
                "compact": True,
            },
            {"section": "重生"},
            {
                "title": "重生次数",
                "meta": f"上次重生 {last_rebirth}",
                "right": f"{status['rebirths']} 次",
                "color": "#b85af3" if status["rebirths"] else "#8fa3bd",
                "compact": True,
            },
        ]
        await self._loan_card(
            event,
            title="开箱借贷",
            subtitle="CS2 / Dota 共用同一笔额度",
            rows=rows,
            footer="借钱 <金额> · 还款 <金额> · 重生（二次确认）· 单笔上限 100 万",
        )

    async def _borrow(self, event, arg: str) -> None:
        user_id = str(event.sender.user_id)
        if not arg.isdigit():
            await self._send(event, f"用法：借钱（或借款）<金额>，单次最多 {MAX_LOAN}")
            return
        result = self.loan.borrow(user_id, int(arg), MAX_LOAN)
        if not result["ok"]:
            await self._send(event, f"借不到：{result['reason']}")
            return
        await self._loan_card(
            event,
            title="借钱成功",
            subtitle=f"本次借入 {int(arg)} 金币",
            rows=[
                {
                    "title": "当前欠款",
                    "meta": "还清前不能再借",
                    "right": f"{result['debt']} 币",
                    "color": "#eb4b4b",
                    "compact": True,
                },
                {
                    "title": "可用额度",
                    "meta": "只能用于开箱 / 开瓶",
                    "right": f"{result['credit']} 币",
                    "color": "#d9a441",
                    "compact": True,
                },
            ],
            footer="还款 <金额> 或用出售所得抵扣 · 重生可重置",
        )

    async def _repay(self, event, arg: str) -> None:
        user_id = str(event.sender.user_id)
        if not arg.isdigit():
            await self._send(event, "用法：还款（或还钱）<金额>")
            return
        status = self.loan.status(user_id)
        if status["debt"] <= 0:
            await self._send(event, "没有待还的借款")
            return
        economy = self._economy()
        balance = int(economy.account(user_id)["balance"]) if economy else 0
        amount = min(int(arg), status["debt"], balance)
        if amount <= 0:
            await self._send(event, "余额不足，先出售一些物品")
            return
        if not self._charge(user_id, amount, "case_repay"):
            await self._send(event, "余额不足，还款失败")
            return
        result = self.loan.repay(user_id, amount)
        await self._loan_card(
            event,
            title="还款成功",
            subtitle=f"本次还款 {result['paid']} 金币",
            rows=[
                {
                    "title": "剩余欠款",
                    "meta": "清零后即可再借",
                    "right": f"{result['debt']} 币",
                    "color": "#9fde4d" if result["debt"] == 0 else "#eb4b4b",
                    "compact": True,
                },
                {
                    "title": "可用额度",
                    "meta": "只能用于开箱 / 开瓶",
                    "right": f"{result['credit']} 币",
                    "color": "#d9a441",
                    "compact": True,
                },
            ],
            footer="还清即可再借，没有每日次数限制",
        )

    def _pending(self) -> dict[str, float]:
        if not hasattr(self, "_rebirth_pending"):
            self._rebirth_pending = {}
        return self._rebirth_pending

    async def _rebirth(self, event, arg: str) -> None:
        user_id = str(event.sender.user_id)
        pending = self._pending()
        if arg != "确认":
            pending[user_id] = time.time() + 60
            debt_now = self.loan.status(user_id)["debt"]
            await self._loan_card(
                event,
                title="重生确认",
                subtitle="不可撤销，60 秒内发送「重生 确认」才会执行",
                rows=[
                    {
                        "title": "当前欠款",
                        "meta": f"¥{self.rmb(debt_now):,.2f} · 变卖所得优先抵扣",
                        "right": f"{debt_now:,} 币",
                        "color": "#eb4b4b" if debt_now else "#4b69ff",
                        "compact": True,
                    },
                    {
                        "title": "自动变卖",
                        "meta": "CS2 与 Dota 背包物品按当前回收价折算",
                        "right": "抵扣欠款",
                        "color": "#d9a441",
                        "compact": True,
                    },
                    {
                        "title": "清空背包",
                        "meta": "两个背包全部清空，钓鱼等其它插件不受影响",
                        "right": "不可撤销",
                        "color": "#eb4b4b",
                        "compact": True,
                    },
                    {
                        "title": "清零",
                        "meta": "钱包余额与剩余欠款一并归零",
                        "right": "余额 / 欠款",
                        "color": "#7fb2ff",
                        "compact": True,
                    },
                ],
                footer="确认请发送：重生 确认",
            )
            return
        deadline = pending.get(user_id, 0)
        if deadline < time.time():
            pending.pop(user_id, None)
            await self._send(event, "确认已过期，请重新发送 重生")
            return
        pending.pop(user_id, None)

        economy = self._economy()
        balance = int(economy.account(user_id)["balance"]) if economy else 0

        cs_coins, cs_count = self.cs2.liquidate(user_id)
        dota_coins, dota_count = self.dota.liquidate(user_id)
        sale_total = cs_coins + dota_coins
        debt_before = self.loan.status(user_id)["debt"]
        if sale_total and debt_before:
            self.loan.repay(user_id, min(sale_total, debt_before))
        if balance > 0:
            self._charge(user_id, balance, "case_rebirth")
        rebirth = self.loan.record_rebirth(user_id)
        total_debt = self.loan.status(user_id)["total_debt"]
        rows = [
            {
                "title": "本次欠款",
                "meta": f"¥{self.rmb(debt_before):,.2f} · 已清零",
                "right": f"{debt_before:,} 币",
                "color": "#eb4b4b",
                "compact": True,
            },
            {
                "title": "累计欠款",
                "meta": f"¥{self.rmb(total_debt):,.2f} · 历史累计",
                "right": f"{total_debt:,} 币",
                "color": "#8fa3bd",
                "compact": True,
            },
            {
                "title": f"变卖 CS2 {cs_count} 件",
                "meta": f"¥{self.rmb(cs_coins):,.2f} · 按当前回收价折算",
                "right": f"{cs_coins} 币",
                "color": "#d9a441",
                "compact": True,
            },
            {
                "title": f"变卖 Dota {dota_count} 件",
                "meta": f"¥{self.rmb(dota_coins):,.2f} · 不可上架的不计价但照样清空",
                "right": f"{dota_coins} 币",
                "color": "#7fb2ff",
                "compact": True,
            },
            {
                "title": "变卖所得抵扣欠款",
                "meta": f"¥{self.rmb(sale_total):,.2f} · 原欠款 {debt_before:,} 币",
                "right": f"{sale_total} 币",
                "color": "#9fde4d",
                "compact": True,
            },
            {
                "title": "钱包清零",
                "meta": f"¥{self.rmb(balance):,.2f} · 钓鱼等其它插件物品未动",
                "right": f"{balance} 币",
                "color": "#8fa3bd",
                "compact": True,
            },
        ]
        await self._loan_card(
            event,
            title=f"重生完成（第 {rebirth['rebirths']} 次）",
            subtitle="背包已清空，欠款已归零",
            rows=rows,
            footer="现在可以重新借钱，单笔上限 100 万",
        )

    # ------------------------------------------------------------------ 分发

    @registrar.qq.on_group_message(priority=32)
    async def handle_message(self, event: GroupMessage) -> None:
        text = (event.raw_message or "").strip()
        if not text:
            return
        help_request = is_help_message(
            text,
            command_names=(
                "cs", "dota", "开箱", "开瓶", "箱子", "宝瓶",
                "借贷", "借钱", "借款", "还款", "还钱", "重生",
            ),
        ) or text in ("cs", "dota", "开箱", "开瓶", "箱子", "宝瓶")
        if help_request:
            # McFish 的钓鱼「开箱 …」也以“开箱”开头；本插件优先级更高，
            # 处理完帮助后主动终止事件传播，避免再被钓鱼插件回一句。
            self._stop_propagation(event)
            await self._help_card(event)
            return

        if text.startswith(("借贷", "借钱", "借款", "还款", "还钱", "重生")):
            await self._loan_dispatch(event, text)
        elif text.startswith("cs"):
            if text.startswith(("cs借贷", "cs借钱", "cs借款", "cs还款", "cs还钱", "cs重生")):
                await self._loan_dispatch(event, text[2:].strip())
            else:
                await self.cs2.dispatch(event, text)
        elif text.startswith("dota"):
            if text.startswith(("dota借贷", "dota借钱", "dota借款", "dota还款", "dota还钱", "dota重生")):
                await self._loan_dispatch(event, text[4:].strip())
            else:
                await self.dota.dispatch(event, text)

    async def _loan_dispatch(self, event, text: str) -> None:
        """借贷命令不需要前缀；cs/dota 前缀仍兼容。"""
        if text.startswith("借贷"):
            await self._loan_status(event)
        elif text.startswith(("借钱", "借款")):
            keyword = "借钱" if text.startswith("借钱") else "借款"
            await self._borrow(event, text[len(keyword):].strip())
        elif text.startswith(("还款", "还钱")):
            keyword = "还款" if text.startswith("还款") else "还钱"
            await self._repay(event, text[len(keyword):].strip())
        elif text.startswith("重生"):
            await self._rebirth(event, text[len("重生"):].strip())

    # ------------------------------------------------------------------ 后台任务

    def _cleanup_cards(self) -> int:
        """清理过期的卡片/面板（cs2 目录同时承载帮助与借贷卡片）。"""
        removed = 0
        for module in (self.cs2, self.dota):
            removed += CommonUtil.cleanup_old_files(
                module.render_dir, max_age_seconds=CARD_KEEP_SECONDS
            )
        return removed

    def _cleanup_images(self) -> int:
        """清理过期/超量的物品图缓存；缺失的图会在下次渲染或启动时重新下载。"""
        return self.cs2.images.cleanup() + self.dota.images.cleanup()

    async def _warm_images(self) -> None:
        """启动时补齐缺失的箱子/宝瓶图标，避免首屏列表等图等到超时。"""
        try:
            cs_ok, cs_fail = await self.cs2.images.prefetch(
                [crate.image for crate in self.cs2.cases.values()]
            )
            dota_ok, dota_fail = await self.dota.images.prefetch(
                [treasure.icon for treasure in self.dota.treasures.values()]
            )
        except Exception as exc:  # noqa: BLE001 - 预热失败不影响开箱
            _log.warning("开箱图片预热失败：%s", exc)
            return
        if cs_ok or dota_ok or cs_fail or dota_fail:
            _log.info(
                "开箱图片预热：CS2 补下 %s 张（失败 %s）/ Dota 补下 %s 张（失败 %s）",
                cs_ok,
                cs_fail,
                dota_ok,
                dota_fail,
            )
