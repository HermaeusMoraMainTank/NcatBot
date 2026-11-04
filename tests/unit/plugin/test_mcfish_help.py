"""改版说明分页（P7）的文案约束与命令测试。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "plugins"))

from McFish import mcfish_help as H
from McFish.McFish import McFish

ADVICE_WORDS = ("建议", "你可以", "快去", "记得", "最好", "推荐")


def test_revision_help_has_eight_short_pages():
    assert H.revision_page_count() == 8
    for index in range(1, 9):
        page = H.revision_page(index)
        lines = page.splitlines()
        assert lines[0].startswith(f"钓鱼改版说明 · 第{index}页 / 共8页")
        assert 2 <= len(lines) <= 12, index


def test_revision_help_only_explains_and_never_advises():
    for index in range(1, 9):
        page = H.revision_page(index)
        for word in ADVICE_WORDS:
            assert word not in page, (index, word)


def test_revision_page_clamps_out_of_range_numbers():
    assert H.revision_page(0) == H.revision_page(1)
    assert H.revision_page(99) == H.revision_page(8)
    assert "常驻鱼王" in H.revision_page(4)
    assert "三叉戟" in H.revision_page(7)


def _plugin(state: dict):
    plugin = McFish.__new__(McFish)
    plugin._state = lambda _uid: state
    plugin._save = lambda _uid, _value: None
    plugin._item_image = lambda _name: None
    return plugin


def _event(text: str):
    return SimpleNamespace(
        sender=SimpleNamespace(user_id="100"),
        raw_message=text,
        reply=AsyncMock(),
    )


def test_help_revision_command_returns_pages():
    state = {"pack": {}, "fish_level": 1}
    plugin = _plugin(state)

    first = _event("钓鱼帮助 改版")
    asyncio.run(plugin._handle_message(first))
    assert first.reply.await_args.kwargs["text"].startswith("钓鱼改版说明 · 第1页")

    fourth = _event("钓鱼帮助 改版 4")
    asyncio.run(plugin._handle_message(fourth))
    assert "常驻鱼王" in fourth.reply.await_args.kwargs["text"]

    capped = _event("钓鱼帮助 改版 99")
    asyncio.run(plugin._handle_message(capped))
    assert capped.reply.await_args.kwargs["text"].startswith("钓鱼改版说明 · 第8页")


def test_help_quick_card_lists_every_routed_command():
    """有人反馈新指令没出现在钓鱼说明里：用路由清单反向卡住帮助文案。"""
    import inspect

    from McFish.McFish import (
        MCFISH_COMMAND_HELP_GROUPS,
        MCFISH_COMMAND_ROWS,
        MCFISH_HELP,
    )

    required = (
        "进行n次钓鱼", "钓鱼鱼点", "前往", "钓鱼力", "开箱", "吃海藻", "吃食物",
        "钓鱼卡牌", "钓鱼装备", "装备图鉴", "合成渔具", "装备渔具", "强化渔具", "卸下渔具",
        "钓鱼工坊", "交付", "领取 三叉戟", "领取 神谕卡组",
        "钓鱼天赋", "学习", "重置钓鱼天赋",
        "购买饰品槽位", "升级财富浮标", "修复鱼竿", "附魔诱钓",
        "合成财富浮标", "合成魔法鱼饵", "分解鱼竿", "消除宝藏诅咒",
        "当前装备概率明细", "查看上次钓鱼结果",
        "钓鱼帮助 改版", "钓鱼帮助 鱼点",
        "钓鱼发放 @用户 物品 数量", "钓鱼充值 @用户 物品 数量",
    )
    joined = "\n".join(MCFISH_COMMAND_ROWS)
    for command in required:
        assert command in joined, command
    # 文本版帮助与速查卡共用同一份命令清单，避免两处漂移。
    for row in MCFISH_COMMAND_ROWS:
        assert row in MCFISH_HELP
    # 图片速查卡使用按功能分组的命令表，而不是另抄一份。
    assert "MCFISH_COMMAND_HELP_GROUPS" in inspect.getsource(McFish._render_help_image)
    table_text = "\n".join(
        f"{command} {desc}"
        for _title, _accent, rows in MCFISH_COMMAND_HELP_GROUPS
        for command, desc in rows
    )
    for command in required:
        assert command in table_text, command


def test_help_image_splits_into_compact_pages(tmp_path: Path):
    from PIL import Image as PILImage

    plugin = McFish.__new__(McFish)
    plugin.workspace = tmp_path

    for page in ("", "快速", "鱼点", "装备", "鱼竿", "掉落", "任务", "机制"):
        output = plugin._render_help_image("100", page)
        assert output.exists()
        with PILImage.open(output) as image:
            assert image.size[0] == 1600
            # 分页后单页必须明显小于拆分前的 3400px 整图。
            assert image.size[1] < 3000, page

    assert plugin._render_help_image("100").name == "help_100_quick.png"
    assert plugin._render_help_image("100", "鱼点").name == "help_100_spots.png"
