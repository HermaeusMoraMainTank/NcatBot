"""把 Tarot 插件包装成确定性 skill，避免依赖 5% 随机触发。"""

from __future__ import annotations

import random
import shutil
import time
from pathlib import Path

from ncatbot.skill import SkillContext, SkillResult, skill


class TarotSkills:
    @skill(
        "tarot.divine",
        summary="塔罗占卜：抽一张牌，返回牌名、正/逆位和牌面图片",
        description=(
            "直接调用 Tarot 插件抽牌；不受原插件 5% 随机触发限制。"
            "牌面图片由发送层自动发出，不要重复调用 message.send_image。"
        ),
        params=[
            {"name": "question", "type": "string", "description": "想问的问题，可空"},
        ],
        tags=["tarot", "media", "read"],
        side_effects="read",
        requires=("fs.read",),
        timeout=30,
    )
    async def skill_tarot_divine(
        self, ctx: SkillContext, question: str = ""
    ) -> SkillResult:
        plugin = self.get_plugin("Tarot") if hasattr(self, "get_plugin") else None
        if plugin is None:
            return SkillResult.fail("Tarot 插件未加载")
        try:
            cards = plugin.get_random_tarots(1)
            card = next(iter(cards), None)
            if card is None:
                return SkillResult.fail("塔罗牌数据为空：请检查 data/yml/tarot.yml")
            orientation = random.randint(0, 1)
            text = plugin.get_tarot_message(card, orientation)
            image = plugin.get_tarot_image(card.image_name, orientation)
        except Exception as exc:
            return SkillResult.fail(f"抽牌失败：{type(exc).__name__}: {exc}")

        artifacts: list[dict] = []
        source = Path(image) if image else None
        if source is not None and source.is_file() and ctx.workspace is not None:
            suffix = source.suffix or ".jpg"
            name = f"tarot-{int(time.time())}{suffix}"
            try:
                target = ctx.workspace.ensure_parent(name)
                shutil.copy2(source, target)
                artifacts.append(
                    {
                        "kind": "image",
                        "name": name,
                        "path": name,
                        "abs_path": str(target),
                    }
                )
            except Exception:
                pass

        content = text
        if question:
            content = f"问题：{question}\n{content}"
        if artifacts:
            content += f"\n（牌面图片：{artifacts[0]['path']}）"
        return SkillResult(
            ok=True,
            content=content,
            data={
                "card": card.name,
                "orientation": "正位" if orientation == 0 else "逆位",
                "question": question,
            },
            artifacts=artifacts,
        )
