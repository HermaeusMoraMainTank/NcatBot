"""为废墟图书馆卡池补中文名（不重拉立绘）。

指定司书规则：
- 仅 Angela/Roland 用正式中文主名
- Malkuth/Gebura/Binah 主名保持英文，中文外号只在 nicknames.json
- Yesod/Hod/Netzach/Tiphereth/Chesed/Hokma 禁止中文作答（主名与别名均去中文）
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_extra_pools import _lor_load_cn_map, _lor_resolve_cn, _lor_has_cjk  # noqa: E402

CJK = re.compile(r"[\u4e00-\u9fff]")

# 正式中文主名
CN_PRIMARY = {"Angela": "安吉拉", "Roland": "罗兰"}
# 仅外号（主名保持英文；外号由 nicknames.json 提供）
CN_NICK_ONLY = {"Malkuth", "Gebura", "Binah"}
# 禁止任何中文答案键
EN_ONLY = {"Yesod", "Hod", "Netzach", "Tiphereth", "Chesed", "Hokma"}

chars_path = ROOT / "resources" / "lor" / "characters.json"
chars = json.loads(chars_path.read_text(encoding="utf-8"))
print("load cn map...", flush=True)
cn_map = _lor_load_cn_map()
updated = 0
for c in chars:
    en = (c.get("fullName") or c.get("name") or "").strip()
    if not en:
        continue

    if en in EN_ONLY or en in CN_NICK_ONLY:
        c["name"] = en
        c["fullNameChinese"] = en
        aliases = [en]
        for a in c.get("aliases") or []:
            a = str(a).strip()
            if a and a not in aliases and not CJK.search(a):
                aliases.append(a)
        c["aliases"] = aliases
        updated += 1
        continue

    if en in CN_PRIMARY:
        new = CN_PRIMARY[en]
        c["name"] = new
        c["fullNameChinese"] = new
        aliases = [new, en]
        for a in c.get("aliases") or []:
            a = str(a).strip()
            if a and a not in aliases and not (CJK.search(a) and a != new):
                # 只保留指定中文 + 非中文别名
                if CJK.search(a) and a != new:
                    continue
                aliases.append(a)
        c["aliases"] = list(dict.fromkeys(aliases))
        updated += 1
        continue

    old = (c.get("fullNameChinese") or "").strip()
    new = _lor_resolve_cn(en, cn_map)
    if not _lor_has_cjk(new):
        continue
    if new != old or not _lor_has_cjk(old):
        updated += 1
    c["name"] = new
    c["fullNameChinese"] = new
    aliases = list(c.get("aliases") or [])
    for a in (new, en):
        if a and a not in aliases:
            aliases.insert(0, a)
    c["aliases"] = list(dict.fromkeys(a for a in aliases if a))

chars_path.write_text(
    json.dumps(chars, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
print(f"updated_cn_fields={updated} total={len(chars)}", flush=True)
for en in ("Tiphereth", "Gebura", "Binah", "Malkuth", "Roland", "Angela", "Yesod"):
    hits = [c for c in chars if c.get("fullName") == en]
    if hits:
        print(
            en,
            "->",
            hits[0]["fullNameChinese"],
            "aliases=",
            hits[0]["aliases"],
            flush=True,
        )
