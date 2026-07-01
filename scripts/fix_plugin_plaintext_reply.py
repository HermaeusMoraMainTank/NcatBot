"""One-off: fix PlainText / Reply for Pydantic v2 message segments in plugins."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "plugins"


def fix_text(s: str) -> str:
    t = s.replace('PlainText(f"', 'PlainText(text=f"')
    t = re.sub(r'PlainText\((?!text=)"', 'PlainText(text="', t)
    t = re.sub(r'PlainText\(\s*\n(\s*)"', r'PlainText(\n\1text="', t)
    t = re.sub(r'PlainText\(\s*\n(\s*)f"', r'PlainText(\n\1text=f"', t)
    t = re.sub(r"Reply\(\s*input\.message_id\s*\)", "Reply(id=input.message_id)", t)
    t = re.sub(r"Reply\(\s*event\.message_id\s*\)", "Reply(id=event.message_id)", t)
    for a, b in (
        ("PlainText(str(days))", "PlainText(text=str(days))"),
        ("PlainText(start_msg)", "PlainText(text=start_msg)"),
        ("PlainText(txt_msg)", "PlainText(text=txt_msg)"),
        ("PlainText(moegirl_link)", "PlainText(text=moegirl_link)"),
        ("PlainText(wiki_link)", "PlainText(text=wiki_link)"),
        ('PlainText(part["data"])', 'PlainText(text=part["data"])'),
        ("PlainText(basic_info)", "PlainText(text=basic_info)"),
        ("PlainText(simple_msg)", "PlainText(text=simple_msg)"),
        ("PlainText(response)", "PlainText(text=response)"),
        ("PlainText(msg)", "PlainText(text=msg)"),
        ("PlainText(error_msg)", "PlainText(text=error_msg)"),
        ("PlainText(full_message)", "PlainText(text=full_message)"),
        ('PlainText("\\n".join(text_parts))', 'PlainText(text="\\n".join(text_parts))'),
    ):
        t = t.replace(a, b)
    return t


def main() -> int:
    n = 0
    for p in ROOT.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        old = p.read_text(encoding="utf-8")
        new = fix_text(old)
        if new != old:
            p.write_text(new, encoding="utf-8")
            print(p.relative_to(ROOT))
            n += 1
    print(f"updated {n} files", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
