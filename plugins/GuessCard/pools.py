"""多游戏卡池加载。"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

_log = logging.getLogger("GuessCard.pools")

_PUNCT_RE = re.compile(
    r"[\s·・‧•\.．,，/\\_\-~～'\"“”‘’\[\]〔〕()（）「」『』【】<>〈〉|&＋+]+"
)

PJSK_REMOTE_BASE = "https://storage.exmeaning.com/sekai-jp-assets/character"

# 用户输入别名 → 卡池 id
POOL_ALIASES: dict[str, str] = {
    "pjsk": "pjsk",
    "sekai": "pjsk",
    "世奇": "pjsk",
    "初音": "pjsk",
    "fgo": "fgo",
    "fg": "fgo",
    "命运": "fgo",
    "fate": "fgo",
    "grandorder": "fgo",
    "e7": "e7",
    "epic7": "e7",
    "epicseven": "e7",
    "第七史诗": "e7",
    "史诗": "e7",
    "ak": "ak",
    "arknights": "ak",
    "明日方舟": "ak",
    "方舟": "ak",
    "mrfz": "ak",
    "pcr": "pcr",
    "priconne": "pcr",
    "princessconnect": "pcr",
    "公主连接": "pcr",
    "公主连结": "pcr",
    "公连": "pcr",
    "gi": "gi",
    "genshin": "gi",
    "ys": "gi",
    "原神": "gi",
    "vt": "vt",
    "vtuber": "vt",
    "vup": "vt",
    "虚拟主播": "vt",
    "管人": "vt",
    "lcb": "lcb",
    "limbus": "lcb",
    "limbuscompany": "lcb",
    "边狱巴士": "lcb",
    "边狱": "lcb",
    "ww": "ww",
    "wuwa": "ww",
    "wutheringwaves": "ww",
    "鸣潮": "ww",
    "ygo": "ygo",
    "yugioh": "ygo",
    "游戏王": "ygo",
    "遊戯王": "ygo",
    "遊戲王": "ygo",
    "uma": "uma",
    "umamusume": "uma",
    "赛马娘": "uma",
    "馬娘": "uma",
    "马娘": "uma",
    "hs": "hs",
    "hearthstone": "hs",
    "炉石": "hs",
    "炉石传说": "hs",
    "sv": "sv",
    "szb": "sv",
    "shadowverse": "sv",
    "影之诗": "sv",
    "影詩": "sv",
    "gbf": "gbf",
    "granblue": "gbf",
    "granbluefantasy": "gbf",
    "碧蓝幻想": "gbf",
    "碧藍幻想": "gbf",
    "ba": "ba",
    "bluearchive": "ba",
    "蔚蓝档案": "ba",
    "蔚藍檔案": "ba",
    "碧蓝档案": "ba",
}

POOL_DISPLAY: dict[str, str] = {
    "pjsk": "PJSK",
    "fgo": "FGO",
    "e7": "第七史诗",
    "ak": "明日方舟",
    "pcr": "公主连接",
    "gi": "原神",
    "vt": "虚拟主播",
    "lcb": "边狱巴士",
    "ww": "鸣潮",
    "ygo": "游戏王",
    "uma": "赛马娘",
    "hs": "炉石传说",
    "sv": "影之诗",
    "gbf": "碧蓝幻想",
    "ba": "蔚蓝档案",
}

CLASS_CN = {
    "saber": "Saber",
    "archer": "Archer",
    "lancer": "Lancer",
    "rider": "Rider",
    "caster": "Caster",
    "assassin": "Assassin",
    "berserker": "Berserker",
    "ruler": "Ruler",
    "avenger": "Avenger",
    "alterEgo": "Alterego",
    "moonCancer": "MoonCancer",
    "foreigner": "Foreigner",
    "pretender": "Pretender",
    "shielder": "Shielder",
    "beast": "Beast",
    "unBeast": "Beast",
}


@dataclass
class CardPool:
    pool_id: str
    display_name: str
    cards: list[dict] = field(default_factory=list)
    characters: dict[Any, dict] = field(default_factory=dict)
    valid_answers: set[str] = field(default_factory=set)

    @property
    def ok(self) -> bool:
        return bool(self.cards) and bool(self.characters)


def resolve_pool_id(raw: Optional[str], default: str = "pjsk") -> Optional[str]:
    if raw is None or not str(raw).strip():
        return default
    key = str(raw).strip().lower()
    return POOL_ALIASES.get(key)


def normalize_answer(text: str) -> str:
    """忽略大小写、空格、中点/括号等标点，便于简称作答。"""
    s = (text or "").strip().lower()
    s = re.sub(r"^[!！]+", "", s)
    s = s.replace("&amp;", "").replace("amp;", "")
    s = _PUNCT_RE.sub("", s)
    return s


def _split_name_parts(name: str) -> list[str]:
    parts = re.split(r"[·・‧•〔\[(（/|,，]", name)
    return [p.strip() for p in parts if p and p.strip()]


_GENERIC_EN = {"the", "a", "an", "of", "and", "assoc", "south", "north", "section"}


_BY_PAREN_RE = re.compile(
    r"\s*[\(（]\s*(?:by|from|aka)\b[^）)]*[\)）]",
    re.I,
)


def _sanitize_name_for_keys(raw: str) -> str:
    """去掉 wiki 昵称里的 (by XXX) 归因，避免把别人名字收成答案键。"""
    s = _BY_PAREN_RE.sub("", raw or "").strip()
    # 残留坏括号说明直接丢掉括号段
    s = re.sub(r"[\(（][^）)]*[\)）]", "", s).strip()
    return s


def expand_answer_keys(*names: Optional[str]) -> set[str]:
    """从一个角色的多个名称扩展出可判定的答案键（已 normalize）。"""
    keys: set[str] = set()
    for name in names:
        if not name:
            continue
        raw = _sanitize_name_for_keys(str(name).strip())
        if not raw:
            continue
        raw_key = normalize_answer(raw)
        keys.add(raw_key)

        def _add_part(part: str) -> None:
            part = _sanitize_name_for_keys(part)
            if not part or part.lower() in _GENERIC_EN:
                return
            nk = normalize_answer(part)
            if len(nk) < 2:
                return
            # 纯拉丁短词易串台（Titaniklad the Ash Dragon → Ash）
            # 整名本身就是短外号（如 Ash）时仍保留
            if (
                re.fullmatch(r"[a-z0-9]+", nk)
                and len(nk) < 4
                and nk != raw_key
            ):
                return
            keys.add(nk)

        for part in _split_name_parts(raw):
            _add_part(part)
        # 空白分词（「拉·曼却领 神父」→ 神父）
        for part in re.split(r"[\s·・‧]+", raw):
            _add_part(part.strip())
        compact = re.sub(r"[^0-9a-zA-Z\u3040-\u30ff\u3400-\u9fff]", "", raw)
        if compact:
            keys.add(normalize_answer(compact))
    return {k for k in keys if k and not k.isdigit()}


def _title_name_combos(title: str, name: str) -> list[str]:
    """称号+角色名组合：神父 + 格里高尔 → 神父格里高尔。"""
    title = (title or "").strip()
    name = (name or "").strip()
    if not name:
        return []
    out: list[str] = []
    if title:
        out.extend([f"{title}{name}", f"{title} {name}"])
        tokens = [t for t in re.split(r"[\s·・‧]+", title) if t]
        if tokens:
            last = tokens[-1]
            if len(last) >= 2 and last != name:
                out.extend([f"{last}{name}", f"{last} {name}"])
            if len(tokens) >= 2:
                tail2 = "".join(tokens[-2:])
                if len(tail2) >= 2:
                    out.append(f"{tail2}{name}")
    return out


# 常见皮肤/形态中文前缀（E7 / 其它池也可用）
_CN_FORM_PREFIXES = (
    "战术型",
    "战术性",
    "杀手",
    "暗杀者",
    "仲裁者",
    "幽影",
    "海滨",
    "夏日",
    "冬日",
    "竞赛",
    "新春",
    "落日",
    "月光",
    "守护者",
    "指挥官",
    "审判者",
    "暴走",
    "血色",
    "森之贤者",
    "剑之君主",
    "剑之君王",
    "银光",
    "翠绿",
    "蔷薇",
    "小小",
    "幼年",
    "传说",
    "神话",
)

_ATTR_CN = {
    "ice": "水",
    "fire": "火",
    "wind": "木",
    "earth": "土",
    "light": "光",
    "dark": "暗",
}


def _cn_typo_variants(s: str) -> list[str]:
    """常见形近字/口误：型↔性、丽↔莉。"""
    out = [s]
    if "型" in s:
        out.append(s.replace("型", "性"))
    if "性" in s:
        out.append(s.replace("性", "型"))
    more: list[str] = []
    for item in out:
        if "丽" in item:
            more.append(item.replace("丽", "莉"))
        if "莉" in item:
            more.append(item.replace("莉", "丽"))
    out.extend(more)
    return list(dict.fromkeys(a for a in out if a))


def _chinese_form_aliases(cn: str, attribute: str = "") -> list[str]:
    """从中文全名展开简称：去前缀、末二字、属性+名、型性/丽莉变体。"""
    cn = (cn or "").strip()
    if not cn or not re.search(r"[\u4e00-\u9fff]", cn):
        return []
    raw: list[str] = [cn]
    # 去形态前缀 → 本体名
    for p in _CN_FORM_PREFIXES:
        if cn.startswith(p) and len(cn) > len(p) + 1:
            raw.append(cn[len(p) :])
            break
    # 纯汉字末 2 字作常用短称（战术型可丽→可丽）
    cjk = "".join(ch for ch in cn if "\u4e00" <= ch <= "\u9fff")
    if len(cjk) >= 3:
        raw.append(cjk[-2:])
    # 属性外号：水可丽
    attr_cn = _ATTR_CN.get((attribute or "").lower().strip(), "")
    short = cjk[-2:] if len(cjk) >= 2 else cjk
    if attr_cn and short and len(short) >= 2:
        raw.append(f"{attr_cn}{short}")
        # 连全称尾（战术型可丽 → 水战术型可丽 不太用；水+去前缀）
        for p in _CN_FORM_PREFIXES:
            if cn.startswith(p) and len(cn) > len(p) + 1:
                raw.append(f"{attr_cn}{cn[len(p):]}")
                break
    # 形近字
    out: list[str] = []
    for item in raw:
        out.extend(_cn_typo_variants(item))
    return list(dict.fromkeys(a for a in out if a))


def character_answer_keys(character: dict) -> set[str]:
    names: list[Optional[str]] = [
        character.get("name"),
        character.get("fullNameChinese"),
        character.get("fullNameJapanese"),
        character.get("fullName"),
        character.get("className"),
    ]
    names.extend(character.get("aliases") or [])
    cn = (character.get("fullNameChinese") or character.get("name") or "").strip()
    title = (character.get("className") or "").strip()
    names.extend(_title_name_combos(title, cn))
    # fullName 里也可能带称号
    full = (character.get("fullName") or "").strip()
    if full and cn and full.endswith(cn) and len(full) > len(cn):
        names.extend(_title_name_combos(full[: -len(cn)].strip(), cn))
    # 中文形态名/属性外号（E7 战术型可丽 → 可丽/水可丽/战术性可丽）
    names.extend(
        _chinese_form_aliases(cn, str(character.get("attribute") or ""))
    )
    return expand_answer_keys(*names)


def display_answer_name(character: dict) -> str:
    """公布答案优先中文完整名；无中文时优先原文全名（英/拉丁），再日文。"""
    full = (character.get("fullName") or "").strip()
    cn = (character.get("fullNameChinese") or character.get("name") or "").strip()
    jp = (character.get("fullNameJapanese") or "").strip()
    if full and re.search(r"[\u4e00-\u9fff]", full):
        return full
    if cn and re.search(r"[\u4e00-\u9fff]", cn):
        return cn
    # 碧蓝幻想等：英文名比假名更常用
    if full:
        return full
    if jp and re.search(r"[\u4e00-\u9fff\u3040-\u30ff]", jp):
        return jp
    return cn or jp or full or (character.get("name") or "?")


def suggest_answers(character: dict, limit: int = 4) -> list[str]:
    """公布答案时展示的友好简称（中/日/英/罗马音混排）。"""
    primary = display_answer_name(character)
    cn = (character.get("fullNameChinese") or character.get("name") or "").strip()
    jp = (character.get("fullNameJapanese") or "").strip()
    en = (character.get("fullName") or "").strip()
    cands: list[str] = []
    # 中文简称（去前缀 / 属性前缀）优先提示
    for a in _chinese_form_aliases(cn, str(character.get("attribute") or "")):
        if normalize_answer(a) != normalize_answer(primary):
            cands.append(a)
    # 中文名若与完整称号不同，优先提示
    if cn and normalize_answer(cn) != normalize_answer(primary):
        cands.append(cn)
    if jp and normalize_answer(jp) != normalize_answer(primary):
        cands.append(jp)
    if (
        en
        and normalize_answer(en) != normalize_answer(primary)
        and re.search(r"[A-Za-z]", en)
    ):
        cands.append(en)
    for a in character.get("aliases") or []:
        a = str(a).strip()
        if not a:
            continue
        if a.lower().startswith("amp"):
            continue
        # 中日优先；英文/罗马音保留较短者
        if re.search(r"[\u4e00-\u9fff\u3040-\u30ff]", a):
            cands.append(a)
        elif len(a) <= 20:
            cands.append(a)
    # 去重保序
    seen: set[str] = set()
    out: list[str] = []
    primary_key = normalize_answer(primary)
    for item in cands:
        key = normalize_answer(item)
        if not key or key in seen or key == primary_key:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def merge_nick_tables(*tables: dict) -> dict:
    """合并多份 nicknames 表（by_id / by_name）。"""
    out: dict[str, dict[str, list[str]]] = {"by_id": {}, "by_name": {}}
    for table in tables:
        if not table:
            continue
        for bucket in ("by_id", "by_name"):
            for key, values in (table.get(bucket) or {}).items():
                if not key:
                    continue
                items = out[bucket].setdefault(str(key), [])
                seen = set(items)
                for value in values or []:
                    text = str(value).strip()
                    if text and text not in seen:
                        items.append(text)
                        seen.add(text)
    return out


def load_custom_nicknames(data_dir: Path) -> dict:
    path = data_dir / "custom_nicknames.json"
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        _log.warning("读取自定义昵称表失败 %s: %s", path, e)
        return {}


def save_custom_nicknames(data_dir: Path, data: dict) -> None:
    path = data_dir / "custom_nicknames.json"
    data_dir.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def character_lookup_keys(character: dict) -> list[str]:
    """写入 custom nicknames 时同步更新的 by_name 键。"""
    keys: list[str] = []
    seen: set[str] = set()
    for field in (
        "fullNameChinese",
        "fullName",
        "fullNameJapanese",
        "name",
    ):
        value = character.get(field)
        if not value:
            continue
        text = str(value).strip()
        if text and text not in seen:
            seen.add(text)
            keys.append(text)
    display = display_answer_name(character)
    if display and display not in seen:
        keys.append(display)
    return keys


def find_character(
    characters: dict[Any, dict], query: str
) -> tuple[Optional[dict], list[dict]]:
    """按角色 id / 中英文名查找。返回 (唯一匹配, 歧义列表)。"""
    q = (query or "").strip()
    if not q:
        return None, []
    if q in characters:
        return characters[q], []

    norm_q = normalize_answer(q)
    exact: list[dict] = []
    partial: list[dict] = []
    for character in characters.values():
        names = [
            character.get("characterId"),
            character.get("name"),
            character.get("fullName"),
            character.get("fullNameChinese"),
            character.get("fullNameJapanese"),
            display_answer_name(character),
        ]
        matched_exact = False
        matched_partial = False
        for name in names:
            if not name:
                continue
            text = str(name)
            n = normalize_answer(text)
            if n == norm_q:
                matched_exact = True
                break
            if norm_q and len(norm_q) >= 2 and norm_q in n:
                matched_partial = True
        if matched_exact:
            exact.append(character)
        elif matched_partial:
            partial.append(character)

    if len(exact) == 1:
        return exact[0], []
    if len(exact) > 1:
        return None, exact
    if len(partial) == 1:
        return partial[0], []
    return None, partial


def reload_character_from_disk(
    pool: CardPool,
    pool_dir: Path,
    character_id: str,
    custom_table: Optional[dict] = None,
) -> dict:
    """从 characters.json 重载单角色并套用全部昵称表。"""
    with open(pool_dir / "characters.json", "r", encoding="utf-8") as f:
        characters_data = json.load(f)
    base = next(
        (c for c in characters_data if c.get("characterId") == character_id),
        None,
    )
    if not base:
        raise KeyError(character_id)
    character = dict(base)
    nick_table = merge_nick_tables(_load_nicknames(pool_dir), custom_table)
    _apply_nicknames(character, nick_table)
    keys = character_answer_keys(character)
    character["_answer_keys"] = keys
    pool.characters[character_id] = character
    return character


def rebuild_valid_answers(pool: CardPool) -> None:
    pool.valid_answers = set()
    for character in pool.characters.values():
        pool.valid_answers.update(
            character.get("_answer_keys") or character_answer_keys(character)
        )


def add_custom_aliases(
    data_dir: Path,
    pool_id: str,
    character: dict,
    aliases: Iterable[str],
    *,
    all_custom: Optional[dict] = None,
) -> tuple[list[str], list[str]]:
    """追加自定义别名并落盘。返回 (新增, 已存在跳过)。"""
    data = all_custom if all_custom is not None else load_custom_nicknames(data_dir)
    pool_table = data.setdefault(pool_id, {"by_id": {}, "by_name": {}})
    by_id = pool_table.setdefault("by_id", {})
    by_name = pool_table.setdefault("by_name", {})

    cid = str(character.get("characterId") or "")
    if not cid:
        raise ValueError("角色缺少 characterId")

    existing = set(character.get("_answer_keys") or character_answer_keys(character))
    added: list[str] = []
    skipped: list[str] = []
    for alias in aliases:
        text = str(alias).strip()
        if not text:
            continue
        key = normalize_answer(text)
        if not key:
            continue
        if key in existing:
            skipped.append(text)
            continue
        existing.add(key)
        added.append(text)

    if not added:
        return added, skipped

    id_items = by_id.setdefault(cid, [])
    id_seen = set(id_items)
    for item in added:
        if item not in id_seen:
            id_items.append(item)
            id_seen.add(item)

    for name_key in character_lookup_keys(character):
        name_items = by_name.setdefault(name_key, [])
        name_seen = set(name_items)
        for item in added:
            if item not in name_seen:
                name_items.append(item)
                name_seen.add(item)

    save_custom_nicknames(data_dir, data)
    return added, skipped


def _load_nicknames(pool_dir: Path) -> dict:
    path = pool_dir / "nicknames.json"
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        _log.warning("读取昵称表失败 %s: %s", path, e)
        return {}


def _apply_nicknames(character: dict, nick_table: dict) -> None:
    """把昵称表合并进 aliases。"""
    if not nick_table:
        return
    aliases = list(character.get("aliases") or [])
    seen = {normalize_answer(a) for a in aliases if a}
    by_id = nick_table.get("by_id") or {}
    by_name = nick_table.get("by_name") or {}
    extra: list[str] = []
    cid = str(character.get("characterId") or "")
    extra.extend(by_id.get(cid) or [])
    # 只用角色主名查表，禁止用 aliases 再展开（否则 A 的玩笑外号「Ibrahim」
    # 会把 B 的整表答案键并进来，造成串台）。
    for key in (
        character.get("name"),
        character.get("fullNameChinese"),
        character.get("fullNameJapanese"),
        character.get("fullName"),
    ):
        if not key:
            continue
        extra.extend(by_name.get(str(key)) or [])
    for item in extra:
        item = str(item).strip()
        if not item:
            continue
        k = normalize_answer(item)
        if not k or k in seen:
            continue
        seen.add(k)
        aliases.append(item)
    character["aliases"] = aliases
    # 若中文名仍是纯英文，用昵称表里的中文填 fullNameChinese
    cn = (character.get("fullNameChinese") or "").strip()
    if cn and not re.search(r"[\u4e00-\u9fff]", cn):
        for item in aliases:
            if re.search(r"[\u4e00-\u9fff]", str(item)):
                character["fullNameChinese"] = str(item).strip()
                if not re.search(r"[\u4e00-\u9fff]", str(character.get("name") or "")):
                    character["name"] = character["fullNameChinese"]
                break


def load_pool(
    resources_root: Path,
    pool_id: str,
    custom_nick_table: Optional[dict] = None,
) -> CardPool:
    pool_dir = resources_root / pool_id
    display = POOL_DISPLAY.get(pool_id, pool_id.upper())
    pool = CardPool(pool_id=pool_id, display_name=display)
    try:
        with open(pool_dir / "guess_cards.json", "r", encoding="utf-8") as f:
            pool.cards = json.load(f)
        with open(pool_dir / "characters.json", "r", encoding="utf-8") as f:
            characters_data = json.load(f)
        pool.characters = {c["characterId"]: c for c in characters_data}
    except FileNotFoundError as e:
        _log.error("加载卡池 %s 失败: %s", pool_id, e)
        return pool

    nick_table = merge_nick_tables(_load_nicknames(pool_dir), custom_nick_table)
    for character in pool.characters.values():
        _apply_nicknames(character, nick_table)
        keys = character_answer_keys(character)
        character["_answer_keys"] = keys
        pool.valid_answers.update(keys)
    _log.info(
        "卡池 %s 加载完成: %d 角色 / %d 卡面 / %d 有效答案键",
        pool_id,
        len(pool.characters),
        len(pool.cards),
        len(pool.valid_answers),
    )
    return pool


def resolve_card_image_url(pool_id: str, card: dict) -> str:
    if card.get("image_url"):
        return str(card["image_url"])
    if pool_id == "pjsk":
        state = card.get("_card_state") or "normal"
        filename = f"card_{state}.webp"
        bundle = card["assetbundleName"]
        return f"{PJSK_REMOTE_BASE}/member/{bundle}/{filename}"
    # fallback face
    if card.get("face_url"):
        return str(card["face_url"])
    raise ValueError(f"卡池 {pool_id} 卡面缺少 image_url: {card.get('id')}")
