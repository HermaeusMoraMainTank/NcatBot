from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

PALETTE = {
    "bg": (241, 246, 244),
    "header": (27, 63, 67),
    "header_muted": (184, 219, 210),
    "text": (35, 55, 58),
    "muted": (103, 124, 124),
    "card": (255, 255, 253),
    "line": (218, 230, 226),
    "green": (45, 139, 109),
    "orange": (220, 139, 58),
    "red": (190, 89, 79),
}


def _font(size: int, bold: bool = False, font_path: str = "") -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = []
    if font_path:
        candidates.append(Path(font_path))
    candidates.extend(
        Path(path)
        for path in (
            "C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/simhei.ttf" if bold else "C:/Windows/Fonts/simsun.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        )
    )
    for candidate in candidates:
        if candidate.exists():
            try:
                return ImageFont.truetype(str(candidate), size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: Any, width: int) -> list[str]:
    lines: list[str] = []
    for source in str(text or "").splitlines() or [""]:
        current = ""
        for char in source:
            candidate = current + char
            if current and draw.textlength(candidate, font=font) > width:
                lines.append(current)
                current = char
            else:
                current = candidate
        lines.append(current)
    return lines


def _fit(draw: ImageDraw.ImageDraw, text: str, font: Any, width: int) -> str:
    value = str(text or "")
    if draw.textlength(value, font=font) <= width:
        return value
    while value and draw.textlength(value + "…", font=font) > width:
        value = value[:-1]
    return value + "…"


def _card(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], fill=PALETTE["card"]) -> None:
    draw.rounded_rectangle(box, radius=18, fill=fill, outline=PALETTE["line"], width=2)


def render_card(
    *,
    title: str,
    subtitle: str,
    stats: list[tuple[str, str, tuple[int, int, int]]],
    sections: list[tuple[str, list[str]]],
    output_path: Path,
    font_path: str = "",
    footer: str = "估算值，仅用于健康记录，不替代医疗建议",
) -> Path:
    width = 1240
    margin = 48
    title_font = _font(48, True, font_path)
    subtitle_font = _font(22, False, font_path)
    section_font = _font(27, True, font_path)
    body_font = _font(22, False, font_path)
    small_font = _font(18, False, font_path)
    value_font = _font(31, True, font_path)
    line_height = 36
    stat_height = 118
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    section_heights = []
    for _, lines in sections:
        wrapped = sum(len(_wrap(measure, line, body_font, width - margin * 2 - 52)) for line in lines)
        section_heights.append(70 + max(1, wrapped) * line_height + 24)
    height = 214 + stat_height + sum(section_heights) + max(0, len(sections) - 1) * 16 + 80
    image = Image.new("RGB", (width, height), PALETTE["bg"])
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, width, 184), radius=28, fill=PALETTE["header"])
    draw.rectangle((0, 150, width, 184), fill=PALETTE["header"])
    draw.text((margin, 34), title, font=title_font, fill=(247, 252, 249))
    draw.text((margin, 108), subtitle, font=subtitle_font, fill=PALETTE["header_muted"])
    draw.text((width - margin, 112), time.strftime("%Y-%m-%d %H:%M"), font=small_font, fill=PALETTE["header_muted"], anchor="ra")

    stat_y = 214
    stat_gap = 18
    stat_width = (width - margin * 2 - stat_gap * (len(stats) - 1)) // max(1, len(stats))
    for index, (label, value, accent) in enumerate(stats):
        x = margin + index * (stat_width + stat_gap)
        _card(draw, (x, stat_y, x + stat_width, stat_y + stat_height))
        draw.rounded_rectangle((x, stat_y, x + 8, stat_y + stat_height), radius=4, fill=accent)
        draw.text((x + 26, stat_y + 20), label, font=small_font, fill=PALETTE["muted"])
        draw.text((x + 26, stat_y + 55), _fit(draw, value, value_font, stat_width - 48), font=value_font, fill=PALETTE["text"])

    y = stat_y + stat_height + 24
    for (heading, lines), section_height in zip(sections, section_heights):
        _card(draw, (margin, y, width - margin, y + section_height))
        draw.text((margin + 26, y + 18), heading, font=section_font, fill=PALETTE["header"])
        body_y = y + 68
        for line in lines:
            for wrapped in _wrap(draw, line, body_font, width - margin * 2 - 52):
                draw.text((margin + 26, body_y), wrapped, font=body_font, fill=PALETTE["text"])
                body_y += line_height
        y += section_height + 16
    draw.text((margin, height - 36), footer, font=small_font, fill=PALETTE["muted"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, "PNG")
    return output_path


def _num(value: Any, digits: int = 0) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "0"
    return f"{number:.{digits}f}" if digits else f"{number:,.0f}"


def _item_line(item: dict[str, Any]) -> str:
    quantity = _num(item.get("quantity"), 1).rstrip("0").rstrip(".")
    return (
        f"{item.get('name', '未命名')} {quantity}{item.get('unit', '份')}  ·  "
        f"{_num(item.get('calories_kcal'))} kcal  ·  "
        f"蛋白质 {_num(item.get('protein_g'), 1)}g  脂肪 {_num(item.get('fat_g'), 1)}g  "
        f"碳水 {_num(item.get('carbs_g'), 1)}g"
    )


def render_notice(title: str, lines: list[str], output_path: Path, font_path: str = "") -> Path:
    return render_card(
        title=title,
        subtitle="健康饮食记录",
        stats=[("状态", "需要处理", PALETTE["orange"])],
        sections=[("说明", lines)],
        output_path=output_path,
        font_path=font_path,
    )


def render_help(output_path: Path, font_path: str = "") -> Path:
    sections = [
        ("档案与记录", [
            "健康档案 你的身高、体重、年龄、性别、职业和日常活动（仅私聊）",
            "健康记录 早餐吃了两个鸡蛋和一个包子",
            "健康记录 昨天 早餐吃了两个鸡蛋",
            "健康撤销 / 健康修改 新描述",
        ]),
        ("评估与面板", ["健康评估 或 健康评估 今天", "健康评估 昨天", "健康面板"]),
        ("管理员管理", ["健康封禁 QQ号 原因，或健康封禁 @成员 原因", "健康解禁 QQ号，或健康解禁 @成员", "仅管理员 273421673 可执行；异常数据也会自动封禁成员。"]),
        ("显示规则", [
            "群聊显示实际食物、热量和营养素；目标热量与目标比较显示为 ****。",
            "健康档案必须在私聊设置，所有结果以图片卡片发送。",
            "检测到明显异常数据的用户将被永久停用本功能。",
        ]),
    ]
    return render_card(
        title="健康饮食 AI",
        subtitle="记录每一餐，看见长期趋势",
        stats=[("记录方式", "逐项入账", PALETTE["green"]), ("查询范围", "今日 / 7天", PALETTE["orange"])],
        sections=sections,
        output_path=output_path,
        font_path=font_path,
    )


def render_profile(profile: dict[str, Any], nickname: str, output_path: Path, font_path: str = "", pending: bool = True) -> Path:
    status = "等待确认" if pending else "已保存"
    sections = [
        ("档案信息", [
            f"称呼：{profile.get('display_name') or nickname}",
            f"性别：{profile.get('sex')}  ·  年龄：{_num(profile.get('age'))} 岁",
            f"身高：{_num(profile.get('height_cm'), 1)} cm  ·  体重：{_num(profile.get('weight_kg'), 1)} kg",
            f"职业：{profile.get('occupation') or '未填写'}",
            f"日常活动：{profile.get('activity_description') or profile.get('activity_level')}",
            f"通勤：{_num(profile.get('commute_km'), 1)} km/天  ·  目标：{profile.get('goal') or '维持'}",
        ]),
        ("能量估算", [
            f"基础代谢 BMR：{_num(profile.get('bmr_kcal'))} kcal/天",
            f"日常消耗 TDEE：{_num(profile.get('tdee_kcal'))} kcal/天",
            f"目标摄入：{_num(profile.get('target_kcal'))} kcal/天",
            "以上为估算值，请回复“确认档案”保存，或回复“重新填写”取消。" if pending else "档案已保存，可随时在私聊更新。",
        ]),
    ]
    return render_card(
        title="健康档案",
        subtitle=f"{status} · 私聊可见完整信息",
        stats=[("BMR", f"{_num(profile.get('bmr_kcal'))} kcal", PALETTE["green"]), ("TDEE", f"{_num(profile.get('tdee_kcal'))} kcal", PALETTE["orange"]), ("目标", f"{_num(profile.get('target_kcal'))} kcal", PALETTE["red"])],
        sections=sections,
        output_path=output_path,
        font_path=font_path,
    )


def render_meal(*, nickname: str, sex: str, meal: dict[str, Any], daily: dict[str, Any], profile: dict[str, Any] | None, group: bool, output_path: Path, font_path: str = "", daily_label: str = "今日累计") -> Path:
    meal_total = meal["totals"]
    target = _num((profile or {}).get("target_kcal")) if not group and profile else "****"
    sections = [
        ("本次逐项入账", [_item_line(item) for item in meal["items"]]),
        ("本次营养合计", [
            f"热量：{_num(meal_total['calories_kcal'])} kcal",
            f"蛋白质：{_num(meal_total['protein_g'], 1)} g  ·  脂肪：{_num(meal_total['fat_g'], 1)} g  ·  碳水：{_num(meal_total['carbs_g'], 1)} g",
        ]),
        (daily_label, [
            f"实际摄入：{_num(daily['calories_kcal'])} / {target} kcal",
            f"蛋白质：{_num(daily['protein_g'], 1)} g  ·  脂肪：{_num(daily['fat_g'], 1)} g  ·  碳水：{_num(daily['carbs_g'], 1)} g",
        ]),
    ]
    return render_card(
        title="饮食已记录",
        subtitle=f"{nickname}（{sex or '未设置性别'}） · {meal.get('meal_type', 'other')}",
        stats=[("本次热量", f"{_num(meal_total['calories_kcal'])} kcal", PALETTE["green"]), (daily_label, f"{_num(daily['calories_kcal'])} / {target}", PALETTE["orange"])],
        sections=sections,
        output_path=output_path,
        font_path=font_path,
    )


def render_assessment(*, nickname: str, sex: str, local_date: str, daily: dict[str, Any], profile: dict[str, Any] | None, macro_lines: list[str], weekly: dict[str, Any], group: bool, output_path: Path, font_path: str = "", analysis_lines: list[str] | None = None, current_progress: dict[str, Any] | None = None) -> Path:
    has_target = bool(profile and profile.get("target_kcal"))
    target = _num(profile.get("target_kcal")) if has_target and not group else "****"
    if current_progress and not group and has_target:
        energy_lines = [
            f"截至当前阶段参考摄入：{_num(current_progress.get('stage_target_kcal'))} / {_num(profile.get('target_kcal'))} kcal",
            str(current_progress.get("status") or ""),
        ]
    elif profile and not group:
        diff = float(profile.get("tdee_kcal") or 0) - float(daily.get("calories_kcal") or 0)
        energy_lines = [f"估算消耗：{_num(profile.get('tdee_kcal'))} kcal  ·  热量差：{_num(diff)} kcal", "当前处于估算缺口" if diff > 0 else "当前处于估算盈余" if diff < 0 else "当前接近估算平衡"]
    else:
        energy_lines = ["目标摄入与热量差：****"]
    weekly_lines = [
        f"已记录 {weekly['recorded_days']}/7 天  ·  实际平均摄入 {_num(weekly['average_calories'])} kcal/天",
        f"近 7 天实际摄入合计：{_num(weekly['total_calories'])} kcal",
    ]
    if weekly.get("weekly_jin") is not None and not group:
        weekly_lines.append(f"理论趋势：每周约 {weekly['weekly_jin']:g} 斤（不是实际体重承诺）")
    elif group:
        weekly_lines.append("目标比较与减重趋势：****")
    sections = [
        ("实际摄入", [f"{local_date}：{_num(daily['calories_kcal'])} / {target} kcal", *energy_lines]),
        ("实际营养素", [f"蛋白质：{_num(daily['protein_g'], 1)} g  ·  脂肪：{_num(daily['fat_g'], 1)} g  ·  碳水：{_num(daily['carbs_g'], 1)} g", *macro_lines]),
        ("近 7 天", weekly_lines),
        ("AI 详细分析与建议", analysis_lines or ["暂无 AI 分析结果"]),
    ]
    return render_card(
        title="饮食评估",
        subtitle=f"{nickname}（{sex or '未设置性别'}） · {local_date}",
        stats=[("实际摄入", f"{_num(daily['calories_kcal'])} kcal", PALETTE["green"]), ("目标", target, PALETTE["orange"]), ("记录天数", f"{weekly['recorded_days']}/7", PALETTE["red"])],
        sections=sections,
        output_path=output_path,
        font_path=font_path,
    )


def render_dashboard(*, nickname: str, profile: dict[str, Any] | None, daily: dict[str, Any], weekly: dict[str, Any], foods: list[str], macro_line: str, group: bool, output_path: Path, font_path: str = "") -> Path:
    target = _num((profile or {}).get("target_kcal")) if profile and not group else "****"
    sections = [
        ("今日", [f"实际摄入：{_num(daily['calories_kcal'])} / {target} kcal", f"蛋白质：{_num(daily['protein_g'], 1)} g  ·  脂肪：{_num(daily['fat_g'], 1)} g  ·  碳水：{_num(daily['carbs_g'], 1)} g"]),
        ("近 7 天实际汇总", [f"记录天数：{weekly['recorded_days']}/7  ·  实际摄入合计：{_num(weekly['total_calories'])} kcal", f"实际平均摄入：{_num(weekly['average_calories'])} kcal/天", macro_line, "目标摄入、目标营养范围和热量差：****" if group else f"目标摄入：{target} kcal/天"]),
        ("近 7 天食物", foods or ["暂无饮食记录"]),
    ]
    if group:
        profile_line = f"称呼：{nickname}  ·  性别：{(profile or {}).get('sex') or '未设置'}\n身高、体重、年龄、职业和每日消耗：****"
    elif profile:
        profile_line = f"称呼：{profile.get('display_name') or nickname}  ·  性别：{profile.get('sex')}  ·  年龄：{_num(profile.get('age'))} 岁\n身高：{_num(profile.get('height_cm'), 1)} cm  ·  体重：{_num(profile.get('weight_kg'), 1)} kg\n职业：{profile.get('occupation') or '未填写'}  ·  每日消耗：{_num(profile.get('tdee_kcal'))} kcal"
    else:
        profile_line = "尚未建立健康档案，目标相关评估暂不可用。"
    sections.insert(0, ("个人档案", profile_line.splitlines()))
    return render_card(
        title="个人健康面板",
        subtitle=f"{nickname} · 图片版数据面板",
        stats=[("今日实际", f"{_num(daily['calories_kcal'])} / {target}", PALETTE["green"]), ("7天记录", f"{weekly['recorded_days']}/7 天", PALETTE["orange"]), ("实际平均", f"{_num(weekly['average_calories'])} kcal", PALETTE["red"])],
        sections=sections,
        output_path=output_path,
        font_path=font_path,
    )
