"""蓝晴「像不像人」的离线体检：直接读 FakeAi 留档，算一组可比较的指标。

用法：
    python scripts/eval_fakeai_persona.py
    python scripts/eval_fakeai_persona.py --since 2026-11-08 --json data/FakeAi/eval/after.json
    python scripts/eval_fakeai_persona.py --compare data/FakeAi/eval/baseline.json
    python scripts/eval_fakeai_persona.py --export-csv data/FakeAi/eval/ratings.csv --sample 60

指标只描述"形态"，判断"像不像"仍要人工盲评；CSV 就是给盲评用的。
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRACE_DIR = ROOT / "data" / "FakeAi" / "traces"
DEFAULT_JSON = ROOT / "data" / "FakeAi" / "eval" / "latest.json"

SHORT_QUESTIONS = {"？", "?", "??", "???", "？?", "?？", "啊？"}


def load_turns(since: str | None = None) -> list[dict]:
    turns = []
    for path in sorted(TRACE_DIR.glob("*.jsonl")):
        if since and path.stem < since.replace("-", ""):
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            turns.append(data)
    return turns


def texts_of(turn: dict) -> list[str]:
    out = []
    for action in turn.get("actions") or []:
        if action.get("kind") != "text":
            continue
        out.extend(action.get("messages") or ([action["text"]] if action.get("text") else []))
    return [text for text in out if text]


def metrics(turns: list[dict]) -> dict:
    with_text = [turn for turn in turns if texts_of(turn)]
    messages = [text for turn in with_text for text in texts_of(turn)]
    lengths = [len(text) for text in messages]
    short_q = sum(1 for text in messages if text.strip() in SHORT_QUESTIONS)
    multi = sum(1 for turn in with_text if len(texts_of(turn)) > 1)
    tokens = [int((turn.get("usage") or {}).get("total_tokens") or 0) for turn in with_text]
    rounds = [int(turn.get("rounds") or 0) for turn in with_text]
    top = Counter(text.strip() for text in messages).most_common(8)
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "turns": len(turns),
        "turns_with_text": len(with_text),
        "messages": len(messages),
        "avg_chars": round(statistics.mean(lengths), 2) if lengths else 0,
        "median_chars": statistics.median(lengths) if lengths else 0,
        "short_question_rate": round(short_q / len(messages), 4) if messages else 0,
        "multi_bubble_rate": round(multi / len(with_text), 4) if with_text else 0,
        "le2_rate": round(sum(1 for n in lengths if n <= 2) / len(messages), 4)
        if messages
        else 0,
        "gt8_rate": round(sum(1 for n in lengths if n > 8) / len(messages), 4)
        if messages
        else 0,
        "unique_rate": round(len({text.strip() for text in messages}) / len(messages), 4)
        if messages
        else 0,
        "avg_total_tokens": round(statistics.mean(tokens), 1) if tokens else 0,
        "avg_rounds": round(statistics.mean(rounds), 2) if rounds else 0,
        "top_texts": top,
        "triggers": dict(Counter(turn.get("trigger") or "?" for turn in turns)),
    }


def print_report(data: dict, baseline: dict | None = None) -> None:
    keys = (
        "turns_with_text",
        "messages",
        "avg_chars",
        "short_question_rate",
        "multi_bubble_rate",
        "le2_rate",
        "gt8_rate",
        "unique_rate",
        "avg_total_tokens",
        "avg_rounds",
    )
    print(f"# 蓝晴人设体检 · {data['generated_at']}\n")
    header = "| 指标 | 数值 |" + (" 基线 | 变化 |" if baseline else " |")
    print(header)
    print("|---|---:|" + ("---:|---:|" if baseline else ""))
    for key in keys:
        value = data.get(key)
        if baseline:
            old = baseline.get(key)
            change = ""
            if isinstance(value, (int, float)) and isinstance(old, (int, float)) and old:
                change = f"{(value - old) / old:+.1%}"
            print(f"| {key} | {value} | {old} | {change} |")
        else:
            print(f"| {key} | {value} |")
    print("\n高频回复：")
    for text, count in data.get("top_texts") or []:
        print(f"- {count:3d}  {text}")
    print("\n触发分布：", data.get("triggers"))


def export_csv(turns: list[dict], path: Path, sample: int) -> None:
    rows = [turn for turn in turns if texts_of(turn)]
    if sample and len(rows) > sample:
        step = len(rows) / sample
        rows = [rows[int(i * step)] for i in range(sample)]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["日期", "群号", "唤醒", "回复", "像人分(1-5)", "备注"])
        for turn in rows:
            writer.writerow(
                [
                    datetime.fromtimestamp(turn.get("ts") or 0).strftime("%Y-%m-%d %H:%M"),
                    turn.get("group_id"),
                    turn.get("wake") or turn.get("trigger"),
                    " / ".join(texts_of(turn)),
                    "",
                    "",
                ]
            )
    print(f"已导出 {len(rows)} 条到 {path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default="")
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--export-csv", type=Path)
    parser.add_argument("--sample", type=int, default=60)
    args = parser.parse_args()

    turns = load_turns(args.since or None)
    if not turns:
        raise SystemExit("没有可用的留档")
    data = metrics(turns)

    if args.export_csv:
        export_csv(turns, args.export_csv, args.sample)
        return

    baseline = None
    if args.compare and args.compare.is_file():
        baseline = json.loads(args.compare.read_text(encoding="utf-8"))
    print_report(data, baseline)

    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n已写入 {args.json}")


if __name__ == "__main__":
    main()
