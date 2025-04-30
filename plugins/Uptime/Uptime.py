import os
import io
import requests
from datetime import datetime, timedelta, timezone
from PIL import Image, ImageDraw, ImageFont
from ncatbot.plugin import BasePlugin, CompatibleEnrollment
from ncatbot.core.element import MessageChain, Image as BotImage, Text
from ncatbot.core.message import GroupMessage

bot = CompatibleEnrollment

# 数据中心列表
DATA_CENTERS = [
    ("猫小胖", "猫小胖"),
    ("莫古力", "莫古力"),
    ("陆行鸟", "陆行鸟"),
    ("豆豆柴", "豆豆柴"),
]

# boss图片路径
BOSS_IMAGE_PATH = os.path.join("data", "image", "ff14", "uptime", "mie.png")

# 颜色配置
COLOR_BG = (48, 24, 48)
COLOR_CARD = (64, 40, 64)
COLOR_CARD_BORDER = (200, 80, 100)
COLOR_TEXT = (255, 255, 255)
COLOR_ERROR = (255, 80, 80)
COLOR_LABEL = (255, 120, 180)
COLOR_TIME = (120, 255, 200)
COLOR_TITLE = (255, 200, 255)

# 字体配置（可根据实际路径调整）
FONT_PATH = os.path.join("data", "font", "msyh.ttc")  # 微软雅黑
FONT_SIZE_TITLE = 36
FONT_SIZE_LABEL = 24
FONT_SIZE_NORMAL = 20
FONT_SIZE_SMALL = 16

# 卡片宽高
CARD_WIDTH = 350
CARD_HEIGHT = 440
CARD_MARGIN = 32

# 错误卡片宽高
ERROR_CARD_WIDTH = 220
ERROR_CARD_HEIGHT = 420

# 总体图片宽高
IMG_WIDTH = CARD_WIDTH * 4 + CARD_MARGIN * 5
IMG_HEIGHT = CARD_HEIGHT + CARD_MARGIN * 2

# 北京时区
BEIJING_TZ = timezone(timedelta(hours=8))


class UptimePlugin(BasePlugin):
    name = "uptime"
    version = "1.0"

    @bot.group_event()
    async def handle_uptime(self, input: GroupMessage):
        message = input.raw_message.strip()
        if not message.startswith("灭云"):
            return
        # 查询四个数据中心
        results = {}
        for dc_name, dc_param in DATA_CENTERS:
            try:
                resp = requests.get(
                    f"https://api.ff14.xin/status?data_center={dc_param}", timeout=10
                )
                if resp.status_code == 200:
                    results[dc_name] = resp.json()
                else:
                    results[dc_name] = None
            except Exception:
                results[dc_name] = None
        # 绘图
        img = self._draw_status_img(results)
        # 保存为本地文件
        img.save("uptime.png", format="PNG")
        # 发送图片
        await self.api.post_group_msg(
            group_id=input.group_id,
            rtf=MessageChain([BotImage("uptime.png")]),
            reply=input.message_id,
        )

    def _draw_status_img(self, results):
        # 加载字体
        try:
            font_title = ImageFont.truetype(FONT_PATH, FONT_SIZE_TITLE)
            font_label = ImageFont.truetype(FONT_PATH, FONT_SIZE_LABEL)
            font_normal = ImageFont.truetype(FONT_PATH, FONT_SIZE_NORMAL)
            font_small = ImageFont.truetype(FONT_PATH, FONT_SIZE_SMALL)
        except Exception:
            font_title = font_label = font_normal = font_small = None
        # 创建底图
        img = Image.new("RGB", (IMG_WIDTH, IMG_HEIGHT), COLOR_BG)
        draw = ImageDraw.Draw(img)
        # 猫小胖卡片
        x0 = CARD_MARGIN
        y0 = CARD_MARGIN
        if results.get("猫小胖") is None:
            self._draw_error_card(draw, font_label, font_normal, x0, y0, "猫小胖")
        else:
            self._draw_dc_card(
                img,
                draw,
                results.get("猫小胖"),
                x=x0,
                y=y0,
                title="猫小胖",
                font_title=font_title,
                font_label=font_label,
                font_normal=font_normal,
                font_small=font_small,
            )
        # 右侧3个卡片
        for idx, dc_name in enumerate(["莫古力", "陆行鸟", "豆豆柴"]):
            x = CARD_MARGIN + CARD_WIDTH * (idx + 1) + CARD_MARGIN * (idx + 1)
            y = CARD_MARGIN
            if results.get(dc_name) is None:
                self._draw_error_card(draw, font_label, font_normal, x, y, dc_name)
            else:
                self._draw_dc_card(
                    img,
                    draw,
                    results.get(dc_name),
                    x=x,
                    y=y,
                    title=dc_name,
                    font_title=font_title,
                    font_label=font_label,
                    font_normal=font_normal,
                    font_small=font_small,
                )
        return img

    def _draw_error_card(self, draw, font_label, font_normal, x, y, title):
        # 错误卡片
        draw.rounded_rectangle(
            [x, y, x + ERROR_CARD_WIDTH, y + ERROR_CARD_HEIGHT],
            radius=18,
            fill=COLOR_CARD,
            outline=COLOR_ERROR,
            width=4,
        )
        draw.text((x + 20, y + 20), title, fill=COLOR_LABEL, font=font_label)
        draw.text((x + 20, y + 60), "错误", fill=COLOR_ERROR, font=font_label)
        draw.text(
            (x + 20, y + 120), "获取数据失败:", fill=COLOR_ERROR, font=font_normal
        )
        draw.text(
            (x + 20, y + 160), "Failed to fetch", fill=COLOR_ERROR, font=font_normal
        )

    def _draw_dc_card(
        self,
        img,
        draw,
        data,
        x,
        y,
        title,
        font_title,
        font_label,
        font_normal,
        font_small,
    ):
        # 卡片背景
        draw.rounded_rectangle(
            [x, y, x + CARD_WIDTH, y + CARD_HEIGHT],
            radius=24,
            fill=COLOR_CARD,
            outline=COLOR_LABEL,
            width=3,
        )
        # 顶部状态标签
        status = "未开启"
        status_color = (255, 80, 80)
        if data and data.get("is_uptime"):
            status = "已开启"
            status_color = (80, 255, 120)
        bbox = draw.textbbox((0, 0), status, font=font_label)
        tag_text_w = bbox[2] - bbox[0]
        tag_text_h = bbox[3] - bbox[1]
        tag_pad_x = 18
        tag_pad_y = 6
        tag_w = tag_text_w + tag_pad_x * 2
        tag_h = tag_text_h + tag_pad_y * 2
        tag_x0 = x + CARD_WIDTH - tag_w - 10
        tag_y0 = y + 10
        tag_x1 = tag_x0 + tag_w
        tag_y1 = tag_y0 + tag_h
        draw.rounded_rectangle(
            [tag_x0, tag_y0, tag_x1, tag_y1], radius=10, fill=status_color
        )
        # 标签内文字完全居中
        text_x = tag_x0 + (tag_w - tag_text_w) // 2
        text_y = tag_y0 + (tag_h - tag_text_h) // 2 - 4  # 往上微调4像素
        draw.text((text_x, text_y), status, fill=(255, 255, 255), font=font_label)
        # 标题（服务器名）上移
        y_title = y + 18
        draw.text((x + 20, y_title), title, fill=COLOR_TITLE, font=font_title)
        bbox = draw.textbbox((0, 0), title, font=font_title)
        title_height = bbox[3] - bbox[1]
        # 时间部分卡片
        y_timeblock = y_title + title_height + 20  # 直接接在标题下面
        timeblock_left = x + 10
        timeblock_right = x + CARD_WIDTH - 10
        draw.rounded_rectangle(
            [timeblock_left, y_timeblock, timeblock_right, y_timeblock + 110],
            radius=16,
            fill=(80, 50, 80),
        )
        # 冷却结束和强制开启时间（右对齐且不溢出）
        now = datetime.now(BEIJING_TZ)
        # 取上次奖励开始时间
        starts = data.get("last_bonus_starts", []) if data else []
        is_uptime = data.get("is_uptime") if data else False
        if starts:
            last_start = self._to_bj_time(starts[0])
            if isinstance(last_start, datetime):
                # 冷却开始时间为上次奖励开始时间+24小时
                cd_start = last_start + timedelta(hours=24)
                # 强制开启时间为上次奖励开始时间+48小时
                force_end = last_start + timedelta(hours=48)
            else:
                cd_start = (now + timedelta(days=1)).replace(
                    hour=17, minute=0, second=0, microsecond=0
                )
                force_end = (now + timedelta(days=2)).replace(
                    hour=17, minute=0, second=0, microsecond=0
                )
        else:
            cd_start = (now + timedelta(days=1)).replace(
                hour=17, minute=0, second=0, microsecond=0
            )
            force_end = (now + timedelta(days=2)).replace(
                hour=17, minute=0, second=0, microsecond=0
            )

        # 判断是否显示下次判定
        show_next_judge = False
        if is_uptime:
            # 如果在奖励时间内，直接显示冷却结束时间
            cd_end = cd_start
        else:
            # 如果不在奖励时间，检查是否在冷却期内
            if now < cd_start:
                # 在冷却期内，显示冷却结束时间
                cd_end = cd_start
            else:
                # 不在冷却期，显示下一个判定时间点
                next_judge_hours = [5, 11, 17, 23]  # 按照时间顺序排列
                next_judge = None
                for hour in next_judge_hours:
                    candidate = now.replace(
                        hour=hour, minute=0, second=0, microsecond=0
                    )
                    if candidate > now:
                        next_judge = candidate
                        break
                if next_judge is None:
                    next_judge = (now + timedelta(days=1)).replace(
                        hour=5, minute=0, second=0, microsecond=0
                    )
                cd_end = next_judge
                show_next_judge = True

        # 如果强制开启时间已过，显示下一个判定时间点
        if force_end <= now:
            next_judge_hours = [5, 11, 17, 23]  # 按照时间顺序排列
            next_judge = None
            for hour in next_judge_hours:
                candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
                if candidate > now:
                    next_judge = candidate
                    break
            if next_judge is None:
                next_judge = (now + timedelta(days=1)).replace(
                    hour=5, minute=0, second=0, microsecond=0
                )
            force_end = next_judge

        cd_left = cd_end - now
        force_left = force_end - now
        cd_str = self._format_timedelta(cd_left)
        force_str = self._format_timedelta(force_left)
        cd_date = cd_end.strftime("%m-%d %H:%M")
        force_date = force_end.strftime("%m-%d %H:%M")
        # 冷却结束
        label_x = x + 30
        timeblock_max_right = timeblock_right - 10
        # 冷却结束倒计时
        bbox = draw.textbbox((0, 0), cd_str, font=font_label)
        cd_str_w = bbox[2] - bbox[0]
        bbox = draw.textbbox((0, 0), cd_date, font=font_small)
        cd_date_w = bbox[2] - bbox[0]
        cd_str_x = timeblock_max_right - max(cd_str_w, cd_date_w)
        draw.text(
            (label_x, y_timeblock + 10),
            "下次判定:" if show_next_judge else "冷却结束:",
            fill=COLOR_LABEL,
            font=font_normal,
        )
        draw.text((cd_str_x, y_timeblock + 2), cd_str, fill=COLOR_TIME, font=font_label)
        draw.text(
            (cd_str_x, y_timeblock + 28), cd_date, fill=COLOR_TIME, font=font_small
        )
        # 强制开启
        bbox = draw.textbbox((0, 0), force_str, font=font_label)
        force_str_w = bbox[2] - bbox[0]
        bbox = draw.textbbox((0, 0), force_date, font=font_small)
        force_date_w = bbox[2] - bbox[0]
        force_str_x = timeblock_max_right - max(force_str_w, force_date_w)
        draw.text(
            (label_x, y_timeblock + 58),
            f"强制开启:",
            fill=COLOR_LABEL,
            font=font_normal,
        )
        draw.text(
            (force_str_x, y_timeblock + 50), force_str, fill=COLOR_TIME, font=font_label
        )
        draw.text(
            (force_str_x, y_timeblock + 76),
            force_date,
            fill=COLOR_TIME,
            font=font_small,
        )
        # boss图片
        y_boss = y_timeblock + 120 + 5  # 再往上移一点
        try:
            boss_img = Image.open(BOSS_IMAGE_PATH).resize((CARD_WIDTH - 40, 60))
            img.paste(boss_img, (x + 20, y_boss))
        except Exception:
            pass
        # 最近奖励记录
        y_record = y_boss + 60 + 5  # 再往上移一点
        draw.text((x + 20, y_record), "最近奖励记录", fill=COLOR_LABEL, font=font_label)
        starts = data.get("last_bonus_starts", []) if data else []
        ends = data.get("last_bonus_ends", []) if data else []
        if starts:
            # 取最新的奖励记录
            st = self._to_bj_time(starts[0])
            if isinstance(st, datetime):
                st_str = st.strftime("%m月%d日%H:%M")
                # 三行分布
                y_row1 = y_record + 30
                y_row2 = y_record + 70
                y_row3 = y_record + 110
                draw.text(
                    (x + 20, y_row1),
                    f"开始时间 {st_str}",
                    fill=COLOR_TEXT,
                    font=font_small,
                )
                if is_uptime:
                    # 如果在奖励时间内，不显示结束时间
                    draw.text(
                        (x + 20, y_row2),
                        "结束时间 进行中",
                        fill=COLOR_TEXT,
                        font=font_small,
                    )
                    draw.text(
                        (x + 20, y_row3),
                        "持续 进行中",
                        fill=COLOR_TEXT,
                        font=font_small,
                    )
                else:
                    # 如果不在奖励时间，显示结束时间
                    ed = self._to_bj_time(ends[0]) if ends else "-"
                    ed_str = (
                        ed.strftime("%m月%d日%H:%M")
                        if isinstance(ed, datetime)
                        else "-"
                    )
                    duration = self._format_duration(st, ed)
                    draw.text(
                        (x + 20, y_row2),
                        f"结束时间 {ed_str}",
                        fill=COLOR_TEXT,
                        font=font_small,
                    )
                    draw.text(
                        (x + 20, y_row3),
                        f"持续 {duration}",
                        fill=COLOR_TEXT,
                        font=font_small,
                    )
        else:
            draw.text((x + 20, y_record + 30), "-", fill=COLOR_TEXT, font=font_small)

    def _format_timedelta(self, td):
        total = int(td.total_seconds())
        h = total // 3600
        m = (total % 3600) // 60
        s = total % 60
        return f"{h:02}:{m:02}:{s:02}"

    def _to_bj_time(self, tstr):
        try:
            dt = datetime.fromisoformat(tstr.replace("Z", "+00:00"))
            return dt.astimezone(BEIJING_TZ)
        except Exception:
            return "-"

    def _format_duration(self, st, ed):
        if not isinstance(st, datetime) or not isinstance(ed, datetime):
            return "-"
        delta = ed - st
        hours = delta.total_seconds() // 3600
        if hours >= 1:
            return f"{int(hours)}小时"
        else:
            return f"{int(delta.total_seconds() // 60)}分钟"
