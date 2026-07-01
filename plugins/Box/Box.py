import asyncio
import logging
import textwrap
from io import BytesIO
from pathlib import Path
from typing import List, Optional

from PIL import Image as PILImage

from ncatbot.event.qq import GroupMessageEvent as GroupMessage
from ncatbot.types import At, Image as NcatImage, MessageArray as MessageChain, PlainText
from ncatbot.plugin import NcatBotPlugin
from ncatbot.core import registrar

from .draw import CardMaker
from .field_mapping import FIELD_MAPPING, LABEL_TO_KEY, DEFAULT_DISPLAY_OPTIONS
from .utils import get_avatar, get_constellation, get_zodiac, render_digest

_log = logging.getLogger(__name__)


class Box(NcatBotPlugin):
    """开盒插件 - 获取QQ用户信息并以图片形式展示"""

    name = "Box"
    version = "1.0"

    # 缓存目录
    cache_dir: Path = Path("data/box/box_cache")
    # 保护名单 (可以添加不希望被开盒的用户ID)
    protect_ids: List[int] = [273421673]
    # 管理员列表 (有权限开盒他人的用户)
    admin_ids: List[int] = []
    # 卡片生成器
    renderer: Optional[CardMaker] = None
    # 撤回时间 (秒)，0表示不撤回
    recall_time: int = 10
    # 仅管理员可开盒他人
    only_admin: bool = False
    # 显示选项
    display_options: List[str] = DEFAULT_DISPLAY_OPTIONS
    # Bot自身的QQ号
    self_id: Optional[int] = None

    async def on_load(self):
        """插件加载时初始化"""
        _log.info(f"开始加载 {self.name} 插件 v{self.version}")

        # 创建缓存目录
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # 初始化卡片生成器
        try:
            self.renderer = CardMaker()
            _log.info("卡片生成器初始化成功")
        except Exception as e:
            _log.error(f"卡片生成器初始化失败: {e}")

        # 获取Bot自身ID
        try:
            login_info = await self.api.qq.query.get_login_info()
            self.self_id = login_info.get("data", {}).get("user_id")
            _log.info(f"Bot ID: {self.self_id}")
        except Exception as e:
            _log.warning(f"获取Bot ID失败: {e}")

        _log.info(f"{self.name} 插件加载完成")

    @registrar.qq.on_group_message()
    async def handle_box_command(self, input: GroupMessage):
        """处理开盒命令"""
        message = input.raw_message.strip()
        sender_id = input.sender.user_id
        group_id = input.group_id

        # 检查是否是开盒命令
        if not (message.startswith("盒") or message.startswith("开盒")):
            return

        # 解析命令
        command = message.replace("开盒", "").replace("盒", "").strip()

        # 检查权限 (仅管理员可开盒他人)
        if self.only_admin and sender_id not in self.admin_ids and command:
            return

        # 获取目标用户ID
        target_ids = self._get_target_ids(input, command)

        # 如果没有目标，则开盒自己
        if not target_ids:
            target_ids = [sender_id]

        # 对每个目标执行开盒
        for target_id in target_ids:
            await self._box_user(input, target_id, group_id)

    def _get_target_ids(self, input: GroupMessage, command: str) -> List[int]:
        """获取目标用户ID列表"""
        target_ids = []

        # 从消息中获取被@的用户
        for seg in input.message:
            if isinstance(seg, At):
                target_id = int(seg.user_id)
                # 过滤掉Bot自己和保护名单中的用户
                if target_id != self.self_id and target_id not in self.protect_ids:
                    target_ids.append(target_id)

        # 如果命令中包含QQ号
        if command.isdigit():
            target_id = int(command)
            if target_id not in self.protect_ids:
                target_ids.append(target_id)

        return target_ids

    async def _box_user(self, input: GroupMessage, target_id: int, group_id: int):
        """执行开盒操作"""
        # 获取用户信息
        stranger_info = {}
        member_info = {}

        try:
            # NcatBot5 返回 StrangerInfo（NapCatModel，Pydantic），可能携带 extra 字段
            result = await self.api.qq.query.get_stranger_info(user_id=target_id)
            if result:
                stranger_info = (
                    result.model_dump() if hasattr(result, "model_dump") else dict(result)
                )
            else:
                stranger_info = {}
            _log.debug(f"获取到的用户信息: {stranger_info}")
        except Exception as e:
            _log.warning(f"获取用户信息失败: {e}")
            await self.api.qq.post_group_msg(
                group_id=group_id, rtf=MessageChain([PlainText(text="无效QQ号")])
            )
            return

        try:
            # get_group_member_info 返回 GroupMemberInfo 对象，需要转换为 dict
            result = await self.api.qq.query.get_group_member_info(
                group_id=group_id, user_id=target_id
            )
            if result:
                # NcatBot5 返回的是 Pydantic 模型（NapCatModel），不是 dataclass
                member_info = (
                    result.model_dump() if hasattr(result, "model_dump") else dict(result)
                )
        except Exception as e:
            _log.warning(f"获取群成员信息失败: {e}")

        # 获取头像
        avatar = await get_avatar(str(target_id))
        if not avatar:
            # 头像获取失败，使用白图
            with BytesIO() as buffer:
                PILImage.new("RGB", (640, 640), (255, 255, 255)).save(
                    buffer, format="PNG"
                )
                avatar = buffer.getvalue()

        # 解析用户信息
        display = self._transform(stranger_info, member_info)

        if not display:
            await self.api.qq.post_group_msg(
                group_id=group_id,
                rtf=MessageChain([PlainText(text="获取用户信息失败或信息为空")]),
            )
            return

        # 检查缓存
        digest = render_digest(display, avatar)
        cache_name = f"{target_id}_{group_id}_{digest}.png"
        cache_path = self.cache_dir / cache_name

        if cache_path.exists():
            image_data = cache_path.read_bytes()
            _log.debug(f"命中缓存: {cache_path}")
        else:
            # 生成卡片图片
            if not self.renderer:
                await self.api.qq.post_group_msg(
                    group_id=group_id, rtf=MessageChain([PlainText(text="卡片生成器未初始化")])
                )
                return

            try:
                image_data = self.renderer.create(avatar, display)
                cache_path.write_bytes(image_data)
                _log.debug(f"写入缓存: {cache_path}")
            except Exception as e:
                _log.error(f"生成卡片失败: {e}")
                await self.api.qq.post_group_msg(
                    group_id=group_id, rtf=MessageChain([PlainText(text=f"生成卡片失败: {e}")])
                )
                return

        # 保存临时图片
        temp_path = self.cache_dir / f"temp_{target_id}.png"
        temp_path.write_bytes(image_data)

        # 发送消息
        message = MessageChain([NcatImage(file=str(temp_path))])

        if self.recall_time > 0:
            # 发送并设置撤回
            result = await self.api.qq.post_group_msg(group_id=group_id, rtf=message)
            if result and result.get("data", {}).get("message_id"):
                message_id = result["data"]["message_id"]
                asyncio.create_task(self._recall_message(message_id, self.recall_time))
        else:
            await self.api.qq.post_group_msg(group_id=group_id, rtf=message)

    async def _recall_message(self, message_id: int, delay: int):
        """延时撤回消息"""
        await asyncio.sleep(delay)
        try:
            await self.api.qq.manage.delete_msg(message_id=message_id)
            _log.info(f"已自动撤回消息: {message_id}")
        except Exception as e:
            _log.error(f"撤回消息失败: {e}")

    def _transform(self, info1: dict, info2: dict) -> List[str]:
        """根据映射表转换用户信息为显示列表"""
        reply: List[str] = []

        # 确保 info1 和 info2 是字典
        if not isinstance(info1, dict):
            info1 = {}
        if not isinstance(info2, dict):
            info2 = {}

        # 将 display_options 中的中文名转换为英文字段名集合
        enabled_keys = {
            LABEL_TO_KEY.get(label, label) for label in self.display_options
        }

        for field in FIELD_MAPPING:
            key = field["key"]
            label = field["label"]
            source = field.get("source", "info1")

            # 检查是否启用显示
            if key not in enabled_keys:
                continue

            # 处理计算字段
            if source == "computed":
                computed_lines = self._compute_field(key, label, info1, info2)
                if computed_lines:
                    reply.extend(computed_lines)
                continue

            # 获取原始值
            data = info1 if source == "info1" else info2
            value = data.get(key)

            # 跳过空值
            if not value:
                continue

            # 跳过特定值
            skip_values = field.get("skip_values", [])
            if value in skip_values:
                continue

            # 应用转换函数
            transform = field.get("transform")
            if transform:
                try:
                    value = transform(value)
                except Exception:
                    continue
                if not value:  # 转换后为空则跳过
                    continue

            # 添加后缀
            suffix = field.get("suffix", "")

            # 处理多行文本（如签名）
            if field.get("multiline"):
                wrap_width = field.get("wrap_width", 15)
                lines = textwrap.wrap(text=f"{label}：{value}", width=wrap_width)
                reply.extend(lines)
            else:
                reply.append(f"{label}：{value}{suffix}")

        return reply

    def _compute_field(
        self, key: str, label: str, info1: dict, info2: dict
    ) -> List[str]:
        """处理需要特殊计算的字段，返回行列表"""

        if key == "birthday":
            year = info1.get("birthday_year")
            month = info1.get("birthday_month")
            day = info1.get("birthday_day")
            if year and month and day:
                return [f"{label}：{year}-{month}-{day}"]
            return []

        if key == "constellation":
            month = info1.get("birthday_month")
            day = info1.get("birthday_day")
            if month and day:
                return [f"{label}：{get_constellation(int(month), int(day))}"]
            return []

        if key == "zodiac":
            year = info1.get("birthday_year")
            month = info1.get("birthday_month")
            day = info1.get("birthday_day")
            if year and month and day:
                return [f"{label}：{get_zodiac(int(year), int(month), int(day))}"]
            return []

        if key == "address":
            country = info1.get("country")
            province = info1.get("province")
            city = info1.get("city")

            if country == "中国" and (province or city):
                return [f"{label}：{province or ''}-{city or ''}"]
            elif country:
                return [f"{label}：{country}"]
            return []

        if key == "detail_address":
            address = info1.get("address")
            if address and address != "-":
                return [f"{label}：{address}"]
            return []

        return []

    async def on_unload(self):
        """插件卸载时清理"""
        # 可选：清空缓存目录
        # if self.cache_dir.exists():
        #     shutil.rmtree(self.cache_dir)
        _log.info(f"{self.name} 插件已卸载")