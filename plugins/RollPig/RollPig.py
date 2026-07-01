"""
RollPig 插件
今日小猪 - 抽取属于自己的每日小猪
改编自 astrbot_plugin_rollpig
"""

import asyncio
import datetime
import json
import random
import re
import tempfile
import logging
from pathlib import Path
from typing import List, Optional

from PIL import Image as PILImage, ImageDraw, ImageFont

from ncatbot.event.qq import GroupMessageEvent as GroupMessage
from ncatbot.types import At, MessageArray as MessageChain, PlainText, Image
from ncatbot.plugin import NcatBotPlugin
from ncatbot.core import registrar


_log = logging.getLogger(__name__)


class RollPig(NcatBotPlugin):
    """今日小猪插件"""

    name = "RollPig"
    version = "1.0"
    description = "今日小猪 - 抽取属于自己的每日小猪"

    # ========== 配置参数 ==========
    CANVAS_WIDTH = 800  # 画布宽度
    CANVAS_HEIGHT = 800  # 画布高度
    AVATAR_SIZE = 280  # 头像大小
    SPACING_AVATAR_NAME = 20  # 头像与名称间距
    SPACING_NAME_DESC = 25  # 名称与描述间距
    SPACING_DESC_ANALYSIS = 30  # 描述与解析间距
    DESC_FONT_SIZE = 32  # 描述字体大小
    ANALYSIS_FONT_SIZE = 28  # 解析字体大小
    ANALYSIS_LINE_HEIGHT_FACTOR = 1.6  # 解析行高因子
    ANALYSIS_WIDTH_RATIO = 0.85  # 解析宽度比例
    NAME_FONT_SIZE = 66  # 名称字体大小

    # 命令别名
    COMMANDS = {"今日小猪", "抽小猪", "我的小猪", "rollpig"}
    LIST_COMMANDS = {"小猪列表", "小猪图鉴", "猪列表"}

    # 图鉴网格参数（单张长图，8 列紧凑布局）
    LIST_COLS = 8
    LIST_CANVAS_WIDTH = 920
    LIST_PADDING = 20
    LIST_HEADER_HEIGHT = 52
    LIST_CELL_GAP = 6
    LIST_CELL_IMAGE_SIZE = 64
    LIST_CELL_NAME_HEIGHT = 20
    LIST_NAME_FONT_SIZE = 14
    LIST_TITLE_FONT_SIZE = 24

    data_dir = "data"

    async def on_load(self):
        """插件加载"""
        _log.info(f"开始加载 {self.name} 插件 v{self.version}")

        # 初始化路径
        self.plugin_data_dir = Path(self.data_dir) / "RollPig"
        self.image_dir = Path(self.data_dir) / "image" / "rollpig"
        self.font_dir = Path(self.data_dir) / "font"
        self.piginfo_path = Path(self.data_dir) / "json" / "pig.json"
        self.today_path = self.plugin_data_dir / "rollpig_today.json"

        # 确保目录存在
        self.plugin_data_dir.mkdir(parents=True, exist_ok=True)

        # 加载小猪信息
        self.pig_list = self.load_json(self.piginfo_path, [])
        if not self.pig_list:
            _log.error("[RollPig] 小猪信息为空或不存在，请检查资源文件！")

        # 初始化字体
        self.font_regular = self._init_regular_font()
        self.font_bold = self._init_bold_font()
        self.font_list_name = self._load_font(
            [
                self.font_dir / "可爱字体.ttf",
                self.font_dir / "SourceHanSansCN-Regular.otf",
                "C:/Windows/Fonts/msyh.ttc",
                "C:/Windows/Fonts/simhei.ttf",
            ],
            self.LIST_NAME_FONT_SIZE,
            "图鉴",
        )
        self.font_list_title = self._load_font(
            [
                self.font_dir / "荆南麦圆体.otf",
                self.font_dir / "SourceHanSansCN-Bold.otf",
                "C:/Windows/Fonts/msyhbd.ttc",
            ],
            self.LIST_TITLE_FONT_SIZE,
            "图鉴标题",
        )

        _log.info(f"{self.name} 插件加载完成")

    async def on_unload(self):
        """插件卸载"""
        _log.info(f"{self.name} 插件已卸载")

    def _load_font(
        self, font_candidates: list, size: int, purpose: str
    ) -> ImageFont.FreeTypeFont:
        """通用字体加载器"""
        for font_path in font_candidates:
            if Path(font_path).exists():
                try:
                    return ImageFont.truetype(str(font_path), size)
                except Exception as e:
                    _log.warning(f"[RollPig] 加载{purpose}字体{font_path}失败：{e}")
                    continue
        _log.warning(f"[RollPig] 未找到{purpose}字体，使用默认字体")
        return ImageFont.load_default()

    def _init_regular_font(self) -> ImageFont.FreeTypeFont:
        """初始化常规字体（可爱字体，用于描述/解析）"""
        font_paths = [
            self.font_dir / "可爱字体.ttf",
            self.font_dir / "SourceHanSansCN-Regular.otf",
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/simhei.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/System/Library/Fonts/PingFang.ttc",
        ]
        return self._load_font(font_paths, self.DESC_FONT_SIZE, "常规")

    def _init_bold_font(self) -> ImageFont.FreeTypeFont:
        """初始化加粗字体（荆南麦圆体，用于名称）"""
        font_paths = [
            self.font_dir / "荆南麦圆体.otf",
            self.font_dir / "SourceHanSansCN-Bold.otf",
            "C:/Windows/Fonts/msyhbd.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/System/Library/Fonts/PingFang.ttc",
        ]
        return self._load_font(font_paths, self.NAME_FONT_SIZE, "加粗")

    def _get_text_size(self, text: str, font: ImageFont.FreeTypeFont) -> tuple:
        """兼容PIL不同版本的文字尺寸计算"""
        draw = ImageDraw.Draw(PILImage.new("RGB", (1, 1)))
        try:
            bbox = draw.textbbox((0, 0), text, font=font)
            return (bbox[2] - bbox[0], bbox[3] - bbox[1])
        except Exception:
            return draw.textsize(text, font=font)

    def _draw_bold_text(
        self,
        draw: ImageDraw.ImageDraw,
        pos: tuple,
        text: str,
        font: ImageFont.FreeTypeFont,
        fill: tuple,
    ):
        """模拟文字加粗"""
        x, y = pos
        offsets = [(1, 0), (-1, 0), (0, 1), (0, -1)]
        for ox, oy in offsets:
            draw.text((x + ox, y + oy), text, fill=fill, font=font)
        draw.text((x, y), text, fill=fill, font=font)

    def load_json(self, path: Path, default):
        """加载JSON文件"""
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(default, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return default
        try:
            return json.loads(path.read_text("utf-8"))
        except json.JSONDecodeError:
            _log.error(f"[RollPig] JSON文件解析失败，重置为默认值：{path}")
            path.write_text(
                json.dumps(default, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return default

    def save_json(self, path: Path, data):
        """保存JSON数据"""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _load_pig_thumbnail(self, pig_id: str, size: int) -> Optional[PILImage.Image]:
        """加载并裁剪为方形缩略图"""
        avatar_path = self.find_image_file(pig_id)
        if not avatar_path:
            return None
        try:
            with PILImage.open(avatar_path) as img:
                if getattr(img, "is_animated", False):
                    img.seek(0)
                frame = img.convert("RGBA")
                frame.thumbnail((size, size), PILImage.Resampling.LANCZOS)
                if frame.size != (size, size):
                    left = (frame.width - size) // 2
                    top = (frame.height - size) // 2
                    frame = frame.crop((left, top, left + size, top + size))
                return frame.copy()
        except Exception as e:
            _log.warning(f"[RollPig] 加载缩略图失败 {pig_id}: {e}")
            return None

    def _truncate_text(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.FreeTypeFont,
        max_width: int,
    ) -> str:
        if not text:
            return text
        if self._get_text_size(text, font)[0] <= max_width:
            return text
        ellipsis = "…"
        for end in range(len(text) - 1, 0, -1):
            probe = text[:end] + ellipsis
            if self._get_text_size(probe, font)[0] <= max_width:
                return probe
        return ellipsis

    def render_pig_list_image(self) -> Optional[Path]:
        """渲染完整小猪图鉴（单张长图）"""
        if not self.pig_list:
            return None

        pigs = self.pig_list
        width = self.LIST_CANVAS_WIDTH
        padding = self.LIST_PADDING
        header_h = self.LIST_HEADER_HEIGHT
        cols = self.LIST_COLS
        gap = self.LIST_CELL_GAP
        img_size = self.LIST_CELL_IMAGE_SIZE
        name_h = self.LIST_CELL_NAME_HEIGHT
        inner_w = width - padding * 2
        cell_w = (inner_w - gap * (cols - 1)) // cols
        row_h = img_size + name_h + gap
        rows = (len(pigs) + cols - 1) // cols
        height = padding * 2 + header_h + rows * row_h

        canvas = PILImage.new("RGB", (width, height), (255, 252, 248))
        draw = ImageDraw.Draw(canvas)

        title = f"小猪图鉴 · 共 {len(pigs)} 只"
        title_w, _ = self._get_text_size(title, self.font_list_title)
        draw.text(
            ((width - title_w) // 2, padding),
            title,
            fill=(255, 120, 80),
            font=self.font_list_title,
        )
        draw.line(
            [
                (padding, padding + header_h - 12),
                (width - padding, padding + header_h - 12),
            ],
            fill=(240, 220, 210),
            width=2,
        )

        start_y = padding + header_h
        for idx, pig in enumerate(pigs):
            col = idx % cols
            row = idx // cols
            x = padding + col * (cell_w + gap)
            y = start_y + row * row_h

            draw.rounded_rectangle(
                (x, y, x + cell_w, y + img_size + name_h + 4),
                radius=8,
                fill=(255, 255, 255),
                outline=(235, 225, 220),
                width=1,
            )

            thumb_x = x + (cell_w - img_size) // 2
            thumb_y = y + 3
            thumb = self._load_pig_thumbnail(pig.get("id", ""), img_size)
            if thumb:
                canvas.paste(thumb, (thumb_x, thumb_y), thumb)
            else:
                draw.rectangle(
                    (thumb_x, thumb_y, thumb_x + img_size, thumb_y + img_size),
                    fill=(245, 240, 238),
                    outline=(220, 210, 205),
                )

            name = pig.get("name", "未知")
            name_font = self.font_list_name
            name_w, _ = self._get_text_size(name, name_font)
            max_name_w = cell_w - 6
            if name_w > max_name_w:
                name = self._truncate_text(draw, name, name_font, max_name_w)
                name_w, _ = self._get_text_size(name, name_font)
            name_x = x + (cell_w - name_w) // 2
            name_y = y + img_size + 4
            draw.text((name_x, name_y), name, fill=(80, 70, 65), font=name_font)

        try:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp_path = Path(tmp.name)
                canvas.save(tmp_path, format="PNG", optimize=True)
            return tmp_path
        except Exception as e:
            _log.error(f"[RollPig] 渲染图鉴失败：{e}")
            return None

    def find_image_file(self, pig_id: str) -> Optional[Path]:
        """查找对应ID的图片文件"""
        exts = ["png", "jpg", "jpeg", "webp", "gif"]
        for ext in exts:
            file = self.image_dir / f"{pig_id}.{ext}"
            if file.exists():
                _log.debug(f"[RollPig] 找到的小猪图片文件：{file.absolute()}")
                return file
        _log.warning(f"[RollPig] 未找到小猪ID {pig_id} 对应的图片文件")
        return None

    def render_pig_image(self, pig_data: dict) -> Optional[Path]:
        """渲染小猪图片"""
        pig_id = pig_data.get("id", "")
        pig_name = pig_data.get("name", "未知小猪")
        pig_desc = pig_data.get("description", "无描述")
        pig_analysis = pig_data.get("analysis", "无解析")

        # 1. 画布基础配置
        canvas_width = self.CANVAS_WIDTH
        canvas_height = self.CANVAS_HEIGHT
        canvas = PILImage.new("RGB", (canvas_width, canvas_height), (255, 255, 255))
        draw = ImageDraw.Draw(canvas)

        # 2. 预加载所有元素并计算尺寸
        avatar_w, avatar_h = self.AVATAR_SIZE, self.AVATAR_SIZE
        avatar = None
        avatar_path = self.find_image_file(pig_id)
        if avatar_path:
            try:
                avatar = PILImage.open(avatar_path)
                avatar.thumbnail((avatar_w, avatar_h))
                if avatar.size != (avatar_w, avatar_h):
                    center_x = avatar.width // 2
                    center_y = avatar.height // 2
                    half = self.AVATAR_SIZE // 2
                    crop_box = (
                        center_x - half,
                        center_y - half,
                        center_x + half,
                        center_y + half,
                    )
                    avatar = avatar.crop(crop_box)
            except Exception as e:
                _log.error(f"[RollPig] 加载小猪图片失败：{str(e)}")
                avatar = None

        # 2.2 名称尺寸
        name_font = self.font_bold
        name_w, name_h = self._get_text_size(pig_name, name_font)

        # 2.3 描述尺寸
        try:
            desc_font = self.font_regular.font_variant(size=self.DESC_FONT_SIZE)
        except Exception:
            desc_font = self.font_regular
        desc_w, desc_h = self._get_text_size(pig_desc, desc_font)

        # 2.4 解析尺寸（自动换行后）
        try:
            analysis_font = self.font_regular.font_variant(size=self.ANALYSIS_FONT_SIZE)
        except Exception:
            analysis_font = self.font_regular
        line_height = int(self.ANALYSIS_FONT_SIZE * self.ANALYSIS_LINE_HEIGHT_FACTOR)
        max_analysis_width = int(canvas_width * self.ANALYSIS_WIDTH_RATIO)

        # 解析文字换行
        analysis_lines = []
        current_line = ""
        for char in pig_analysis:
            current_line += char
            line_w, _ = self._get_text_size(current_line, analysis_font)
            if line_w > max_analysis_width:
                analysis_lines.append(current_line[:-1])
                current_line = char
        if current_line:
            analysis_lines.append(current_line)
        analysis_total_h = len(analysis_lines) * line_height

        # 3. 计算整体内容总高度
        spacing_avatar_name = self.SPACING_AVATAR_NAME
        spacing_name_desc = self.SPACING_NAME_DESC
        spacing_desc_analysis = self.SPACING_DESC_ANALYSIS
        total_content_h = (
            avatar_h
            + spacing_avatar_name
            + name_h
            + spacing_name_desc
            + desc_h
            + spacing_desc_analysis
            + analysis_total_h
        )

        # 4. 计算垂直居中的起始Y坐标
        start_y = (canvas_height - total_content_h) // 2

        # 5. 绘制所有元素
        # 5.1 绘制头像
        avatar_x = (canvas_width - avatar_w) // 2
        avatar_y = start_y
        if avatar:
            canvas.paste(
                avatar,
                (avatar_x, avatar_y),
                mask=avatar if avatar.mode == "RGBA" else None,
            )
        else:
            try:
                error_font = self.font_regular.font_variant(size=24)
            except Exception:
                error_font = self.font_regular
            error_text = "图片加载失败"
            error_w, error_h = self._get_text_size(error_text, error_font)
            error_x = (canvas_width - error_w) // 2
            draw.text(
                (error_x, avatar_y + 120),
                error_text,
                fill=(255, 0, 0),
                font=error_font,
            )

        # 5.2 绘制名称
        name_y = avatar_y + avatar_h + spacing_avatar_name
        name_x = (canvas_width - name_w) // 2
        self._draw_bold_text(draw, (name_x, name_y), pig_name, name_font, (0, 0, 0))

        # 5.3 绘制描述
        desc_y = name_y + name_h + spacing_name_desc
        desc_x = (canvas_width - desc_w) // 2
        draw.text((desc_x, desc_y), pig_desc, fill=(85, 85, 85), font=desc_font)

        # 5.4 绘制解析
        analysis_y = desc_y + desc_h + spacing_desc_analysis
        for line in analysis_lines:
            line_w, line_h = self._get_text_size(line, analysis_font)
            line_x = (canvas_width - line_w) // 2
            draw.text((line_x, analysis_y), line, fill=(51, 51, 51), font=analysis_font)
            analysis_y += line_height

        # 6. 保存临时文件
        try:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp_path = Path(tmp.name)
                canvas.save(tmp_path, format="PNG", quality=95)
            _log.debug(f"[RollPig] 合成图片成功，临时文件路径：{tmp_path.absolute()}")
            if not tmp_path.exists():
                _log.error(f"[RollPig] 临时文件创建失败：{tmp_path}")
                return None
            return tmp_path
        except Exception as e:
            _log.error(f"[RollPig] 合成图片失败：{str(e)}")
            return None

    def _get_at_ids(self, input: GroupMessage) -> List[str]:
        """
        获取消息中被 @ 的用户 ID 列表（排除机器人自己）
        """
        at_ids = []
        raw_message = input.raw_message

        # 获取机器人自己的 ID
        bot_id = None
        if hasattr(self, "api") and hasattr(self.api, "self_id"):
            bot_id = str(self.api.self_id)

        # 从 CQ 码中解析 @ 信息
        at_pattern = re.compile(r"\[CQ:at,qq=(\d+)\]")
        for match in at_pattern.finditer(raw_message):
            qq = match.group(1)
            if qq != bot_id:  # 排除机器人自己
                at_ids.append(qq)

        return at_ids

    def _extract_command(self, message: str) -> str:
        """
        从消息中提取命令（去除 @ 信息和多余空格）
        """
        # 移除 CQ:at 码
        clean_message = re.sub(r"\[CQ:at,qq=\d+\]", "", message)
        # 移除多余空格
        clean_message = clean_message.strip()
        return clean_message

    @registrar.qq.on_group_message()
    async def handle_message(self, input: GroupMessage):
        """处理消息"""
        raw_message = input.raw_message.strip()

        # 提取纯命令（去除 @ 信息）
        command = self._extract_command(raw_message)

        if command in self.LIST_COMMANDS:
            await self._send_pig_list(input)
            return

        # 检查是否是今日小猪命令
        if command not in self.COMMANDS:
            return

        group_id = input.group_id
        sender_id = str(input.sender.user_id)

        # 检查是否有 @ 群友
        at_ids = self._get_at_ids(input)

        if len(at_ids) > 1:
            # 一次只能抽取一个
            await self.api.qq.post_group_msg(
                group_id=group_id, text="一次只能抽取一个小猪哦！"
            )
            return

        # 确定目标用户 ID：如果有 @，则使用被 @ 的用户；否则使用发送者
        if at_ids:
            target_user_id = at_ids[0]
        else:
            target_user_id = sender_id

        today_str = datetime.date.today().isoformat()
        today_cache = self.load_json(self.today_path, {"date": "", "records": {}})

        # 检查日期是否变更，重置缓存
        if today_cache.get("date") != today_str:
            today_cache = {"date": today_str, "records": {}}

        user_records = today_cache["records"]

        # 如果目标用户今天已经抽过，返回之前的结果
        if target_user_id in user_records:
            pig = user_records[target_user_id]
            await self._send_rendered_pig(input, pig, target_user_id)
            return

        # 检查小猪列表
        if not self.pig_list:
            await self.api.qq.post_group_msg(
                group_id=group_id, text="小猪信息加载失败，请检查后台报错！"
            )
            return

        # 随机抽取小猪
        pig = random.choice(self.pig_list)
        user_records[target_user_id] = pig
        self.save_json(self.today_path, today_cache)

        await self._send_rendered_pig(input, pig, target_user_id)

    async def _send_pig_list(self, input: GroupMessage):
        """发送小猪图鉴图片（单张长图，无文字消息）"""
        group_id = input.group_id
        img_path = await asyncio.to_thread(self.render_pig_list_image)

        if not img_path or not img_path.exists():
            _log.warning("[RollPig] 图鉴渲染失败")
            return

        try:
            await self.api.qq.post_group_msg(
                group_id=group_id,
                rtf=MessageChain([Image(file=str(img_path.absolute()))]),
            )
        except Exception as e:
            _log.error(f"[RollPig] 发送图鉴图片失败：{e}")
        finally:
            try:
                img_path.unlink(missing_ok=True)
            except Exception as cleanup_err:
                _log.warning(f"[RollPig] 清理图鉴临时图片失败：{cleanup_err}")

    async def _send_rendered_pig(
        self, input: GroupMessage, pig_data: dict, user_id: str
    ):
        """合成并发送小猪图片"""
        group_id = input.group_id

        # 使用线程池异步执行CPU密集型任务
        img_path = await asyncio.to_thread(self.render_pig_image, pig_data)

        if img_path and img_path.exists():
            try:
                # 合并成一条消息发送
                chain = MessageChain(
                    [
                        At(user_id=str(user_id)),
                        PlainText(text=" 这是你的今日小猪：\n"),
                        Image(file=str(img_path.absolute())),
                    ]
                )
                await self.api.qq.post_group_msg(group_id=group_id, rtf=chain)

                _log.info("[RollPig] 合成图片发送成功")
                return
            except Exception as e:
                _log.error(f"[RollPig] 发送合成图片失败：{str(e)}")
            finally:
                try:
                    img_path.unlink(missing_ok=True)
                except Exception as cleanup_err:
                    _log.warning(f"[RollPig] 清理临时图片失败：{cleanup_err}")

        # 降级发送
        await self._send_fallback_msg(input, pig_data)

    async def _send_fallback_msg(self, input: GroupMessage, pig_data: dict):
        """降级发送：原始图片 + 纯文本"""
        group_id = input.group_id
        pig_name = pig_data.get("name", "未知小猪")
        pig_desc = pig_data.get("description", "无描述")
        pig_analysis = pig_data.get("analysis", "无解析")
        pig_id = pig_data.get("id", "")

        text_msg = (
            f"【今日小猪】\n名称：{pig_name}\n描述：{pig_desc}\n解析：{pig_analysis}"
        )

        avatar_path = self.find_image_file(pig_id)
        if avatar_path and avatar_path.exists():
            try:
                chain = MessageChain(
                    [
                        Image(file=str(avatar_path.absolute())),
                        PlainText(text=text_msg),
                    ]
                )
                await self.api.qq.post_group_msg(group_id=group_id, rtf=chain)
                return
            except Exception as e:
                _log.error(f"[RollPig] 发送原始图片失败：{str(e)}")
                text_msg += "\n\n（图片发送失败，仅展示文字信息）"

        await self.api.qq.post_group_msg(group_id=group_id, text=text_msg)
