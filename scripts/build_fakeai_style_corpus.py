"""从群消息存档里抽一批「群友平时怎么说话」的样本。

用法：
    python scripts/build_fakeai_style_corpus.py --days 30 --limit 500
    python scripts/build_fakeai_style_corpus.py --db data/db/memory.db

只保留短句、去掉命令/链接/图片锚点/机器人自己的话，按用户和日期做分散采样。
输出 data/FakeAi/style_corpus.json，供 FakeAi 每轮检索 3~5 条注入。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import random
import re
import sqlite3
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_tags_for():
    """独立加载 style_corpus.py，避免触发 FakeAi 包的循环导入。"""
    path = ROOT / "plugins" / "FakeAi" / "style_corpus.py"
    spec = importlib.util.spec_from_file_location("_fakeai_style_corpus", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.tags_for


tags_for = _load_tags_for()

DEFAULT_DB = ROOT / "data" / "db" / "memory.db"
DEFAULT_OUT = ROOT / "data" / "FakeAi" / "style_corpus.json"

COMMAND_PREFIXES = (
    "钓鱼", "签到", "我的钱包", "查看钱包", "打劫", "购买", "出售", "装备", "卸下",
    "升级", "合成", "分解", "附魔", "前往", "开箱", "吃海藻", "吃食物", "交付",
    "领取", "学习", "重置", "占卜", "塔罗", "点歌", "搜视频", "查询视频", "解析",
    "帮助", "重启", "更新", "权限", "禁言", "撤回", "戳", "拍", "抽卡", "来张",
    "发图", "图库", "算数", "计算", "猜卡", "抽签", "人品", "今日", "早报",
    "送礼物给", "llm", "jrrp", "blb",
)
PLACEHOLDERS = {"[图片]", "[视频]", "[表情]", "[合并转发]", "[回复]", "[文件]"}
BAD_NAME = {"", "[null]", "None", "null", "蓝晴", "系统"}
LINK_RE = re.compile(r"https?://|www\.|\[CQ:|\[图片#|\[视频#|\[合并转发#|\[文件", re.I)
PURE_SYMBOL = re.compile(r"^[\W_]+$")
MEDIA_ANCHOR = re.compile(r"\[(图片|视频|表情|合并转发|回复|文件)(#\d+)?\]")
WS = re.compile(r"\s+")


def clean(text: str) -> str:
    text = MEDIA_ANCHOR.sub("", str(text or ""))
    text = WS.sub(" ", text).strip()
    return text


def should_skip(content: str, name: str, user_id: str, bot_qq: str) -> bool:
    if not content or content in PLACEHOLDERS:
        return True
    if name in BAD_NAME or str(user_id) == str(bot_qq):
        return True
    if LINK_RE.search(content):
        return True
    if content.startswith(
        (
            "🎨", "⏳", "在画了", "请稍等", "已发送", "正在生成", "正在使用",
            "[引用消息]", "[sysmsg]", "目录:", "成功:", "标签:",
        )
    ):
        return True
    if content.startswith(COMMAND_PREFIXES):
        return True
    if content.startswith(("蓝晴", "滥情", "/", "#", "！", "!")):
        return True
    if PURE_SYMBOL.match(content):
        return True
    if len(content) < 2 or len(content) > 26:
        return True
    # 复读/刷屏：同一字符超过 8 次
    if any(count >= 8 for count in Counter(content).values()):
        return True
    # 纯「哈哈哈」「草草草」这类，当样本没有信息量
    if len(set(content)) <= 2 and len(content) >= 3:
        return True
    return False


def load_rows(db: Path, days: int) -> list[tuple]:
    if not db.is_file():
        raise SystemExit(f"找不到数据库：{db}")
    since = time.time() - days * 86400
    conn = sqlite3.connect(db)
    try:
        return list(
            conn.execute(
                """SELECT group_id, user_id, name, content, ts
                   FROM group_messages
                   WHERE ts >= ? AND content IS NOT NULL AND content != ''
                   ORDER BY ts ASC""",
                (since,),
            )
        )
    finally:
        conn.close()


def pick(rows: list[tuple], limit: int, bot_qq: str) -> list[dict]:
    by_user: dict[str, list] = defaultdict(list)
    seen: set[str] = set()
    for group_id, user_id, name, content, ts in rows:
        text = clean(content)
        if should_skip(text, str(name or ""), str(user_id or ""), bot_qq):
            continue
        key = re.sub(r"[\W_]+", "", text).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        by_user[str(user_id)].append(
            {"text": text, "group_id": str(group_id), "user": str(name or ""), "ts": float(ts)}
        )

    # 每人最多 6 条，避免一个话痨占满样本
    pool: list[dict] = []
    for items in by_user.values():
        items.sort(key=lambda item: item["ts"])
        if len(items) <= 6:
            pool.extend(items)
        else:
            step = len(items) / 6
            pool.extend(items[int(i * step)] for i in range(6))

    if len(pool) > limit:
        # 按时间分层抽，保证覆盖不同日子
        pool.sort(key=lambda item: item["ts"])
        step = len(pool) / limit
        pool = [pool[int(i * step)] for i in range(limit)]

    for item in pool:
        item["tags"] = tags_for(item["text"])
        item["len"] = len(item["text"])
        item.pop("ts", None)
    return pool


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--bot-qq", default="1512130053")
    parser.add_argument("--seed", type=int, default=20261108)
    args = parser.parse_args()

    random.seed(args.seed)
    rows = load_rows(args.db, args.days)
    items = pick(rows, max(50, args.limit), args.bot_qq)
    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": str(args.db),
        "days": args.days,
        "count": len(items),
        "items": items,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tag_counts = Counter(tag for item in items for tag in item["tags"])
    print(f"写入 {args.out}：{len(items)} 条")
    print("标签分布：", dict(tag_counts))
    for sample in items[:10]:
        print(" -", sample["text"])


if __name__ == "__main__":
    main()
