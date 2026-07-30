"""清洗 VT nicknames：去掉 by/... 杂质，补常见假名/简体别名。"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "resources" / "vt"

# 常见「读音/简体」补丁（wiki 常只有汉字 or 罗马音）
EXTRA_BY_NAME: dict[str, list[str]] = {
    "Kuzuha": ["くずは", "クズハ", "葛叶", "葛葉", "久坐叶"],
    "Ibrahim": ["イブラヒム", "あいぶらむ", "アイブラ", "Ibra", "爱卜羊", "爱卜"],
    "Kanae": ["叶", "カナエ", "かなえ"],
    "叶": ["Kanae", "カナエ", "かなえ"],
    "Kagami Hayato": ["加賀美ハヤト", "かがみはやと", "ハヤト"],
    "Kenmochi Toya": ["剣持刀也", "けんもちとうや", "刀也", "剑持刀也"],
    "Ex Albio": ["えくすあるびお", "エクス・アルビオ", "Ex", "阿尔比奥"],
    "Lauren Iroas": ["ローレン・イロアス", "ローレン", "劳伦"],
    "Furen E Lustario": ["フレン・E・ルスタリオ", "フレン", "芙莲"],
    "Lize Helesta": ["リゼ・ヘルエスタ", "リゼ", "莉莉丝", "丽兹"],
    "Ange Katrina": ["アンジュ・カトリーナ", "アンジュ", "安洁"],
    "Lulu Bel": ["鈴原るる", "るる", "露露"],
    "Mysta Rias": ["ミスタ・リアス", "ミスタ", "Mysta"],
    "Shu Yamino": ["闇ノシュウ", "シュウ", "Shu"],
    "Luxiem": [],
    "Vox Akuma": ["ヴォックス・アクマ", "ヴォックス", "Vox"],
    "Ike Eveland": ["アイク・イーヴランド", "アイク", "Ike"],
    "Luca Kaneshiro": ["ルカ・カネシロ", "ルカ", "Luca"],
    "Sonny Brisko": ["サニー・ブリスコー", "サニー", "Sonny"],
    "Uki Violeta": ["ウキ・ヴィオレタ", "ウキ", "Uki"],
    "Alban Knox": ["アルバーン・ノックス", "アルバーン", "Alban"],
    "Fulgur Ovid": ["ファルガー・オーヴィド", "ファルガー", "Fulgur"],
    "Wilson": [],
    "Elira Pendora": ["エリーラ・ペンドラ", "エリーラ", "Elira"],
    "Pomu Rainpuff": ["ポム・レインパフ", "ポム", "Pomu"],
    "Finana Ryugu": ["フィナーナ・竜宮", "フィナーナ", "Finana"],
    "Selen Tatsuki": ["セレン・龍月", "セレン", "Selen", "Dokibird"],
    "Rosemi Lovelock": ["ロセミ・ラブロック", "ロセミ", "Rosemi"],
    "Petra Gurin": ["ペトラ・グリン", "ペトラ", "Petra"],
    "Nina Kosaka": ["にーな", "ニーナ", "Nina"],
    "Millie Parfait": ["ミリー・パフェ", "ミリー", "Millie"],
    "Enna Alouette": ["エンナ・アルエット", "エンナ", "Enna"],
    "Reimu Endou": ["遠藤霊夢", "れいむ", "Reimu"],
    "Hex Haywire": ["ヘックス", "Hex", "ヘックス・ヘイワイヤー"],
    "Kotoka Torahime": ["虎姫コトカ", "コトカ", "Kotoka"],
    "Ver Vermillion": ["ヴェール", "Ver"],
    "Claude Clawmark": ["クロード", "Claude"],
    "Kunai Nakasato": ["中里くない", "くない", "Kunai"],
    "Victoria Brightshield": ["ヴィクトリア", "Victoria"],
    "Doppio Dropscythe": ["ドッピオ", "Doppio"],
    "Meloco Kyoran": ["狂蘭メロコ", "メロコ", "Meloco"],
    "Genzuki Tojiro": ["弦月藤士郎", "とうじろう"],
    "Sakayori Soma": ["酒寄ソウマ", "ソウマ"],
    "Nagao Kei": ["長尾景", "けい", "景"],
    "Kaida Haru": ["甲斐田晴", "はる"],
    "Fuwa Minato": ["不破湊", "みなと"],
    "Raito": ["雷斗", "らいと"],
    "Leos Vincent": ["レオス・ヴィンセント", "レオス"],
    "Oliver Evans": ["オリバー・エバンス", "オリバー"],
    "Axia Krone": ["アクシア・クローネ", "アクシア"],
    "Kanda Shoichi": ["神田笑一", "しょういち"],
    "Amamiya Kokoro": ["天宮こころ", "こころ"],
    "Rizu-kyun": ["りずきゅん"],
    "Sister Cleaire": ["シスター・クレア", "クレア"],
    "Elu": ["える"],
    "Usaki Mito": ["宇佐美みと", "みと"],
    "Tsukino Mito": ["月ノ美兎", "美兎", "みと"],
    "Higuchi Kaede": ["樋口楓", "かえで"],
    "Shizuka Rin": ["静凛", "しずか"],
    "Yuhi Riri": ["夕陽リリ", "リリ"],
    "Suzuhara Lulu": ["鈴原るる", "るる"],
    "Moira": ["モイラー", "モイア"],
    "Ryushen": ["緑仙", "りゅしぇん", "リュシェン"],
    "Honma Himawari": ["本間ひまわり", "ひまわり"],
    "Makaino Ririmu": ["魔界ノりりむ", "りりむ"],
    "Yuuhi Riri": ["夕陽リリ"],
    "Dola": ["ドーラ"],
    "Ars Almal": ["アルス・アルマル", "アルス"],
    "Aiba Uiha": ["愛芭ういは", "ういは"],
    "Shiina Yuika": ["椎名唯華", "ゆいか"],
    "Suha": ["すは"],
    "Fushimi Gaku": ["伏見ガク", "ガク"],
    "Kenmochi Touya": ["剣持刀也"],
}

_BY_PAREN = re.compile(
    r"\s*[\(（]\s*(?:by|from|aka|female|male|persona|alt|formerly|formerly known)\b[^）)]*[\)）]",
    re.I,
)
_PERSONA_NOTE = re.compile(
    r"\s*[\(（][^）)]*(?:persona|form|version|mode)[^）)]*[\)）]",
    re.I,
)
_BY_PREFIX = re.compile(r"^(?:by|from)\s+", re.I)
_BAD_EXACT = {
    "onii-chan",
    "onee-chan",
    "nii-san",
    "nee-san",
    "senpai",
    "chan",
    "kun",
    "san",
    "sama",
}
_GENERIC = re.compile(
    r"^(onii|onee|nii|nee|senpai)[- ]?(chan|san|sama)?$",
    re.I,
)


def clean_alias(a: str) -> list[str]:
    s = (a or "").strip()
    if not s:
        return []
    # 拆开坏掉的多值行
    chunks = re.split(r"[/|;；、]+", s)
    out: list[str] = []
    for chunk in chunks:
        t = chunk.strip()
        t = _BY_PAREN.sub("", t).strip()
        t = _PERSONA_NOTE.sub("", t).strip()
        t = _BY_PREFIX.sub("", t).strip(" ·・,-")
        # 去掉所有括号注释（归因/形态说明）
        t = re.sub(r"[\(（][^）)]*[\)）]", "", t).strip()
        # 去掉残留半截括号 / 脏尾巴
        t = re.sub(r"[\(（][^）)]*$", "", t).strip()
        t = re.sub(r"^[）)]+", "", t).strip()
        t = t.strip(" )）(（]【[》>\"'")
        # wiki 偶发粘连 Sanya(femalepersona)
        t = re.sub(
            r"(?i)(female|male)?persona$",
            "",
            t,
        ).strip(" -_")
        if not t or len(t) > 36:
            continue
        if "http" in t.lower():
            continue
        if _GENERIC.match(t) or t.lower() in _BAD_EXACT:
            continue
        if t.lower().startswith("by "):
            continue
        # 过短英文碎片
        if re.fullmatch(r"[A-Za-z]{1,2}", t):
            continue
        out.append(t)
        # 去空格罗马音
        if " " in t and re.search(r"[A-Za-z]", t):
            out.append(t.replace(" ", ""))
    return out


def uniq(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for a in items:
        k = a.strip().lower()
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(a.strip())
    return out


def main() -> None:
    chars = json.loads((ROOT / "characters.json").read_text(encoding="utf-8"))
    nicks = json.loads((ROOT / "nicknames.json").read_text(encoding="utf-8"))
    by_id = nicks.get("by_id") or {}
    by_name = nicks.get("by_name") or {}

    # 其他角色的正式名，用于剔除「开玩笑互叫外号」造成的串台
    official_names: set[str] = set()
    for c in chars:
        for key in (c.get("fullName"), c.get("name"), c.get("characterId")):
            if key:
                official_names.add(str(key).strip().lower())
                official_names.add(str(key).replace(" ", "").strip().lower())

    def drop_foreign(aliases: list[str], self_names: set[str]) -> list[str]:
        out = []
        for a in aliases:
            k = a.strip().lower()
            k2 = a.replace(" ", "").strip().lower()
            if k in official_names and k not in self_names:
                continue
            if k2 in official_names and k2 not in self_names:
                continue
            out.append(a)
        return out

    for cid, lst in list(by_id.items()):
        cleaned: list[str] = []
        for a in lst or []:
            cleaned.extend(clean_alias(str(a)))
        by_id[cid] = uniq(cleaned)

    for name, lst in list(by_name.items()):
        cleaned = []
        for a in lst or []:
            cleaned.extend(clean_alias(str(a)))
        # extras
        for extra in EXTRA_BY_NAME.get(str(name), []):
            cleaned.extend(clean_alias(extra))
        by_name[name] = uniq(cleaned)

    # 同步到 character + 补 JP 展示名
    for c in chars:
        cid = str(c.get("characterId") or "")
        title = (c.get("fullName") or c.get("name") or "").strip()
        self_names = {
            cid.lower(),
            title.lower(),
            title.replace(" ", "").lower(),
            str(c.get("name") or "").strip().lower(),
        }
        self_names = {x for x in self_names if x}
        merged = list(c.get("aliases") or [])
        merged.extend(by_id.get(cid) or [])
        merged.extend(by_name.get(title) or [])
        for extra in EXTRA_BY_NAME.get(title, []):
            merged.extend(clean_alias(extra))
        # 再洗一遍角色自带 aliases
        cleaned = []
        for a in merged:
            cleaned.extend(clean_alias(str(a)))
        cleaned = uniq(drop_foreign(cleaned, self_names))
        c["aliases"] = cleaned
        by_id[cid] = uniq(drop_foreign(by_id.get(cid) or [], self_names) + cleaned)
        by_name[title] = uniq(
            drop_foreign(by_name.get(title) or [], self_names) + cleaned
        )
        jp = [
            a
            for a in cleaned
            if re.search(r"[\u3040-\u30ff\u3400-\u9fff]", a) and len(a) <= 20
        ]
        if jp:
            c["fullNameJapanese"] = jp[0]
            if not re.search(r"[\u4e00-\u9fff]", str(c.get("fullNameChinese") or "")):
                # 优先汉字名作中文展示
                han = [a for a in jp if re.search(r"[\u4e00-\u9fff]", a)]
                c["fullNameChinese"] = (han[0] if han else jp[0])
                c["name"] = c["fullNameChinese"]

    out = {"by_id": by_id, "by_name": by_name}
    (ROOT / "nicknames.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (ROOT / "characters.json").write_text(
        json.dumps(chars, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"cleaned vt nicknames: chars={len(chars)} by_id={len(by_id)}")


if __name__ == "__main__":
    main()
