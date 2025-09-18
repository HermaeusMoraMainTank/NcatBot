# coding: utf-8
# Author: 一铭
# GitHub: github.com/HG-ha/nsfwpy
# Date: 2025-06-24
# License: GPL-3.0
# Version: 0.0.2
# Description: 群聊NSFW内容检测插件，基于nsfwpy

from .ymconfig import nsfwdb
from .utils import nsfwc, refresh_nsfw_config
from ncatbot.core import GroupMessage
from ncatbot.plugin_system.builtin_mixin.ncatbot_plugin import NcatBotPlugin
from ncatbot.plugin_system.builtin_plugin.unified_registry.filter_system.decorators import (
    group_only,
)
from ncatbot.utils.logger import get_log
from ncatbot.utils import config
import io
import base64
from PIL import Image, ImageDraw, ImageFont

from ncatbot.core import (
    MessageChain,
    Text,
    At,
    Image as BotImage,
)

_log = get_log()


class YmNsfwPy(NcatBotPlugin):
    name = "YmNsfwPy"
    version = "0.0.2"
    description = "群聊NSFW内容检测插件, 基于nsfwpy，由于插件形态可能存在变动，暂时不按照插件规范进行开发"

    ROOT = config.root
    NSFW_CONFIG = nsfwdb()

    def save_config(self):
        """保存配置到插件数据"""
        self.config.update(self.NSFW_CONFIG.data)
        _log.info("配置已保存")

    def reload_config(self):
        """重新加载配置"""
        refresh_nsfw_config(self)
        _log.info("配置已刷新")

    async def generate_help_image(self) -> bytes:
        """生成帮助图片"""
        # 定义命令分类
        categories = {
            "管理员管理": [
                ("添加管理员 @用户", "添加群组管理员（仅全局管理员）"),
                ("移除管理员 @用户", "移除群组管理员（仅全局管理员）"),
                ("查看管理员", "查看管理员列表（仅全局管理员）"),
            ],
            "通知人管理": [
                ("添加通知人 @用户", "添加NSFW检测通知人（管理员可用）"),
                ("移除通知人 @用户", "移除NSFW检测通知人（管理员可用）"),
                ("查看通知人", "查看通知人列表（仅全局管理员）"),
            ],
            "开关控制": [
                ("开启nsfw检测", "开启本群NSFW检测（管理员可用）"),
                ("关闭nsfw检测", "关闭本群NSFW检测（管理员可用）"),
            ],
            "阈值设置": [
                ("设置全局阈值 0.x", "设置全局检测阈值（仅全局管理员）"),
                ("设置阈值 0.x", "设置本群检测阈值（管理员可用）"),
                ("重置阈值", "重置本群阈值使用全局阈值（管理员可用）"),
                ("查看阈值", "查看当前检测阈值"),
            ],
            "模型设置": [
                ("切换模型 d/m2/i3", "切换检测模型（仅全局管理员）"),
            ],
            "配置管理": [
                ("刷新配置", "手动刷新配置（仅全局管理员）"),
                ("保存配置", "手动保存配置（仅全局管理员）"),
            ],
            "其他功能": [
                ("测试", "测试插件是否正常工作"),
                ("debug", "查看调试信息（仅全局管理员）"),
            ],
        }

        # 计算图片尺寸
        width = 900
        title_height = 60
        category_height = 30
        command_height = 25
        spacing = 20
        bottom_margin = 40

        # 计算总高度
        total_height = title_height
        for category, commands in categories.items():
            total_height += category_height + len(commands) * command_height + spacing
        total_height += bottom_margin

        # 创建图片
        image = Image.new("RGB", (width, total_height), color="white")
        draw = ImageDraw.Draw(image)

        # 尝试加载字体
        try:
            font = ImageFont.truetype("simhei.ttf", 18)
            title_font = ImageFont.truetype("simhei.ttf", 24)
            category_font = ImageFont.truetype("simhei.ttf", 20)
        except:
            font = ImageFont.load_default()
            title_font = ImageFont.load_default()
            category_font = ImageFont.load_default()

        # 绘制标题
        title = "YmNsfwPy NSFW检测插件帮助"
        title_width = draw.textlength(title, font=title_font)
        draw.text(
            ((width - title_width) // 2, 20), title, fill="black", font=title_font
        )

        # 绘制分类和命令
        y = title_height
        for category, commands in categories.items():
            # 绘制分类标题
            draw.text((20, y), f"【{category}】", fill="blue", font=category_font)
            y += category_height

            # 绘制该分类下的所有命令
            for command, description in commands:
                text = f"{command}: {description}"
                # 如果文本太长，进行换行处理
                if draw.textlength(text, font=font) > width - 60:
                    # 计算每行可以容纳的字符数
                    chars_per_line = int(
                        (width - 60) / (draw.textlength("测", font=font))
                    )
                    # 分行显示
                    for i in range(0, len(text), chars_per_line):
                        line = text[i : i + chars_per_line]
                        draw.text((40, y), line, fill="black", font=font)
                        y += command_height
                else:
                    draw.text((40, y), text, fill="black", font=font)
                    y += command_height

            y += spacing  # 分类之间的间距

        # 添加底部说明
        footer_text = "注意：全局管理员拥有所有权限，群组管理员拥有部分管理权限"
        draw.text((20, y), footer_text, fill="red", font=font)

        # 将图片转换为字节流
        img_byte_arr = io.BytesIO()
        image.save(img_byte_arr, format="PNG")
        img_byte_arr.seek(0)
        return img_byte_arr.getvalue()

    @group_only
    async def on_group_event(self, msg: GroupMessage):
        # 处理帮助命令
        if msg.raw_message == "nsfw":
            try:
                help_image = await self.generate_help_image()
                # 将图片数据转换为base64
                image_base64 = base64.b64encode(help_image).decode()
                await msg.reply(
                    rtf=MessageChain([BotImage(f"base64://{image_base64}")])
                )
            except Exception as e:
                _log.error(f"发送帮助图片失败: {e}")
                await msg.reply(text="发送帮助图片失败，请稍后重试")
            return

        if self.NSFW_CONFIG.is_group_check_enabled(msg.group_id):
            await nsfwc(msg, self)

        # 管理员管理功能（仅全局管理员可用）
        if msg.raw_message[:5] == "添加管理员":
            if self.NSFW_CONFIG.is_global_admin(msg.user_id):
                for user in msg.message:
                    if user["type"] == "at":
                        self.NSFW_CONFIG.add_group_admin(
                            msg.group_id, int(user["data"]["qq"])
                        )

                await msg.reply(text="管理员添加成功")
            else:
                await msg.reply(text="只有全局管理员可以执行此操作")

        if msg.raw_message[:5] == "移除管理员":
            if self.NSFW_CONFIG.is_global_admin(msg.user_id):
                for user in msg.message:
                    if user["type"] == "at":
                        self.NSFW_CONFIG.remove_group_admin(
                            msg.group_id, int(user["data"]["qq"])
                        )

                await msg.reply(text="管理员移除成功")
            else:
                await msg.reply(text="只有全局管理员可以执行此操作")

        if msg.raw_message == "查看管理员":
            if self.NSFW_CONFIG.is_global_admin(msg.user_id):
                admins = self.NSFW_CONFIG.get_group_admins(msg.group_id)
                global_admins = self.NSFW_CONFIG.get_global_admins()

                content = [Text("管理员列表：\n全局管理员：")]
                for admin in global_admins:
                    content.append(At(admin))

                group_only_admins = [
                    admin for admin in admins if admin not in global_admins
                ]
                if group_only_admins:
                    content.append(Text("\n群组管理员："))
                    for admin in group_only_admins:
                        content.append(At(admin))

                await msg.reply(rtf=MessageChain(content))
            else:
                await msg.reply(text="只有全局管理员可以查看管理员列表")

        # 通知人管理功能（管理员和全局管理员可用）
        if msg.raw_message[:5] == "添加通知人":
            if self.NSFW_CONFIG.can_manage_notifiers(msg.user_id, msg.group_id):
                for user in msg.message:
                    if user["type"] == "at":
                        self.NSFW_CONFIG.add_group_notifier(
                            msg.group_id, int(user["data"]["qq"])
                        )

                await msg.reply(text="通知人添加成功")
            else:
                await msg.reply(text="你没有权限执行此操作")

        if msg.raw_message[:5] == "移除通知人":
            if self.NSFW_CONFIG.can_manage_notifiers(msg.user_id, msg.group_id):
                for user in msg.message:
                    if user["type"] == "at":
                        self.NSFW_CONFIG.remove_group_notifier(
                            msg.group_id, int(user["data"]["qq"])
                        )

                await msg.reply(text="通知人移除成功")
            else:
                await msg.reply(text="你没有权限执行此操作")

        # 开关控制功能（管理员和全局管理员可用）
        if msg.raw_message == "关闭nsfw检测":
            if self.NSFW_CONFIG.can_manage_settings(msg.user_id, msg.group_id):
                current_notifiers = self.NSFW_CONFIG.get_group_notifiers(msg.group_id)
                self.NSFW_CONFIG.update_group_settings(
                    msg.group_id, current_notifiers, False
                )

                await msg.reply(text="关闭成功")
            else:
                await msg.reply(text="你没有权限执行此操作")

        if msg.raw_message == "开启nsfw检测":
            if self.NSFW_CONFIG.can_manage_settings(msg.user_id, msg.group_id):
                current_notifiers = self.NSFW_CONFIG.get_group_notifiers(msg.group_id)
                self.NSFW_CONFIG.update_group_settings(
                    msg.group_id, current_notifiers, True
                )

                await msg.reply(text="开启成功")
            else:
                await msg.reply(text="你没有权限执行此操作")

        # 查看功能（全局管理员可用）
        if msg.raw_message == "查看通知人":
            if self.NSFW_CONFIG.is_global_admin(msg.user_id):
                notifiers = self.NSFW_CONFIG.get_group_notifiers(msg.group_id)
                content = [Text("通知人列表：")]
                for notifier in notifiers:
                    at = At(notifier)
                    content.append(at)
                await msg.reply(rtf=MessageChain(content))
            else:
                await msg.reply(text="只有全局管理员可以查看通知人列表")

        # 全局设置功能（仅全局管理员可用）
        if msg.raw_message.startswith("设置全局阈值"):
            if self.NSFW_CONFIG.is_global_admin(msg.user_id):
                try:
                    value = float(msg.raw_message.split()[1])
                    if 0 <= value <= 1:
                        self.NSFW_CONFIG.threshold = value  # 配置类内部会自动保存和刷新
                        await msg.reply(text=f"全局阈值已设置为: {value}")
                    else:
                        await msg.reply(text="阈值必须在0到1之间")
                except (IndexError, ValueError):
                    await msg.reply(
                        text="格式错误！请使用'设置全局阈值 0.x'的格式，例如：设置全局阈值 0.6"
                    )
            else:
                await msg.reply(text="只有全局管理员可以设置全局阈值")

        # 群组阈值设置功能（管理员和全局管理员可用）
        if msg.raw_message.startswith("设置阈值"):
            if self.NSFW_CONFIG.can_manage_settings(msg.user_id, msg.group_id):
                try:
                    value = float(msg.raw_message.split()[1])
                    if 0 <= value <= 1:
                        self.NSFW_CONFIG.set_group_threshold(
                            msg.group_id, value
                        )  # 配置类内部会自动保存
                        await msg.reply(text=f"本群阈值已设置为: {value}")
                    else:
                        await msg.reply(text="阈值必须在0到1之间")
                except (IndexError, ValueError):
                    await msg.reply(
                        text="格式错误！请使用'设置阈值 0.x'的格式，例如：设置阈值 0.6"
                    )
            else:
                await msg.reply(text="你没有权限执行此操作")

        if msg.raw_message == "重置阈值":
            if self.NSFW_CONFIG.can_manage_settings(msg.user_id, msg.group_id):
                self.NSFW_CONFIG.remove_group_threshold(
                    msg.group_id
                )  # 配置类内部会自动保存
                global_threshold = self.NSFW_CONFIG.threshold
                await msg.reply(
                    text=f"本群阈值已重置，将使用全局阈值: {global_threshold}"
                )
            else:
                await msg.reply(text="你没有权限执行此操作")

        if msg.raw_message == "查看阈值":
            current_threshold = self.NSFW_CONFIG.get_group_threshold(msg.group_id)
            has_custom = self.NSFW_CONFIG.has_group_threshold(msg.group_id)
            if has_custom:
                await msg.reply(text=f"当前本群阈值为: {current_threshold}")
            else:
                await msg.reply(text=f"当前阈值为: {current_threshold} (使用全局阈值)")

        if msg.raw_message.startswith("切换模型"):
            if self.NSFW_CONFIG.is_global_admin(msg.user_id):
                try:
                    model_type = msg.raw_message.split()[1]
                    if model_type in ["d", "m2", "i3"]:
                        self.NSFW_CONFIG.nsfwpy_type = (
                            model_type  # 配置类内部会自动保存和刷新
                        )
                        await msg.reply(text=f"模型已切换为: {model_type}")
                    else:
                        await msg.reply(text="模型类型必须是 d、m2 或 i3 之一")
                except IndexError:
                    await msg.reply(
                        text="格式错误！请使用'切换模型 类型'的格式，例如：切换模型 m2"
                    )
            else:
                await msg.reply(text="只有全局管理员可以切换模型")

        # 手动刷新配置命令
        if msg.raw_message == "刷新配置":
            if self.NSFW_CONFIG.is_global_admin(msg.user_id):
                self.NSFW_CONFIG.manual_refresh()
                await msg.reply(text="配置已刷新")
            else:
                await msg.reply(text="只有全局管理员可以刷新配置")

        # 手动保存配置命令
        if msg.raw_message == "保存配置":
            if self.NSFW_CONFIG.is_global_admin(msg.user_id):
                if self.NSFW_CONFIG.manual_save():
                    await msg.reply(text="配置已保存")
                else:
                    await msg.reply(text="配置无变更，无需保存")
            else:
                await msg.reply(text="只有全局管理员可以保存配置")

        # 其他消息处理
        if msg.raw_message.startswith("测试"):
            await msg.reply(text="测试消息已接收")

        if msg.raw_message.startswith("debug"):
            if self.NSFW_CONFIG.is_global_admin(msg.user_id):
                await msg.reply(text=f"当前配置：{self.NSFW_CONFIG.data}")
            else:
                await msg.reply(text="只有全局管理员可以查看调试信息")

    async def on_load(self):
        # 初始化配置
        self.NSFW_CONFIG.init_data(self.config)

        # 设置配置类的回调函数
        self.NSFW_CONFIG.set_callbacks(
            save_callback=self.save_config, refresh_callback=self.reload_config
        )

        # 检查是否存在管理员，如果没有则添加ROOT用户为全局管理员
        if not self.NSFW_CONFIG.has_global_admins():
            if self.ROOT is not None:
                self.NSFW_CONFIG.add_global_admin(self.ROOT)

                _log.info(f"已添加ROOT用户为全局管理员: {self.ROOT}")
            else:
                _log.warning("未检测到ROOT配置")
                try:
                    admin_qq = int(
                        input("YmNsfwPy 需要配置插件管理员QQ，请输入管理员QQ: ").strip()
                    )
                    self.NSFW_CONFIG.add_global_admin(admin_qq)

                    _log.info(f"已添加管理员: {admin_qq}")
                except ValueError:
                    _log.error("输入的QQ号格式不正确")
                    raise SystemExit("程序已终止：需要正确的管理员QQ号才能继续运行")

        _log.info(f"当前nsfwpybot管理员： {self.NSFW_CONFIG.get_global_admins()}")

    async def on_close(self, *arg, **kwd):
        # 清理配置类资源并保存
        await self.NSFW_CONFIG.cleanup()
