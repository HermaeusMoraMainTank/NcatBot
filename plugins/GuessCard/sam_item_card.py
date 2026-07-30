"""FF14 武士刀灰机 wiki 风格物品卡渲染。"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

from PIL import Image as PILImage
from PIL import ImageDraw, ImageFont

RARITY_CN = {1: "普通", 2: "稀有", 3: "上品", 4: "极品", 7: "绝品"}
# wiki / 游戏常见品名色
RARITY_COLOR = {
    1: (255, 255, 255),
    2: (192, 255, 192),
    3: (89, 255, 255),
    4: (255, 128, 255),
    7: (255, 176, 64),
}

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def auto_attack(phys: int, delay_ms: int) -> float:
    """物理自动攻击 = 物理基本性能 × (攻击间隔秒 / 3)。"""
    if phys <= 0 or delay_ms <= 0:
        return 0.0
    return round(phys * (delay_ms / 1000.0) / 3.0, 2)


def fetch_item_json_curl(item_id: int, timeout: int = 45) -> Optional[dict]:
    page = f"Data:Item/{int(item_id)}.json"
    url = (
        "https://ff14.huijiwiki.com/api.php?action=parse"
        f"&page={quote(page)}&prop=wikitext&format=json"
    )
    cmd = [
        "curl",
        "-sL",
        "--fail",
        "-A",
        UA,
        "-e",
        "https://ff14.huijiwiki.com/",
        "--connect-timeout",
        "15",
        "--max-time",
        str(timeout),
        url,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0 or not (r.stdout or "").strip():
        return None
    try:
        payload = json.loads(r.stdout)
        wt = payload["parse"]["wikitext"]["*"].strip()
        return json.loads(wt)
    except (KeyError, json.JSONDecodeError, TypeError):
        return None


def normalize_wiki_item(raw: dict, fallback: Optional[dict] = None) -> dict[str, Any]:
    """把 Data:Item JSON 压成渲染用结构。"""
    fb = fallback or {}
    attrs_block = raw.get("属性") or {}
    base = attrs_block.get("基本性能") or {}
    special = attrs_block.get("属性") or {}
    order = attrs_block.get("属性顺序") or list(special.keys())

    phys = int(base.get("物理基本性能") or raw.get("物理性能") or fb.get("damagePhys") or 0)
    mag = int(base.get("魔法基本性能") or raw.get("魔法性能") or 0)
    delay_ms = int(raw.get("攻击间隔") or 2640)
    delay_s = round(delay_ms / 1000.0, 2)
    ilvl = int(raw.get("品级") or fb.get("itemLevel") or 0)
    elvl = int(raw.get("装备等级") or fb.get("equipLevel") or 0)
    rarity = int(raw.get("品质") or fb.get("rarity") or 1)
    job = (raw.get("可使用职业显示") or "武士").strip()
    name = (raw.get("中文名") or fb.get("fullNameChinese") or fb.get("name") or "").strip()
    itype = (raw.get("类型") or "武士刀").strip()

    stats: list[tuple[str, int]] = []
    for key in order:
        val = special.get(key)
        if val is None:
            continue
        try:
            stats.append((str(key), int(val)))
        except (TypeError, ValueError):
            continue
    if not stats:
        # fallback from flat fields
        mapping = {1: "力量", 3: "耐力", 27: "暴击", 44: "信念", 22: "直击", 45: "技能速度"}
        for i in range(1, 7):
            t = int(raw.get(f"属性类型{i}") or 0)
            v = int(raw.get(f"属性数值{i}") or 0)
            if t and v and t in mapping:
                stats.append((mapping[t], v))

    # 灰机模板：修理等级 ≈ 装备等级-10；镶嵌多为能工巧匠同装等
    repair_lvl = max(1, elvl - 10) if elvl else 0
    meld_lvl = elvl
    dye_n = int(raw.get("染色") or 0)
    dye_label = {0: "", 1: "单染色", 2: "双染色"}.get(dye_n, f"{dye_n}染色")

    flags: list[str] = []
    if raw.get("独占"):
        flags.append("只能持有一个")
    if raw.get("珍稀"):
        flags.append("不可交易")
    if raw.get("可禁忌镶嵌") is False:
        flags.append("无法禁忌镶嵌")
    # 市场：珍稀/独占通常不可卖
    if raw.get("珍稀") or raw.get("独占"):
        flags.append("不可在市场出售")

    return {
        "name": name,
        "type": itype,
        "phys": phys,
        "mag": mag,
        "auto_attack": auto_attack(phys, delay_ms),
        "delay": delay_s,
        "ilvl": ilvl,
        "elvl": elvl,
        "rarity": rarity,
        "rarity_label": RARITY_CN.get(rarity, f"品质{rarity}"),
        "job": job,
        "stats": stats,
        "sell": int(raw.get("出售价格") or 0),
        "repair_job": (raw.get("修理职业") or "锻铁匠").strip(),
        "repair_level": repair_lvl,
        "repair_mat": (raw.get("消耗素材") or "").strip(),
        "meld_job": "能工巧匠",
        "meld_level": meld_lvl,
        "materia_slots": int(raw.get("嵌孔数") or 0),
        "advanced_melding": bool(raw.get("可禁忌镶嵌")),
        "desynth": bool(raw.get("可分解")),
        "desynth_skill": float(ilvl) if raw.get("可分解") and ilvl else 0.0,
        "glamour": bool(raw.get("武具投影")),
        "glamour_dresser": bool(raw.get("投影台")),
        "collectable": bool(raw.get("可收藏品")),
        "advanced_materia": bool(raw.get("精制魔晶石")),
        "dye": dye_n,
        "dye_label": dye_label,
        "crest": bool(raw.get("部队徽记")),
        "flags": flags,
        "icon_hash": (raw.get("图标") or "").strip(),
        "en": (raw.get("英文名") or "").strip(),
        "jp": (raw.get("日文名") or "").strip(),
        "patch": raw.get("版本"),
    }


def format_wiki_text(info: dict) -> str:
    """接近灰机物品卡的纯文本（字段顺序对齐 wiki 浮层）。"""
    lines = [
        info["name"],
        info["type"],
        "物理基本性能",
        str(info["phys"]),
        "物理自动攻击",
        f"{info['auto_attack']:.2f}",
        "攻击间隔",
        f"{info['delay']:.2f}",
        f"品级 {info['ilvl']}",
        info["job"],
        f"{info['elvl']}级或更高",
        "特殊",
    ]
    # wiki 特殊段常把属性挤在一行
    if info.get("stats"):
        lines.append("".join(f"{k} +{v}" for k, v in info["stats"]))
    lines.append("制作&修理")
    repair = f"修理等级{info['repair_job']}"
    if info.get("repair_level"):
        repair += f" {info['repair_level']}级或更高"
    if info.get("repair_mat"):
        repair += f"修理材料{info['repair_mat']}"
    if info.get("meld_level"):
        repair += f"镶嵌魔晶石等级{info.get('meld_job') or '能工巧匠'} {info['meld_level']}级或更高"
    lines.append(repair)

    meta_parts: list[str] = []
    if info.get("advanced_materia"):
        meta_parts.append("精制魔晶石：")
    if info.get("glamour"):
        meta_parts.append("武具投影：")
    if info.get("desynth") and info.get("desynth_skill"):
        meta_parts.append(f"建议分解技能：{info['desynth_skill']:.2f}")
    elif info.get("desynth"):
        meta_parts.append("建议分解：")
    if info.get("dye_label"):
        meta_parts.append(f"染色：{info['dye_label']}")
    if info.get("crest"):
        meta_parts.append("部队徽记：")
    if info.get("glamour_dresser"):
        meta_parts.append("投影台：")
    if info.get("collectable"):
        meta_parts.append("收藏柜：")
    if meta_parts:
        lines.append("".join(meta_parts))
    if info.get("sell"):
        lines.append(f"出售价格：{info['sell']} 金币")
    if info.get("flags"):
        lines.append("")
        lines.append("".join(info["flags"]))
    return "\n".join(lines)


def _font(path: Optional[Path], size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if path and path.exists():
        try:
            return ImageFont.truetype(str(path), size)
        except OSError:
            pass
    return ImageFont.load_default()


def _text_width(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    box = draw.textbbox((0, 0), text, font=font)
    return int(box[2] - box[0])


def render_wiki_item_card(
    info: dict,
    icon: Optional[PILImage.Image],
    *,
    font_path: Optional[Path] = None,
    out_path: Path,
) -> Path:
    """绘制接近灰机/游戏物品浮层风格的信息卡。"""
    W = 520
    pad = 16
    # 预估高度（制作&修理多行）
    rows = 16 + len(info.get("stats") or []) + (2 if info.get("flags") else 0)
    H = max(480, 56 + rows * 28 + 140)

    # 灰机/游戏常见深蓝紫底
    bg = PILImage.new("RGB", (W, H), (24, 28, 40))
    draw = ImageDraw.Draw(bg)
    # 外框
    border = RARITY_COLOR.get(int(info.get("rarity") or 1), (180, 180, 200))
    draw.rectangle([1, 1, W - 2, H - 2], outline=border, width=2)
    draw.rectangle([4, 4, W - 5, H - 5], outline=(60, 70, 95), width=1)

    font_title = _font(font_path, 26)
    font_body = _font(font_path, 20)
    font_small = _font(font_path, 17)
    font_label = _font(font_path, 18)

    y = pad
    # 图标
    icon_box = 72
    if icon is not None:
        ic = icon.convert("RGBA")
        ic.thumbnail((icon_box, icon_box), PILImage.Resampling.LANCZOS)
        plate = PILImage.new("RGBA", (icon_box + 8, icon_box + 8), (40, 48, 68, 255))
        px, py = pad, pad
        bg.paste(plate, (px, py))
        bg.paste(ic, (px + 4, py + 4), ic)
        text_x = pad + icon_box + 18
    else:
        text_x = pad

    name_color = RARITY_COLOR.get(int(info.get("rarity") or 1), (255, 255, 255))
    draw.text((text_x, y + 4), info["name"], fill=name_color, font=font_title)
    draw.text((text_x, y + 36), info["type"], fill=(170, 185, 210), font=font_small)
    y = pad + icon_box + 14

    def rule() -> None:
        nonlocal y
        draw.line([(pad, y), (W - pad, y)], fill=(70, 80, 110), width=1)
        y += 10

    def row(label: str, value: str, label_color=(150, 165, 190), value_color=(235, 240, 250)) -> None:
        nonlocal y
        draw.text((pad, y), label, fill=label_color, font=font_label)
        vw = _text_width(draw, value, font_body)
        draw.text((W - pad - vw, y), value, fill=value_color, font=font_body)
        y += 26

    rule()
    row("物理基本性能", str(info["phys"]))
    row("物理自动攻击", f"{info['auto_attack']:.2f}")
    row("攻击间隔", f"{info['delay']:.2f}")
    rule()
    draw.text((pad, y), f"品级 {info['ilvl']}", fill=(235, 240, 250), font=font_body)
    y += 26
    draw.text((pad, y), info["job"], fill=(235, 240, 250), font=font_body)
    y += 26
    draw.text((pad, y), f"{info['elvl']}级或更高", fill=(200, 210, 230), font=font_body)
    y += 28
    rule()
    draw.text((pad, y), "特殊", fill=(255, 210, 120), font=font_body)
    y += 26
    for k, v in info.get("stats") or []:
        row(k, f"+{v}", label_color=(200, 210, 230), value_color=(120, 220, 160))
    rule()
    draw.text((pad, y), "制作&修理", fill=(255, 210, 120), font=font_body)
    y += 26

    def wrap_line(text: str, color=(190, 200, 220)) -> None:
        nonlocal y
        max_w = W - pad * 2
        cur = ""
        for ch in text:
            trial = cur + ch
            if _text_width(draw, trial, font_small) > max_w and cur:
                draw.text((pad, y), cur, fill=color, font=font_small)
                y += 22
                cur = ch
            else:
                cur = trial
        if cur:
            draw.text((pad, y), cur, fill=color, font=font_small)
            y += 22

    repair_line = f"修理等级{info['repair_job']}"
    if info.get("repair_level"):
        repair_line += f" {info['repair_level']}级或更高"
    if info.get("repair_mat"):
        repair_line += f" 修理材料{info['repair_mat']}"
    wrap_line(repair_line)
    if info.get("meld_level"):
        wrap_line(
            f"镶嵌魔晶石等级{info.get('meld_job') or '能工巧匠'} "
            f"{info['meld_level']}级或更高"
        )
    if info.get("materia_slots"):
        wrap_line(f"魔晶石孔×{info['materia_slots']}")

    meta_bits = []
    if info.get("advanced_materia"):
        meta_bits.append("精制魔晶石")
    if info.get("glamour"):
        meta_bits.append("武具投影")
    if info.get("desynth") and info.get("desynth_skill"):
        meta_bits.append(f"建议分解技能 {info['desynth_skill']:.2f}")
    elif info.get("desynth"):
        meta_bits.append("可分解")
    if info.get("dye_label"):
        meta_bits.append(info["dye_label"])
    if info.get("crest"):
        meta_bits.append("部队徽记")
    if info.get("glamour_dresser"):
        meta_bits.append("投影台")
    if info.get("collectable"):
        meta_bits.append("收藏柜")
    if meta_bits:
        wrap_line(" · ".join(meta_bits), color=(170, 180, 200))
    if info.get("sell"):
        row("出售价格", f"{info['sell']} 金币")
    if info.get("flags"):
        rule()
        flag_text = "　".join(info["flags"])
        # wrap
        max_w = W - pad * 2
        cur = ""
        for ch in flag_text:
            trial = cur + ch
            if _text_width(draw, trial, font_small) > max_w and cur:
                draw.text((pad, y), cur, fill=(255, 140, 140), font=font_small)
                y += 22
                cur = ch
            else:
                cur = trial
        if cur:
            draw.text((pad, y), cur, fill=(255, 140, 140), font=font_small)
            y += 22

    # 裁掉底部空白
    final_h = min(H, y + pad)
    bg = bg.crop((0, 0, W, final_h))
    # 重画底边框
    draw = ImageDraw.Draw(bg)
    draw.rectangle([1, 1, W - 2, final_h - 2], outline=border, width=2)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    bg.save(out_path, "PNG", optimize=True)
    return out_path
