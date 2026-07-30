"""分批从 wiki 拉外号/多语名称，写入各卡池 nicknames.json 并回填 characters。

优先: vt（virtualyoutuber.fandom）
其它: gi / ak / ww 做轻量补充
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "resources"
UA = {"User-Agent": "Mozilla/5.0 NcatBot-GuessCard/1.0"}

_TAG_RE = re.compile(r"<[^>]+>")
_REF_RE = re.compile(r"<ref[\s\S]*?</ref>|<ref[^/]*/>", re.I)
_LINK_RE = re.compile(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]")
_URL_RE = re.compile(r"\[https?://[^ ]+ ([^\]]+)\]")


def fetch_json(url: str, timeout: int = 25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def clean_cell(raw: str) -> str:
    s = raw or ""
    s = _REF_RE.sub("", s)
    s = _TAG_RE.sub("\n", s)
    s = _LINK_RE.sub(r"\1", s)
    s = _URL_RE.sub(r"\1", s)
    s = s.replace("'''", "").replace("''", "")
    s = re.sub(r"\{\{[^}]+\}\}", "", s)
    parts = []
    for line in re.split(r"[\n/、，,;；]+", s):
        line = line.strip(" ·・•-–—")
        line = re.sub(r"\s+", " ", line).strip()
        if not line or line.lower() in {"n/a", "none", "?", "-"}:
            continue
        # 去掉纯括号说明行
        if line.startswith("(") and line.endswith(")"):
            line = line[1:-1].strip()
        if line:
            parts.append(line)
    return parts


def parse_infobox_field(wikitext: str, field: str) -> list[str]:
    # |field = value   （值可跨到下一 | 之前）
    pat = rf"\|\s*{re.escape(field)}\s*=\s*([^\n|]*(?:\n(?!\|)[^\n|]*)*)"
    m = re.search(pat, wikitext, re.I)
    if not m:
        return []
    return clean_cell(m.group(1))


def extract_paren_names(text: str) -> list[str]:
    """Kuzuha (葛葉) / 虎姫コトカ (Torahime Kotoka)"""
    out = []
    for m in re.finditer(r"[（(]([^）)]+)[）)]", text or ""):
        out.extend(clean_cell(m.group(1)))
    return out


def fetch_vt_wikitext(title: str) -> str:
    api = "https://virtualyoutuber.fandom.com/api.php?" + urllib.parse.urlencode(
        {
            "action": "parse",
            "page": title,
            "prop": "wikitext",
            "format": "json",
        }
    )
    j = fetch_json(api, timeout=25)
    wt = (j.get("parse") or {}).get("wikitext") or {}
    if isinstance(wt, dict):
        return wt.get("*") or ""
    return str(wt or "")


def enrich_vt() -> None:
    pool = ROOT / "vt"
    chars = json.loads((pool / "characters.json").read_text(encoding="utf-8"))
    by_id: dict[str, list[str]] = {}
    by_name: dict[str, list[str]] = {}
    print(f"[VT] enrich {len(chars)} chars ...", flush=True)

    for i, c in enumerate(chars):
        title = (c.get("fullName") or c.get("name") or "").strip()
        cid = str(c.get("characterId") or "")
        if not title or not cid:
            continue
        aliases: list[str] = []
        try:
            wt = fetch_vt_wikitext(title)
        except Exception as e:
            print(f"  FAIL {title}: {type(e).__name__} {e}", flush=True)
            time.sleep(0.3)
            continue

        original = parse_infobox_field(wt, "original_name")
        nicks = parse_infobox_field(wt, "nick_name")
        fans = parse_infobox_field(wt, "fan_name")
        # 简介里 '''Name''' (日文)
        m = re.search(r"'''([^']+)'''\s*[（(]([^）)]+)[）)]", wt)
        if m:
            aliases.append(m.group(1).strip())
            aliases.extend(clean_cell(m.group(2)))

        aliases.append(title)
        aliases.append(title.replace(" ", ""))
        # 空格拆分罗马音：Nagao Kei -> Nagao / Kei 太短易撞，只保留全名+去空格
        aliases.extend(original)
        for o in original:
            aliases.extend(extract_paren_names(o))
            # 去掉括号后的日文本体
            base = re.sub(r"[（(][^）)]+[）)]", "", o).strip()
            if base:
                aliases.append(base)
        aliases.extend(nicks)
        aliases.extend(fans)

        # 清洗去重（去掉 (by X) 归因，避免答案键串台）
        _by_paren = re.compile(
            r"\s*[\(（]\s*(?:by|from|aka)\b[^）)]*[\)）]", re.I
        )
        uniq: list[str] = []
        seen = set()
        for a in aliases:
            a = str(a).strip()
            a = _by_paren.sub("", a).strip()
            a = re.sub(r"[\(（][^）)]*[\)）]", "", a).strip()
            if not a or len(a) > 40:
                continue
            # 过滤明显句子 / 过泛称呼
            if "http" in a.lower() or a.lower().startswith("by "):
                continue
            if re.fullmatch(
                r"(onii|onee|nii|nee|senpai)[- ]?(chan|san|sama)?", a, re.I
            ):
                continue
            key = a.lower()
            if key in seen:
                continue
            seen.add(key)
            uniq.append(a)

        by_id[cid] = uniq
        by_name[title] = uniq

        # 回填角色字段
        jp_candidates = [
            a
            for a in uniq
            if re.search(r"[\u3040-\u30ff\u3400-\u9fff]", a) and len(a) <= 20
        ]
        c["aliases"] = list(dict.fromkeys([*(c.get("aliases") or []), *uniq]))
        if jp_candidates:
            c["fullNameJapanese"] = jp_candidates[0]
            # 东亚常用汉字名也可作展示名
            if not re.search(r"[\u4e00-\u9fff]", str(c.get("fullNameChinese") or "")):
                c["fullNameChinese"] = jp_candidates[0]
                c["name"] = jp_candidates[0]

        if (i + 1) % 20 == 0:
            print(f"  progress {i+1}/{len(chars)} last={title} n={len(uniq)}", flush=True)
            time.sleep(0.15)
        else:
            time.sleep(0.05)

    nick = {"by_id": by_id, "by_name": by_name}
    (pool / "nicknames.json").write_text(
        json.dumps(nick, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (pool / "characters.json").write_text(
        json.dumps(chars, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[VT] DONE nicknamed={len(by_id)}", flush=True)


def enrich_gi_static() -> None:
    """原神常见外号（wiki/社区常用）。"""
    nick = {
        "by_name": {
            "钟离": ["岩王帝君", "摩拉克斯", "帝君", "Zhongli"],
            "Zhongli": ["钟离", "岩王帝君", "摩拉克斯"],
            "雷电将军": ["影", "巴尔", "将军", "Raiden Shogun", "Raiden"],
            "Raiden Shogun": ["雷电将军", "影", "巴尔"],
            "纳西妲": ["草神", "小吉祥草王", "布耶尔", "Nahida"],
            "Nahida": ["纳西妲", "草神", "小吉祥草王"],
            "芙宁娜": ["Focalors", "芙卡洛斯", "Furina"],
            "Furina": ["芙宁娜", "芙卡洛斯"],
            "那维莱特": ["Neuvillette", "美露莘之友"],
            "Neuvillette": ["那维莱特"],
            "胡桃": ["堂主", "Hu Tao"],
            "Hu Tao": ["胡桃", "堂主"],
            "温迪": ["巴巴托斯", "风神", "Venti"],
            "Venti": ["温迪", "巴巴托斯", "风神"],
            "流浪者": ["散兵", "国崩", "斯卡拉姆齐", "Wanderer"],
            "Wanderer": ["流浪者", "散兵", "国崩"],
            "神里绫华": ["绫华", "Kamisato Ayaka", "Ayaka"],
            "Kamisato Ayaka": ["神里绫华", "绫华", "Ayaka"],
            "神里绫人": ["绫人", "Kamisato Ayato", "Ayato"],
            "宵宫": ["Yoimiya"],
            "Yoimiya": ["宵宫"],
            "八重神子": ["神子", "Yae Miko", "狐妖"],
            "Yae Miko": ["八重神子", "神子"],
            "阿蕾奇诺": ["仆人", "Arlecchino"],
            "Arlecchino": ["阿蕾奇诺", "仆人"],
            "玛薇卡": ["火神", "Mavuika"],
            "Mavuika": ["玛薇卡", "火神"],
            "提纳里": ["Tighnari"],
            "赛诺": ["Cyno"],
            "艾尔海森": ["Alhaitham", "海哥"],
            "Alhaitham": ["艾尔海森", "海哥"],
            "妮露": ["Nilou"],
            "甘雨": ["椰羊", "Ganyu"],
            "Ganyu": ["甘雨", "椰羊"],
            "优菈": ["Eula"],
            "迪卢克": ["卢姥爷", "Diluc"],
            "Diluc": ["迪卢克", "卢姥爷"],
            "凯亚": ["Kaeya"],
            "琴": ["团长", "Jean"],
            "Jean": ["琴", "团长"],
            "可莉": ["火花骑士", "Klee"],
            "Klee": ["可莉", "火花骑士"],
            "阿贝多": ["Albedo"],
            "魈": ["降魔大圣", "Xiao"],
            "Xiao": ["魈", "降魔大圣"],
            "夜兰": ["Yelan"],
            "申鹤": ["Shenhe"],
            "云堇": ["Yun Jin"],
            "瑶瑶": ["Yaoyao"],
            "白术": ["Baizhu"],
            "林尼": ["Lyney"],
            "琳妮特": ["Lynette"],
            "菲米尼": ["Freminet"],
            "娜维娅": ["Navia"],
            "千织": ["Chiori"],
            "闲云": ["Xianyun", "留云借风真君"],
            "Xianyun": ["闲云", "留云借风真君"],
            "嘉明": ["Gaming"],
            "Gaming": ["嘉明"],
            "茜特菈莉": ["Citlali"],
            "Citlali": ["茜特菈莉"],
            "玛拉妮": ["Mualani"],
            "Mualani": ["玛拉妮"],
            "基尼奇": ["Kinich"],
            "Kinich": ["基尼奇"],
            "恰斯卡": ["Chasca"],
            "Chasca": ["恰斯卡"],
            "希诺宁": ["Xilonen"],
            "Xilonen": ["希诺宁"],
            "欧洛伦": ["Ororon"],
            "Ororon": ["欧洛伦"],
            "伊安珊": ["Iansan"],
            "Iansan": ["伊安珊"],
            "瓦雷莎": ["Varesa"],
            "Varesa": ["瓦雷莎"],
            "爱诺": ["Aino"],
            "Aino": ["爱诺"],
            "伊涅芙": ["Ineffa"],
            "Ineffa": ["伊涅芙"],
            "丝柯克": ["Skirk"],
            "Skirk": ["丝柯克"],
            "爱可菲": ["Escoffier"],
            "Escoffier": ["爱可菲"],
            "塔利雅": ["Dahlia"],
            "Dahlia": ["塔利雅"],
            "伊法": ["Ifa"],
            "Ifa": ["伊法"],
        }
    }
    path = ROOT / "gi" / "nicknames.json"
    path.write_text(json.dumps(nick, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[GI] wrote {path}", flush=True)


def enrich_ak_static() -> None:
    nick = {
        "by_name": {
            "阿米娅": ["兔子", "Amiya", "萝卜"],
            "Amiya": ["阿米娅", "兔子"],
            "能天使": ["苹果派", "Exusiai"],
            "Exusiai": ["能天使", "苹果派"],
            "德克萨斯": ["德狗", "Texas"],
            "Texas": ["德克萨斯", "德狗"],
            "推进之王": ["王大锤", "Siege"],
            "Siege": ["推进之王"],
            "银灰": ["银老板", "SilverAsh"],
            "SilverAsh": ["银灰", "银老板"],
            "陈": ["陈晖洁", "Ch'en", "Chen"],
            "Ch'en": ["陈", "陈晖洁"],
            "煌": ["Blaze", "火枪手"],
            "Blaze": ["煌", "火枪手"],
            "W": ["达不溜", "二重阴影"],
            "凯尔希": ["凯爷爷", "Kal'tsit", "猫"],
            "Kal'tsit": ["凯尔希", "凯爷爷"],
            "绮良": ["Kirara"],
            "拉普兰德": ["Lapland", "拉普"],
            "Lapland": ["拉普兰德", "拉普"],
            "斯卡蒂": ["虎鲸", "Skadi"],
            "Skadi": ["斯卡蒂", "虎鲸"],
            "嵯峨": ["Saga"],
            "年": ["Nian"],
            "夕": ["Dusk"],
            "令": ["Ling"],
            "烛烛": ["烛煌"],
            "维什戴尔": ["Wiš'adel", "W改变"],
            "玛恩纳": ["叔叔", "Młynar"],
            "Młynar": ["玛恩纳", "叔叔"],
            "逻各斯": ["Logos"],
            "Logos": ["逻各斯"],
            "调香师": ["Perfumer"],
            "嘉维尔": ["Gaavial", "鳄鱼"],
        }
    }
    path = ROOT / "ak" / "nicknames.json"
    path.write_text(json.dumps(nick, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[AK] wrote {path}", flush=True)


def enrich_e7_static() -> None:
    """E7：常用外号 + 从 characters 自动补中文简称/属性前缀。"""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from pools import _chinese_form_aliases  # noqa: E402

    nick: dict = {
        "by_name": {
            "Ras": ["拉斯", "王太子"],
            "拉斯": ["Ras"],
            "Mercedes": ["梅赛德斯", "梅爷"],
            "梅赛德斯": ["Mercedes"],
            "Aither": ["埃泽"],
            "Vildred": ["维德瑞德", "男刀"],
            "维德瑞德": ["Vildred"],
            "Ken": ["肯恩"],
            "肯恩": ["Ken"],
            "Destina": ["杰斯缇娜"],
            "Angelica": ["安洁莉卡"],
            "安洁莉卡": ["Angelica"],
            "Ruele of Light": ["光之璐艾尔", "光天使"],
            "Arbiter Vildred": ["仲裁者维德瑞德", "暗刀"],
            "Specter Tenebria": ["幽影泰妮布里雅", "暗蒂妈"],
            "Tenebria": ["泰妮布里雅", "蒂妈"],
            "泰妮布里雅": ["Tenebria", "蒂妈"],
            "Haste": ["海斯特"],
            "Krau": ["克劳乌"],
            "克劳乌": ["Krau"],
            "Charles": ["查尔斯"],
            "Seasider Bellona": ["海滨维尔萝娜"],
            "Bellona": ["维尔萝娜"],
            "维尔萝娜": ["Bellona"],
            "Luna": ["露娜"],
            "阿布加多": ["Abigail", "雅碧凯"],
            "Abigail": ["雅碧凯", "阿布加多"],
            "雅碧凯": ["Abigail"],
            "诺托斯": ["Notos"],
            "Notos": ["诺托斯"],
            "露易莎": ["Ruiza"],
            "Ruiza": ["露易莎"],
            "小小赛娜": ["Young Senya", "小赛娜"],
            "Young Senya": ["小小赛娜", "小赛娜"],
            "战术型可丽": [
                "战术性可丽",
                "可丽",
                "可莉",
                "水可丽",
                "水可莉",
                "Coli",
                "Tactical Archetype Coli",
            ],
            "可丽": ["可莉", "Coli"],
            "杀手可丽": ["暗可丽", "暗可莉", "Assassin Coli"],
        }
    }
    path = ROOT / "e7" / "nicknames.json"
    by_name = dict(nick["by_name"])
    by_id: dict = {}
    if path.exists():
        old = json.loads(path.read_text(encoding="utf-8"))
        by_id = dict(old.get("by_id") or {})
        for k, v in (old.get("by_name") or {}).items():
            cur = list(by_name.get(k) or [])
            for item in v:
                if item not in cur:
                    cur.append(item)
            by_name[k] = cur

    # 从角色表自动展开中文简称，写入 aliases 用的 nick 表
    chars_path = ROOT / "e7" / "characters.json"
    if chars_path.exists():
        chars = json.loads(chars_path.read_text(encoding="utf-8"))
        for c in chars:
            cn = (c.get("fullNameChinese") or "").strip()
            en = (c.get("fullName") or "").strip()
            cid = str(c.get("characterId") or "")
            if not cn:
                continue
            extras = _chinese_form_aliases(cn, str(c.get("attribute") or ""))
            if en:
                extras.append(en)
            bucket = list(by_name.get(cn) or [])
            for a in extras:
                if a and a not in bucket:
                    bucket.append(a)
            by_name[cn] = bucket
            if en:
                eb = list(by_name.get(en) or [])
                for a in [cn, *extras]:
                    if a and a not in eb:
                        eb.append(a)
                by_name[en] = eb
            if cid:
                ib = list(by_id.get(cid) or [])
                for a in [cn, *extras]:
                    if a and a not in ib:
                        ib.append(a)
                by_id[cid] = ib

    out = {"by_id": by_id, "by_name": by_name}
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[E7] wrote {path} by_name={len(by_name)} by_id={len(by_id)}", flush=True)


def enrich_pcr_extra() -> None:
    """公连：Hoshino 已有大量别名，只补少数缺口外号。"""
    nick = {
        "by_name": {
            "优衣": ["由依", "种田", "Yuuki"],
            "可可萝": ["妈", "背包", "Kokkoro"],
            "凯露": ["黑猫", "臭鼬", "Karyl", "凯露"],
            "佩可莉姆": ["吃货", "公主", " Pecorine", "贪吃佩可"],
            "贪吃佩可": ["佩可莉姆", "吃货", "公主"],
            "克里斯蒂娜": ["克总", "皇冠"],
            "矛依未": ["511"],
            "镜华": ["小仓唯", "xcw"],
            "纯白闪亮☆星石": ["真爱", "傻白"],
        }
    }
    path = ROOT / "pcr" / "nicknames.json"
    path.write_text(json.dumps(nick, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[PCR] wrote {path}", flush=True)


def enrich_fgo_extra() -> None:
    nick = {
        "by_name": {
            "阿尔托莉雅·潘德拉贡": ["呆毛", "阿尔托莉雅", "Saber", "亚瑟王"],
            "阿尔托莉雅·潘德拉贡〔Alter〕": ["黑呆", "黑Saber"],
            "吉尔伽美什": ["金闪闪", "迦勒底"],
            "梅林": ["花之魔术师", "小莫"],
            "斯卡哈": ["师匠", "Scathach"],
            "斯卡哈·斯卡蒂": ["C妈", "裙裙"],
            "诸葛孔明〔埃尔梅罗II世〕": ["孔明", "老子"],
            "贞德": ["Ruler贞德"],
            "贞德〔Alter〕": ["黑贞", "Jalter"],
            "阿蒂拉": ["阿尔黛拉"],
            "阿蒂拉·the·San〔ta〕": ["弓大王", "弓大为", "圣诞阿蒂拉"],
        }
    }
    path = ROOT / "fgo" / "nicknames.json"
    path.write_text(json.dumps(nick, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[FGO] wrote {path}", flush=True)


def enrich_ww_extra() -> None:
    path = ROOT / "ww" / "nicknames.json"
    old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    by_name = old.get("by_name") or {}
    extra = {
        "Jinhsi": ["今汐", "晋析"],
        "今汐": ["Jinhsi"],
        "Changli": ["长离", "長離"],
        "长离": ["Changli", "長離"],
        "Yinlin": ["吟霖"],
        "吟霖": ["Yinlin"],
        "Jiyan": ["忌炎"],
        "忌炎": ["Jiyan"],
        "Calcharo": ["卡卡罗"],
        "卡卡罗": ["Calcharo"],
        "Encore": ["安可", "恩柯尔"],
        "安可": ["Encore"],
        "Verina": ["维里奈"],
        "维里奈": ["Verina"],
        "Baizhi": ["白芷"],
        "白芷": ["Baizhi"],
        "Yangyang": ["秧秧"],
        "秧秧": ["Yangyang"],
        "Chixia": ["炽霞"],
        "炽霞": ["Chixia"],
        "Taoqi": ["桃祈"],
        "桃祈": ["Taoqi"],
        "Yuanwu": ["渊武"],
        "渊武": ["Yuanwu"],
        "Danjin": ["丹瑾"],
        "丹瑾": ["Danjin"],
        "Sanhua": ["散华"],
        "散华": ["Sanhua"],
        "Mortefi": ["莫特斐"],
        "莫特斐": ["Mortefi"],
        "Aalto": ["奥托"],
        "Lingyang": ["凌阳"],
        "凌阳": ["Lingyang"],
        "Youhu": ["釉瑚"],
        "釉瑚": ["Youhu"],
        "Jianxin": ["鉴心"],
        "鉴心": ["Jianxin"],
        "Xiangli Yao": ["相里要"],
        "相里要": ["Xiangli Yao"],
        "Zhezhi": ["折枝"],
        "折枝": ["Zhezhi"],
        "Shorekeeper": ["守岸人"],
        "守岸人": ["Shorekeeper"],
        "Camellya": ["椿"],
        "椿": ["Camellya"],
        "Carlotta": ["珂莱塔"],
        "珂莱塔": ["Carlotta"],
        "Roccia": ["洛可可"],
        "洛可可": ["Roccia"],
        "Phoebe": ["菲比"],
        "菲比": ["Phoebe"],
        "Brant": ["布兰特"],
        "布兰特": ["Brant"],
        "Cantarella": ["坎特蕾拉"],
        "坎特蕾拉": ["Cantarella"],
        "Zani": ["赞妮"],
        "赞妮": ["Zani"],
        "Ciaccona": ["夏空"],
        "夏空": ["Ciaccona"],
        "Cartethyia": ["卡提希娅"],
        "卡提希娅": ["Cartethyia"],
        "Lupa": ["露帕"],
        "露帕": ["Lupa"],
        "Phrolova": ["弗洛洛"],
        "弗洛洛": ["Phrolova"],
        "Augusta": ["奥古斯塔"],
        "奥古斯塔": ["Augusta"],
        "Iuno": ["尤诺"],
        "尤诺": ["Iuno"],
        "Galbrena": ["嘉白露"],
        "嘉白露": ["Galbrena"],
        "Qiuyuan": ["仇远"],
        "仇远": ["Qiuyuan"],
        "Chisa": ["千咲"],
        "千咲": ["Chisa"],
        "Buling": ["卜灵"],
        "卜灵": ["Buling"],
        "Lynae": ["琳娜艾"],
        "琳娜艾": ["Lynae"],
        "Mornye": ["莫宁妮"],
        "莫宁妮": ["Mornye"],
        "Lucia": ["露西亚"],
        "露西亚": ["Lucia"],
    }
    by_name.update(extra)
    out = {"by_id": old.get("by_id") or {}, "by_name": by_name}
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[WW] wrote {path}", flush=True)


def main() -> None:
    import sys

    targets = sys.argv[1:] or ["gi", "ak", "e7", "pcr", "fgo", "ww", "vt"]
    for t in targets:
        if t == "vt":
            enrich_vt()
        elif t == "gi":
            enrich_gi_static()
        elif t == "ak":
            enrich_ak_static()
        elif t == "e7":
            enrich_e7_static()
        elif t == "pcr":
            enrich_pcr_extra()
        elif t == "fgo":
            enrich_fgo_extra()
        elif t == "ww":
            enrich_ww_extra()
        else:
            raise SystemExit(f"unknown {t}")


if __name__ == "__main__":
    main()
