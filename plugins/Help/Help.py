"""
Help 插件 - 显示所有可用插件的功能和触发命令
"""

import os
import logging
from datetime import datetime
from PIL import Image as PILImage, ImageDraw, ImageFont

from ncatbot.core import Image, MessageChain, Reply, GroupMessage
from ncatbot.plugin_system import NcatBotPlugin, on_message

log = logging.getLogger(__name__)

# 插件信息列表
PLUGIN_INFO = [
    {
        "name": "🎲 今日人品",
        "commands": ["jrrp"],
        "desc": "查看今日运气值",
    },
    {
        "name": "🔮 今日运势",
        "commands": ["今日运势"],
        "desc": "抽取今日运势签",
    },
    {
        "name": "🃏 塔罗占卜",
        "commands": ["占卜"],
        "desc": "抽取塔罗牌",
    },
    {
        "name": "💑 今日老婆",
        "commands": ["今日老婆"],
        "desc": "随机分配群友作为今日老婆",
    },
    {
        "name": "💕 二次元老婆",
        "commands": ["今日二次元老婆"],
        "desc": "随机分配二次元老婆",
    },
    {
        "name": "🍔 吃什么",
        "commands": ["吃什么"],
        "desc": "随机推荐吃的/喝的",
    },
    {
        "name": "🐟 摸鱼日历",
        "commands": ["摸鱼"],
        "desc": "查看摸鱼日历",
    },
    {
        "name": "🍗 疯狂星期四",
        "commands": ["疯狂星期四"],
        "desc": "随机KFC文案",
    },
    {
        "name": "🎭 表情包制作",
        "commands": ["meme"],
        "desc": "发送meme查看关键词列表",
    },
    {
        "name": "🎵 点歌",
        "commands": ["点歌 歌名"],
        "desc": "网易云音乐点歌",
    },
    {
        "name": "🎶 QQ点歌",
        "commands": ["qq点歌 歌名"],
        "desc": "QQ音乐点歌",
    },
    {
        "name": "🔍 识别人物",
        "commands": ["查询人物"],
        "desc": "识别动漫/Gal角色（回复图片）",
    },
    {
        "name": "📺 B站解析",
        "commands": ["发送B站链接"],
        "desc": "自动解析B站视频信息",
    },
    {
        "name": "🔎 搜视频",
        "commands": ["搜视频 关键词"],
        "desc": "搜索B站视频",
    },
    {
        "name": "🎮 VRC查询",
        "commands": ["搜索vrc玩家 用户名"],
        "desc": "查询VRChat玩家信息",
    },
    {
        "name": "💰 FF14物价",
        "commands": ["搜索物品 名称 服务器"],
        "desc": "查询FF14市场物价",
    },
    {
        "name": "📊 发言统计",
        "commands": ["发言统计 时间 对象"],
        "desc": "如: 发言统计 今日 群",
    },
    {
        "name": "😀 表情包统计",
        "commands": ["表情包统计 时间 对象"],
        "desc": "如: 表情包统计 今日 群",
    },
    {
        "name": "📝 翻译缩写",
        "commands": ["翻译 缩写"],
        "desc": "翻译网络缩写词",
    },
    {
        "name": "🔫 轮盘赌",
        "commands": ["轮盘赌"],
        "desc": "俄罗斯轮盘赌小游戏",
    },
    {
        "name": "🎰 大乐透",
        "commands": ["开始大乐透"],
        "desc": "群大乐透抽奖",
    },
    {
        "name": "👍 点赞",
        "commands": ["赞我"],
        "desc": "让Bot给你点赞",
    },
    {
        "name": "🐱 随机表情包",
        "commands": ["随机表情包"],
        "desc": "获取随机表情包",
    },
    {
        "name": "🔄 撤回查询",
        "commands": ["查询撤回"],
        "desc": "查看撤回的消息记录(管理员)",
    },
    {
        "name": "🐾 卡拉彼丘",
        "commands": ["卡拉彼丘"],
        "desc": "随机卡拉彼丘语录",
    },
    {
        "name": "💬 蓝晴AI",
        "commands": ["@蓝晴"],
        "desc": "和蓝晴聊天",
    },
    {
        "name": "📋 蓝晴印象",
        "commands": ["蓝晴印象"],
        "desc": "查看蓝晴对你的印象",
    },
]


class Help(NcatBotPlugin):
    """帮助插件 - 显示所有可用功能"""

    name = "Help"
    version = "1.0"
    description = "显示所有可用插件的功能和触发命令"

    # 图片生成配置
    FONT_PATH = "data/font/sakura.ttf"
    FALLBACK_FONT = "data/font/simhei.ttf"
    OUTPUT_DIR = "data/image/help"

    # 颜色配置
    BG_COLOR = (30, 33, 40)  # 深色背景
    TITLE_COLOR = (255, 200, 100)  # 金色标题
    NAME_COLOR = (130, 200, 255)  # 浅蓝色插件名
    CMD_COLOR = (150, 255, 150)  # 浅绿色命令
    DESC_COLOR = (200, 200, 200)  # 灰色描述
    LINE_COLOR = (60, 65, 75)  # 分隔线颜色

    async def on_load(self):
        """插件加载"""
        os.makedirs(self.OUTPUT_DIR, exist_ok=True)
        log.info(f"[{self.name}] 插件加载完成")

    @on_message
    async def handle_help(self, input: GroupMessage):
        """处理帮助命令"""
        message = input.raw_message.strip()

        if message not in ["help", "帮助", "功能", "菜单"]:
            return

        try:
            # 生成帮助图片
            image_path = self._generate_help_image()

            # 发送图片
            await self.api.post_group_msg(
                group_id=input.group_id,
                rtf=MessageChain(
                    [
                        Image(image_path),
                        Reply(input.message_id),
                    ]
                ),
            )
        except Exception as e:
            log.error(f"[{self.name}] 生成帮助图片失败: {e}")
            await self.api.post_group_msg(
                group_id=input.group_id,
                text="生成帮助图片失败，请稍后再试",
            )

    def _get_font(self, size: int) -> ImageFont.FreeTypeFont:
        """获取字体"""
        try:
            return ImageFont.truetype(self.FONT_PATH, size)
        except Exception:
            try:
                return ImageFont.truetype(self.FALLBACK_FONT, size)
            except Exception:
                return ImageFont.load_default()

    def _generate_help_image(self) -> str:
        """生成帮助图片"""
        # 字体设置
        title_font = self._get_font(36)
        name_font = self._get_font(22)
        cmd_font = self._get_font(18)
        desc_font = self._get_font(16)
        footer_font = self._get_font(14)

        # 计算图片尺寸
        padding = 40
        item_height = 85  # 每个插件项的高度
        cols = 2  # 两列布局
        rows = (len(PLUGIN_INFO) + cols - 1) // cols

        width = 900
        height = padding * 2 + 80 + rows * item_height + 50  # 标题 + 内容 + 底部

        # 创建图片
        image = PILImage.new("RGB", (width, height), self.BG_COLOR)
        draw = ImageDraw.Draw(image)

        # 绘制标题
        title = "🌟 蓝晴Bot 功能列表 🌟"
        title_bbox = draw.textbbox((0, 0), title, font=title_font)
        title_width = title_bbox[2] - title_bbox[0]
        draw.text(
            ((width - title_width) // 2, padding),
            title,
            font=title_font,
            fill=self.TITLE_COLOR,
        )

        # 绘制分隔线
        draw.line(
            [(padding, padding + 60), (width - padding, padding + 60)],
            fill=self.LINE_COLOR,
            width=2,
        )

        # 绘制插件列表
        col_width = (width - padding * 2) // cols
        start_y = padding + 80

        for i, plugin in enumerate(PLUGIN_INFO):
            col = i % cols
            row = i // cols

            x = padding + col * col_width + 10
            y = start_y + row * item_height

            # 插件名称
            draw.text(
                (x, y),
                plugin["name"],
                font=name_font,
                fill=self.NAME_COLOR,
            )

            # 触发命令
            cmd_text = "命令: " + " | ".join(plugin["commands"])
            # 限制命令长度
            if len(cmd_text) > 35:
                cmd_text = cmd_text[:32] + "..."
            draw.text(
                (x + 10, y + 28),
                cmd_text,
                font=cmd_font,
                fill=self.CMD_COLOR,
            )

            # 功能描述
            draw.text(
                (x + 10, y + 52),
                plugin["desc"],
                font=desc_font,
                fill=self.DESC_COLOR,
            )

            # 绘制分隔线（每行之间）
            if row < rows - 1 or col == 0:
                line_y = y + item_height - 5
                if col == 0:
                    draw.line(
                        [(padding, line_y), (width - padding, line_y)],
                        fill=self.LINE_COLOR,
                        width=1,
                    )

        # 绘制底部信息
        footer_y = height - 35
        draw.line(
            [(padding, footer_y - 10), (width - padding, footer_y - 10)],
            fill=self.LINE_COLOR,
            width=2,
        )

        footer_text = "发送 help/帮助/功能 查看此页面 | 更多功能开发中..."
        footer_bbox = draw.textbbox((0, 0), footer_text, font=footer_font)
        footer_width = footer_bbox[2] - footer_bbox[0]
        draw.text(
            ((width - footer_width) // 2, footer_y),
            footer_text,
            font=footer_font,
            fill=(150, 150, 150),
        )

        # 保存图片
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        output_path = os.path.join(self.OUTPUT_DIR, f"help_{timestamp}.png")
        image.save(output_path, "PNG")

        return output_path
