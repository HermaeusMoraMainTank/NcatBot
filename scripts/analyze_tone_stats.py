"""用本群聊天记录当语料，量一量「人味」的微文本指标。

对比两组：
- 群友：data/db/memory.db 里真实群消息；
- 蓝晴：data/FakeAi/traces 里实际发出去的文字。

输出标点、句长、语气词、重复字、句式扎堆等指标，用来校准 tone.py / _shape_reply，
而不是把语料原样塞回 prompt。

用法：
    python scripts/analyze_tone_stats.py
    python scripts/analyze_tone_stats.py --days 30 --json data/FakeAi/eval/tone_stats.json
    python scripts/analyze_tone_stats.py --group 853963912
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import statistics
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "db" / "memory.db"
DEFAULT_TRACES = ROOT / "data" / "FakeAi" / "traces"
DEFAULT_JSON = ROOT / "data" / "FakeAi" / "eval" / "tone_stats.json"

END_PUNCT = "。！？!?…~～"
PARTICLES = "吧啊呢哦嘛啦呀呗"
MEDIA_ANCHOR = re.compile(r"\[(图片|视频|表情|合并转发|回复|文件)(#\d+)?\]")
LINK_RE = re.compile(r"https?://|www\.|\[CQ:", re.I)
PURE_SYMBOL = re.compile(r"^[\W_]+$")
WS = re.compile(r"\s+")

MICRO_MARKERS = {
    "也": "也",
    "还": "还",
    "应该": "应该",
    "感觉": "感觉",
    "可能": "可能",
    "好像": "好像",
    "根本": "根本",
    "特别": "特别",
    "简直": "简直",
    "贼": "贼",
    "巨": "巨",
}

STRUCTURES = {
    "不是X而是Y": re.compile(r"不是[^，。！？]{0,10}而是"),
    "最…的是": re.compile(r"最[^，。！？]{1,10}的是"),
    "先…再…": re.compile(r"先[^，。！？]{1,10}再"),
    "破折号": re.compile(r"——"),
}

HOMOPHONE_TYPOS = ("做为", "因该", "即然", "必竟")
REPEAT_CHAR = re.compile(r"([\u4e00-\u9fff])\1")
REPEAT_PUNCT = re.compile(r"([，。！？!?])\1+")
MIXED_PUNCT = re.compile(r"[\u4e00-\u9fff][,.;:][\u4e00-\u9fff]")

COMMAND_PREFIXES = (
    "钓鱼", "签到", "我的钱包", "查看钱包", "打劫", "购买", "出售", "装备", "卸下",
    "升级", "合成", "分解", "附魔", "前往", "开箱", "吃海藻", "吃食物", "交付",
    "领取", "学习", "重置", "占卜", "塔罗", "点歌", "搜视频", "查询视频", "解析",
    "帮助", "重启", "更新", "权限", "禁言", "撤回", "抽卡", "来张", "发图",
    "图库", "算数", "计算", "猜卡", "抽签", "人品", "今日", "早报",
    "送礼物给", "llm", "jrrp", "blb",
)
PLUGIN_OUTPUT = re.compile(
    r"^(🎨|⏳|在画了|请稍等|已发送|已生成|正在生成|正在使用|\[引用消息\]|\[sysmsg\]|目录:|成功:|标签:)"
)


def clean(text: str) -> str:
    text = MEDIA_ANCHOR.sub("", str(text or ""))
    return WS.sub(" ", text).strip()


def usable(text: str) -> bool:
    if not text or text in {"[图片]", "[视频]", "[表情]"}:
        return False
    if LINK_RE.search(text) or PURE_SYMBOL.match(text) or PLUGIN_OUTPUT.match(text):
        return False
    if text.startswith(COMMAND_PREFIXES) or text.startswith(("/", "#", "！", "!")):
        return False
    return 2 <= len(text) <= 120


def group_texts(db: Path, days: int, group: str) -> list[str]:
    if not db.is_file():
        raise SystemExit(f"找不到数据库：{db}")
    since = int(time.time()) - days * 86400
    sql = "SELECT group_id, name, user_id, content FROM group_messages WHERE ts >= ?"
    params: list = [since]
    if group:
        sql += " AND group_id = ?"
        params.append(str(group))
    conn = sqlite3.connect(db)
    try:
        rows = list(conn.execute(sql, params))
    finally:
        conn.close()
    out = []
    for _gid, name, user_id, content in rows:
        if str(name or "") in {"", "[null]", "蓝晴", "系统"} or str(user_id or "") == "0":
            continue
        text = clean(content)
        if usable(text):
            out.append(text)
    return out


def bot_texts(traces: Path, days: int, group: str) -> list[str]:
    since = time.time() - days * 86400
    out = []
    for path in sorted(traces.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if float(data.get("ts") or 0) < since:
                continue
            if group and str(data.get("group_id")) != str(group):
                continue
            for action in data.get("actions") or []:
                if action.get("kind") != "text":
                    continue
                for text in action.get("messages") or (
                    [action["text"]] if action.get("text") else []
                ):
                    text = clean(text)
                    if text:
                        out.append(text)
    return out


def per_k(count: int, chars: int) -> float:
    return round(count * 1000 / chars, 3) if chars else 0.0


def metrics(texts: list[str]) -> dict:
    chars = sum(len(text) for text in texts)
    lengths = [len(text) for text in texts]
    no_punct = sum(1 for text in texts if text[-1] not in END_PUNCT)
    questions = sum(1 for text in texts if text.rstrip()[-1:] in "？?")
    particle_end = sum(1 for text in texts if text[-1] in PARTICLES)
    return {
        "messages": len(texts),
        "chars": chars,
        "avg_chars": round(statistics.mean(lengths), 2) if lengths else 0,
        "median_chars": statistics.median(lengths) if lengths else 0,
        "le6_rate": round(sum(1 for n in lengths if n <= 6) / len(texts), 4)
        if texts
        else 0,
        "gt15_rate": round(sum(1 for n in lengths if n > 15) / len(texts), 4)
        if texts
        else 0,
        "no_end_punct_rate": round(no_punct / len(texts), 4) if texts else 0,
        "question_rate": round(questions / len(texts), 4) if texts else 0,
        "particle_end_rate": round(particle_end / len(texts), 4) if texts else 0,
        "repeat_char_per_k": per_k(sum(len(REPEAT_CHAR.findall(t)) for t in texts), chars),
        "repeat_punct_per_k": per_k(sum(len(REPEAT_PUNCT.findall(t)) for t in texts), chars),
        "mixed_punct_per_k": per_k(sum(len(MIXED_PUNCT.findall(t)) for t in texts), chars),
        "homophone_typo_per_k": per_k(
            sum(sum(t.count(w) for w in HOMOPHONE_TYPOS) for t in texts), chars
        ),
        "markers": {
            name: per_k(sum(t.count(word) for t in texts), chars)
            for name, word in MICRO_MARKERS.items()
        },
        "structures": {
            name: per_k(len(pattern.findall("".join(texts))), chars)
            for name, pattern in STRUCTURES.items()
        },
        "top_texts": Counter(texts).most_common(10),
    }


def print_report(human: dict, bot: dict) -> None:
    rows = (
        ("消息数", "messages"),
        ("总字数", "chars"),
        ("平均字数", "avg_chars"),
        ("中位字数", "median_chars"),
        ("≤6 字占比", "le6_rate"),
        (">15 字占比", "gt15_rate"),
        ("句末无标点率", "no_end_punct_rate"),
        ("问号结尾率", "question_rate"),
        ("语气词结尾率", "particle_end_rate"),
        ("重复字/千字", "repeat_char_per_k"),
        ("重复标点/千字", "repeat_punct_per_k"),
        ("中英标点混用/千字", "mixed_punct_per_k"),
        ("同音错字/千字", "homophone_typo_per_k"),
    )
    print("# 本群语料体检\n")
    print("| 指标 | 群友 | 蓝晴 |")
    print("|---|---:|---:|")
    for label, key in rows:
        print(f"| {label} | {human.get(key)} | {bot.get(key)} |")
    print("\n## 微标记（每千字）\n")
    print("| 词 | 群友 | 蓝晴 |")
    print("|---|---:|---:|")
    for name in MICRO_MARKERS:
        print(
            f"| {name} | {human['markers'].get(name)} | {bot['markers'].get(name)} |"
        )
    print("\n## 句式（每千字）\n")
    print("| 句式 | 群友 | 蓝晴 |")
    print("|---|---:|---:|")
    for name in STRUCTURES:
        print(
            f"| {name} | {human['structures'].get(name)} | {bot['structures'].get(name)} |"
        )
    print("\n## 群友高频短句\n")
    for text, count in human.get("top_texts") or []:
        print(f"- {count:3d}  {text}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--traces", type=Path, default=DEFAULT_TRACES)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--group", default="")
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    args = parser.parse_args()

    human = metrics(group_texts(args.db, args.days, args.group))
    bot = metrics(bot_texts(args.traces, args.days, args.group))
    print_report(human, bot)

    payload = {"days": args.days, "group": args.group, "human": human, "bot": bot}
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n已写入 {args.json}")


if __name__ == "__main__":
    main()
