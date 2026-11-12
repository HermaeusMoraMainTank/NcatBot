"""把 B 站文集里的表情包按作者标注的名字收进 FakeAi 贴纸库。

数据源是图文正文里的 ``pic.comment``（作者给每张图标的名字），
按名字落盘到 ``data/fakeai/stickers/<库名>/``，同名不同图自动成为同一名字的变体，
并写 ``pack.json`` 供 ``StickerCatalog`` 检索。

原图常有 1080p PNG 和 8MB 动图，入库前统一缩到聊天够用的尺寸：
静态 512px、动图 360px，并写 ``_sources.json`` 记录原图哈希，
重跑不会重复下载同一张图。

用法::

    python scripts/import_bilibili_stickers.py --readlist 1063625 --from 1 --to 20
    python scripts/import_bilibili_stickers.py --rebuild-meta   # 只重算 pack.json
    python scripts/import_bilibili_stickers.py --recompress     # 只压缩已有图片
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
}

LIST_URL = "https://api.bilibili.com/x/article/list/web/articles?id={}"
ARTICLE_URL = "https://api.bilibili.com/x/article/view?id={}"

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
MAX_SIDE = 512
MAX_STILL_BYTES = 140_000
MAX_GIF_FRAMES = 48
GIF_PASSES = (
    (320, 96, 280_000),
    (260, 64, 200_000),
    (200, 48, 140_000),
    (170, 40, 110_000),
)
VARIANT_SEP = "·"

# 名字里出现的词 -> (情绪桶, 检索词, 画面补充)；命中越多的桶胜出，平票按 TIE_PRIORITY 取。
# 情绪桶留空 = 只当检索词，不参与情绪判定（看/盯 这类纯动作词）。
LEXICON: dict[str, tuple[str, str, str]] = {
    # happy
    "开心": ("happy", "开心 高兴 愉快", "开心"),
    "笑": ("happy", "笑 微笑 乐", "在笑"),
    "比耶": ("happy", "比耶 剪刀手 耶", "比耶"),
    "星星眼": ("happy", "星星眼 期待 崇拜", "星星眼"),
    "摇": ("happy", "摇 摇摆 晃", "摇来摇去"),
    "蹦": ("happy", "蹦 跳 兴奋", "蹦蹦跳跳"),
    "跳": ("happy", "跳 蹦 兴奋", "跳起来"),
    "转圈": ("happy", "转圈 旋转", "转圈"),
    "旋转": ("happy", "转圈 旋转", "转圈"),
    "想要": ("happy", "想要 求 期待", "想要"),
    "超级棒": ("happy", "棒 点赞 赞 夸奖", "竖拇指夸"),
    "点赞": ("happy", "点赞 赞 认同", "点赞"),
    "摸头": ("happy", "摸头 摸摸 安抚", "被摸头"),
    "茄子": ("happy", "拍照 笑 茄子", "拍照喊茄子"),
    "单眼": ("happy", "单眼 眨眼", "单眼眨眼"),
    "邪魅": ("happy", "邪魅 得意", "邪魅一笑"),
    "叉腰": ("happy", "叉腰 神气 得意", "叉腰"),
    "爱心": ("happy", "爱心 喜欢 比心", "比心"),
    "喜欢": ("happy", "喜欢 爱心 中意", "表示喜欢"),
    "咬": ("happy", "咬 撒娇 闹", "张嘴咬"),
    # sad
    "哭": ("sad", "哭 流泪 委屈 难过", "在哭"),
    "委屈": ("sad", "委屈 难过 求安慰", "委屈"),
    "失落": ("sad", "失落 沮丧 失望", "失落"),
    "垂耳": ("sad", "垂耳 没精神 委屈", "耳朵耷下来"),
    "郁闷": ("sad", "郁闷 烦 不开心", "郁闷"),
    "无助": ("sad", "无助 可怜 求", "无助"),
    "不想活": ("sad", "摆烂 累 想死 崩溃", "摆烂"),
    "紫砂": ("sad", "摆烂 累 想死 崩溃", "摆烂"),
    "泪": ("sad", "泪 哭", "带泪"),
    "伤心": ("sad", "伤心 难过", "伤心"),
    # angry
    "生气": ("angry", "生气 怒 不爽 火大", "生气"),
    "不开心": ("angry", "不开心 生气 摆脸", "不高兴"),
    "不高兴": ("angry", "不高兴 生气 摆脸", "不高兴"),
    "哼": ("angry", "哼 不服 生气", "哼一声"),
    "哈气": ("angry", "哈气 凶 炸毛 警告", "哈气"),
    "恼": ("angry", "恼 生气 不满", "恼火"),
    "嫌弃": ("angry", "嫌弃 鄙视 无语 白眼", "嫌弃"),
    "厌恶": ("angry", "厌恶 嫌弃 恶心", "厌恶"),
    "不屑": ("angry", "不屑 鄙视 看不起", "不屑"),
    "斜眼": ("angry", "斜眼 瞪 不爽", "斜眼看人"),
    "捶": ("angry", "捶 打 揍", "挥拳捶"),
    "打击": ("angry", "打击 打 揍 敲", "动手打"),
    "凶": ("angry", "凶 生气 警告", "凶"),
    "瞪": ("angry", "瞪 盯着 不爽", "瞪着"),
    # shy
    "害羞": ("shy", "害羞 不好意思 脸红", "害羞"),
    "脸红": ("shy", "脸红 害羞 羞涩", "脸红"),
    "心虚": ("shy", "心虚 慌 不好意思", "心虚"),
    "对手指": ("shy", "对手指 心虚 扭捏", "对手指"),
    "抱抱": ("shy", "抱抱 撒娇 求抱", "要抱抱"),
    "撒娇": ("shy", "撒娇 求 抱抱", "撒娇"),
    "情书": ("shy", "情书 喜欢 表白", "递情书"),
    # confused
    "疑惑": ("confused", "疑惑 问号 不解 懵", "疑惑"),
    "问号": ("confused", "问号 疑惑 不解", "一脸问号"),
    "震惊": ("confused", "震惊 吃惊 惊呆", "震惊"),
    "诧异": ("confused", "诧异 吃惊 迷惑", "诧异"),
    "惊恐": ("confused", "惊恐 害怕 惊", "惊恐"),
    "恐慌": ("confused", "恐慌 害怕 慌", "慌"),
    "呆": ("confused", "呆 发愣 放空", "发呆"),
    "歪头": ("confused", "歪头 疑惑 不解", "歪头"),
    "汗": ("confused", "流汗 无语 尴尬", "冒汗"),
    "害怕": ("confused", "害怕 慌 惊", "害怕"),
    "好奇": ("confused", "好奇 疑问", "好奇"),
    "阿巴阿巴": ("confused", "阿巴阿巴 说不出话 懵", "张嘴说不出话"),
    "死机": ("confused", "死机 宕机 大脑空白 懵", "大脑死机"),
    "宕机": ("confused", "宕机 死机 懵", "宕机"),
    # daily
    "点头": ("daily", "点头 同意 嗯嗯", "点头"),
    "嘘": ("daily", "嘘 安静 小声", "比嘘"),
    "静音": ("daily", "静音 安静 闭嘴", "静音"),
    "美图": ("daily", "美图 好看 画面", "好看的一张图"),
    "贴脸": ("daily", "贴脸 凑近 怼镜头", "贴到镜头前"),
    "躺": ("daily", "躺 躺平 摆烂", "躺着"),
    "玩锤子": ("daily", "玩锤子 整活 敲", "拿着锤子玩"),
    "桶": ("daily", "桶 躲 探头", "在桶里"),
    "无奈": ("daily", "无奈 摊手 无语", "无奈"),
    "拍照": ("daily", "拍照 相机 咔嚓", "拍照"),
    "转头": ("daily", "转头 回头", "转头"),
    "转头看": ("daily", "转头 回头 看", "转头看"),
    "吃": ("daily", "吃 吃瓜 干饭", "在吃"),
    "啃": ("daily", "啃 吃 干饭", "在啃"),
    "戳": ("daily", "戳 点 碰", "伸手戳"),
    "喝": ("daily", "喝 奶茶 茶", "在喝"),
    "茶": ("daily", "喝茶 悠闲", "喝茶"),
    "伸手": ("daily", "伸手 手 摸", "伸手"),
    "举": ("daily", "举手 举 我我我", "举手"),
    "摸": ("daily", "摸 手", "伸手摸"),
    "冒出": ("daily", "冒出 探头 出现", "冒出来"),
    "摘眼镜": ("daily", "摘眼镜 眼镜", "摘眼镜"),
    "咀嚼": ("daily", "咀嚼 吃 嚼", "嚼东西"),
    "张嘴": ("daily", "张嘴 说话", "张嘴"),
    "打伞": ("daily", "打伞 下雨 撑伞", "打伞"),
    "耳朵": ("daily", "耳朵 晃耳朵 耳", "晃耳朵"),
    "排饼": ("daily", "排饼 摆盘", "摆一排饼"),
    "风景": ("daily", "风景 好看 景色", "好看背景"),
    "奶茶": ("daily", "奶茶 喝 饮料", "喝奶茶"),
    "甜甜圈": ("daily", "甜甜圈 甜点", "吃甜甜圈"),
    "指指点点": ("daily", "指指点点 指 点名", "指着人"),
    "摆手": ("daily", "摆手 打招呼 嗨", "摆手打招呼"),
    "靠近": ("daily", "靠近 凑近 贴", "凑近"),
    "叼": ("daily", "叼 叼着 烟", "叼着东西"),
    "色盘": ("daily", "色盘 眼镜 调色", "戴彩色眼镜"),
    "困": ("daily", "困 瞌睡 睡", "困了"),
    "哈欠": ("daily", "打哈欠 困 睡", "打哈欠"),
    "睡": ("daily", "睡 困 晚安", "睡了"),
    "骑车": ("daily", "骑车 单车 出门", "骑车"),
    "玩水": ("daily", "玩水 水 湿", "玩水"),
    "玩电脑": ("daily", "玩电脑 电脑 上网", "玩电脑"),
    "洗脚": ("daily", "洗脚 脚", "洗脚"),
    "拿鱼": ("daily", "拿鱼 打人 拍", "拿鱼拍人"),
    "魔法棒": ("daily", "魔法棒 魔法", "挥魔法棒"),
    "酒": ("daily", "酒 喝 醉", "酒"),
    # 只做检索词，不投票
    "看": ("", "看 围着看", ""),
    "盯": ("", "盯 看着", ""),
    "凝视": ("", "凝视 盯着", ""),
    "偷看": ("", "偷看 瞄 探头", ""),
    "瞅": ("", "瞅 瞥一眼", ""),
    "咪": ("", "咪 猫", ""),
}

TIE_PRIORITY = ["sad", "angry", "shy", "happy", "confused", "daily"]

# 作者标注为已删除/测试/署名 的图直接跳过
SKIP_NAMES = ("已删除", "测试面捕", "测试", "原图作者", "原图来源")

DEFAULT_MOOD = "daily"


def http_json(url: str, retries: int = 8) -> dict:
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            print(f"  网络错误 {exc!r}，重试")
            time.sleep(10 + attempt * 10)
            continue
        if data.get("code") == 0:
            return data["data"]
        print(f"  code={data.get('code')}，退避重试")
        time.sleep(15 + attempt * 15)
    raise RuntimeError(f"请求失败：{url}")


def http_bytes(url: str, retries: int = 5) -> bytes | None:
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except Exception as exc:  # noqa: BLE001 - 单张图下载失败就跳过，别中断整批
            print(f"  图片下载失败 {url} {exc!r}")
            time.sleep(3 + attempt * 5)
    return None


def article_index(title: str) -> int | None:
    """从标题里抠出第几期：kipfel猫猫表情包（3） -> 3。"""
    m = re.search(r"[（(](\d+)[）)]", title)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)$", title)
    return int(m.group(1)) if m else None


def cached_article(cache: Path, idx: int, aid: int) -> dict:
    path = cache / f"{idx:02d}-{aid}.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    data = http_json(ARTICLE_URL.format(aid))
    cache.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def pics_of(data: dict) -> list[dict]:
    """按正文顺序取出 (url, 作者标注的名字)。"""
    out: list[dict] = []
    opus = ((data.get("opus") or {}).get("content") or {}).get("paragraphs") or []
    for para in opus:
        if para.get("para_type") != 2:
            continue
        for pic in (para.get("pic") or {}).get("pics") or []:
            url = str(pic.get("url") or "")
            if url:
                out.append(
                    {
                        "url": url.replace("http://", "https://"),
                        "name": str(pic.get("comment") or ""),
                    }
                )
    if not out:
        # 老模板没有 opus 段落，只能退化成不带名字的顺序图
        for url in data.get("content_pic_list") or []:
            out.append({"url": str(url), "name": ""})
    return out


_BAD_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]+')
_EDGE = " \t-·,，。.!！?？~～\"'“”‘’；;、"
_BRACKETS = {"（": "）", "(": ")", "【": "】", "《": "》", "[": "]"}


def clean_name(raw: str, fallback: str) -> str:
    """清掉文件名非法字符和首尾标点；成对的括号整层去掉，但不拆散「吃瓜（比耶）」。"""
    text = _BAD_CHARS.sub("-", str(raw or "").strip())
    text = re.sub(r"\s+", " ", text).strip(_EDGE)
    while len(text) > 2:
        pair = _BRACKETS.get(text[0])
        if not pair or text[-1] != pair:
            break
        text = text[1:-1].strip(_EDGE)
    return (text or fallback)[:40]


def base_name(stem: str) -> str:
    """猫猫摇·2 -> 猫猫摇，情绪按基名判定。"""
    return re.sub(rf"\{VARIANT_SEP}\d+$", "", stem)


def classify(name: str) -> tuple[str, str, str]:
    """按名字里的词判情绪桶，并拼一句画面描述和检索词。"""
    votes: dict[str, int] = {}
    keywords = [name]
    descs: list[str] = []
    for token, (mood, words, desc) in LEXICON.items():
        if token not in name:
            continue
        if mood:
            votes[mood] = votes.get(mood, 0) + 1
        keywords.extend(words.split())
        if desc and desc not in descs:
            descs.append(desc)
    if not votes:
        mood = DEFAULT_MOOD
    else:
        mood = max(
            votes.items(),
            key=lambda kv: (kv[1], -TIE_PRIORITY.index(kv[0])),
        )[0]
    caption = "，".join(descs) if descs else name
    return mood, caption, " ".join(dict.fromkeys(keywords))


def ext_of(data: bytes, url: str) -> str | None:
    head = data[:16]
    if head.startswith(b"\xff\xd8"):
        return ".jpg"
    if head.startswith(b"\x89PNG"):
        return ".png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if head[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
    return suffix if suffix in IMAGE_EXTS else None


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _frames_to_gif(im, side: int, colors: int, Image) -> bytes | None:
    """先把所有帧读进内存再编码，避免边读边写把源文件写坏。

    长动图（几百帧）光缩尺寸还是几百 KB 往上，按等间隔抽帧压到 48 帧以内。
    """
    total = int(im.n_frames)
    step = max(1, total // MAX_GIF_FRAMES)
    base_duration = int(im.info.get("duration") or 100)
    frames = []
    for i in range(0, total, step):
        im.seek(i)
        frame = im.convert("RGBA")
        frame.thumbnail((side, side), Image.LANCZOS)
        frames.append(frame.quantize(colors=colors, method=Image.Quantize.FASTOCTREE))
    if not frames:
        return None
    buf = io.BytesIO()
    frames[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=[base_duration * step] * len(frames),
        loop=int(im.info.get("loop") or 0),
        optimize=True,
        disposal=2,
    )
    return buf.getvalue()


def shrink(path: Path) -> None:
    """缩到聊天够用的尺寸。先编码到内存，关掉源文件后再替换。

    Windows 不允许替换还被打开的文件，所以写入必须发生在 ``with`` 之外。
    """
    try:
        from PIL import Image
    except ImportError:
        return
    try:
        data = _shrunk_bytes(path, Image)
    except Exception as exc:  # noqa: BLE001 - 一张图压不动就原样留着
        print(f"  压缩失败 {path.name}: {exc!r}")
        return
    if data:
        _write_atomic(path, data)


def _shrunk_bytes(path: Path, Image) -> bytes | None:
    size = path.stat().st_size
    with Image.open(path) as im:
        long_side = max(im.size)
        if int(getattr(im, "n_frames", 1) or 1) > 1:
            if size <= GIF_PASSES[0][2] and long_side <= GIF_PASSES[0][0]:
                return None
            for side, colors, budget in GIF_PASSES:
                data = _frames_to_gif(im, side, colors, Image)
                if data is None:
                    return None
                if len(data) <= budget or side == GIF_PASSES[-1][0]:
                    return data
            return None
        has_alpha = im.mode in ("RGBA", "LA") or "transparency" in im.info
        if size <= MAX_STILL_BYTES and long_side <= MAX_SIDE:
            return None
        im = im.convert("RGBA" if has_alpha else "RGB")
        im.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
        buf = io.BytesIO()
        if path.suffix.lower() == ".webp":
            im.save(buf, format="WEBP", quality=82)
        elif has_alpha:
            im.quantize(colors=192, method=Image.Quantize.FASTOCTREE).save(
                buf, format="PNG", optimize=True
            )
        else:
            im.save(buf, format="JPEG", quality=82, optimize=True)
        return buf.getvalue()


def load_sources(lib_dir: Path) -> dict[str, str]:
    path = lib_dir / "_sources.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def save_sources(lib_dir: Path, sources: dict[str, str]) -> None:
    (lib_dir / "_sources.json").write_text(
        json.dumps(sources, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def rebuild_meta(lib_dir: Path, pack: dict) -> int:
    """按磁盘上的文件名重算 pack.json，并给冲突后缀起可读的变体名。"""
    stems = {
        f.stem
        for f in lib_dir.iterdir()
        if f.is_file() and f.suffix.lower() in IMAGE_EXTS
    }
    for stem in sorted(stems):
        m = re.match(r"^(.*)-([0-9a-f]{6})$", stem)
        if not m:
            continue
        base = m.group(1)
        n = 2
        while f"{base}{VARIANT_SEP}{n}" in stems:
            n += 1
        for ext in IMAGE_EXTS:
            old = lib_dir / f"{stem}{ext}"
            if old.is_file():
                old.rename(lib_dir / f"{base}{VARIANT_SEP}{n}{ext}")
                break
        stems.discard(stem)
        stems.add(f"{base}{VARIANT_SEP}{n}")
    images = {}
    for stem in sorted(stems):
        base = base_name(stem)
        mood, caption, keywords = classify(base)
        images[stem] = {"mood": mood, "caption": caption, "keywords": keywords}
    pack["images"] = images
    (lib_dir / "pack.json").write_text(
        json.dumps(pack, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return len(images)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--readlist", type=int, default=1063625)
    ap.add_argument("--from", dest="start", type=int, default=1)
    ap.add_argument("--to", dest="end", type=int, default=20)
    ap.add_argument("--library", default="kipfel")
    ap.add_argument("--root", default="data/fakeai/stickers")
    ap.add_argument("--cache", default="temp/kipfel/raw")
    ap.add_argument("--pack-name", default="kipfel 猫猫表情包")
    ap.add_argument("--recompress", action="store_true", help="只压缩已有图片，不联网")
    ap.add_argument(
        "--rebuild-meta", action="store_true", help="只重算 pack.json，不联网"
    )
    args = ap.parse_args()

    lib_dir = Path(args.root) / args.library
    lib_dir.mkdir(parents=True, exist_ok=True)
    pack_path = lib_dir / "pack.json"
    pack: dict = {
        "id": args.library,
        "name": args.pack_name,
        "version": "1.0.0",
        "license": "fan-collected-noncommercial",
        "source": f"https://www.bilibili.com/read/readlist/rl{args.readlist}",
        "note": "图片由文集作者标注命名，原作模型 kipfel / キプフェル by かめ山（Mochiyama Kingyo）",
        "images": {},
    }
    if pack_path.is_file():
        pack.update(json.loads(pack_path.read_text(encoding="utf-8")))

    if args.recompress:
        changed = 0
        for f in sorted(lib_dir.iterdir()):
            if f.suffix.lower() not in IMAGE_EXTS:
                continue
            before = f.stat().st_size
            shrink(f)
            if f.stat().st_size != before:
                changed += 1
        print(f"压缩完成，{changed} 张体积有变化")
        return

    if args.rebuild_meta:
        count = rebuild_meta(lib_dir, pack)
        print(f"pack.json 已重建，{count} 个贴纸名")
        return

    listing = http_json(LIST_URL.format(args.readlist))
    articles = []
    for art in listing.get("articles") or []:
        idx = article_index(str(art.get("title") or ""))
        if idx is not None and args.start <= idx <= args.end:
            articles.append((idx, int(art["id"]), str(art.get("title") or "")))
    articles.sort()
    print(f"待处理 {len(articles)} 篇")

    sources = load_sources(lib_dir)
    added = skipped = unnamed = 0
    cache = Path(args.cache)
    for idx, aid, title in articles:
        data = cached_article(cache, idx, aid)
        pics = pics_of(data)
        print(f"[{idx:02d}] {title} 共 {len(pics)} 张")
        for n, pic in enumerate(pics, 1):
            if not pic["name"].strip():
                unnamed += 1
                continue
            name = clean_name(pic["name"], f"{args.library}-{idx}-{n}")
            if name in SKIP_NAMES:
                skipped += 1
                continue
            raw = http_bytes(pic["url"])
            if not raw:
                skipped += 1
                continue
            digest = hashlib.md5(raw).hexdigest()
            if digest in sources and (lib_dir / sources[digest]).is_file():
                skipped += 1
                continue
            ext = ext_of(raw, pic["url"])
            if ext is None:
                skipped += 1
                continue
            target = lib_dir / f"{name}{ext}"
            if target.exists():
                variant = 2
                while (lib_dir / f"{name}{VARIANT_SEP}{variant}{ext}").exists():
                    variant += 1
                target = lib_dir / f"{name}{VARIANT_SEP}{variant}{ext}"
            target.write_bytes(raw)
            shrink(target)
            sources[digest] = target.name
            added += 1
            time.sleep(0.4)
        time.sleep(1)
        save_sources(lib_dir, sources)

    save_sources(lib_dir, sources)
    count = rebuild_meta(lib_dir, pack)
    print(f"新增 {added} 张，跳过 {skipped}，无名字 {unnamed}，图库共 {count} 个贴纸名")


if __name__ == "__main__":
    main()
