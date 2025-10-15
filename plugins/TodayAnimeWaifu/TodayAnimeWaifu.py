import os
import random
from datetime import date
from typing import Dict, Set

from common.constants.HMMT import HMMT
from common.entity.GroupMember import GroupMember
from common.utils.CommonUtil import CommonUtil
from ncatbot.core import At, Image as ImageElement, MessageChain, Text, GroupMessage
from ncatbot.plugin_system import NcatBotPlugin, on_message

from ncatbot.utils.logger import get_log


_log = get_log()

# 二次元老婆图片目录路径
ANIME_WAIFU_IMG_DIR = r"D:\IDEA\wife\img1"
ANIME_WAIFU_IMG_DIR_3 = r"D:\IDEA\wife\img3"

# 特殊用户ID


class TodayAnimeWaifu(NcatBotPlugin):
    name = "TodayAnimeWaifu"  # 插件名称
    version = "1.0"  # 插件版本

    allocated_waifus_by_group: Dict[int, Set[str]] = {}
    allocated_waifus_3_by_group: Dict[int, Set[str]] = {}
    user_to_waifu_map_by_group: Dict[int, Dict[int, str]] = {}
    last_reset_date = date.today()  # 记录上一次重置的日期

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.waifu_images = self._load_waifu_images()
        self.waifu_images_3 = self._load_waifu_images_3()

    def _load_waifu_images(self):
        """加载二次元老婆图片列表"""
        waifu_images = []
        if os.path.exists(ANIME_WAIFU_IMG_DIR):
            for filename in os.listdir(ANIME_WAIFU_IMG_DIR):
                if filename.lower().endswith(
                    (".png", ".jpg", ".jpeg", ".gif", ".webp")
                ):
                    waifu_images.append(filename)
        return waifu_images

    def _load_waifu_images_3(self):
        """加载特殊二次元老婆图片列表"""
        waifu_images = []
        if os.path.exists(ANIME_WAIFU_IMG_DIR_3):
            for filename in os.listdir(ANIME_WAIFU_IMG_DIR_3):
                if filename.lower().endswith(
                    (".png", ".jpg", ".jpeg", ".gif", ".webp")
                ):
                    waifu_images.append(filename)
        return waifu_images

    def reset_all_allocated_waifus(self):
        """重置所有已分配的二次元老婆"""
        self.allocated_waifus_by_group.clear()
        self.allocated_waifus_3_by_group.clear()
        self.user_to_waifu_map_by_group.clear()

    def get_random_waifu(self, group_id: int, user_id: int):
        """随机获取一个二次元老婆"""
        allocated_waifu_images = self.allocated_waifus_by_group.setdefault(
            group_id, set()
        )
        allocated_waifu_images_3 = self.allocated_waifus_3_by_group.setdefault(
            group_id, set()
        )

        # 特殊用户100%进入img3
        if user_id == HMMT.HMMT_ID:
            # 过滤掉已分配的特殊二次元老婆
            available_waifus_3 = [
                waifu
                for waifu in self.waifu_images_3
                if waifu not in allocated_waifu_images_3
            ]

            if not available_waifus_3:
                # 如果没有可用的特殊二次元老婆，重置分配
                allocated_waifu_images_3.clear()
                available_waifu_images_3 = self.waifu_images_3.copy()
                available_waifus_3 = [
                    waifu
                    for waifu in available_waifu_images_3
                    if waifu not in allocated_waifu_images_3
                ]

            if not available_waifus_3:
                # 如果img3没有可用的，回退到img1
                available_waifus = [
                    waifu
                    for waifu in self.waifu_images
                    if waifu not in allocated_waifu_images
                ]
                if not available_waifus:
                    allocated_waifu_images.clear()
                    available_waifus = self.waifu_images.copy()

                if not available_waifus:
                    return None

                selected_waifu = random.choice(available_waifus)
                allocated_waifu_images.add(selected_waifu)
                return {"filename": selected_waifu, "directory": "img1"}

            selected_waifu = random.choice(available_waifus_3)
            allocated_waifu_images_3.add(selected_waifu)
            return {"filename": selected_waifu, "directory": "img3"}

        # 普通用户概率分配
        # 20%概率进入img3，80%概率进入img1
        rand = random.random()

        if rand < 0.2:  # 20%概率进入img3
            # 过滤掉已分配的特殊二次元老婆
            available_waifus_3 = [
                waifu
                for waifu in self.waifu_images_3
                if waifu not in allocated_waifu_images_3
            ]

            if not available_waifus_3:
                # 如果img3没有可用的，回退到img1
                available_waifus = [
                    waifu
                    for waifu in self.waifu_images
                    if waifu not in allocated_waifu_images
                ]
                if not available_waifus:
                    allocated_waifu_images.clear()
                    available_waifus = self.waifu_images.copy()

                if not available_waifus:
                    return None

                selected_waifu = random.choice(available_waifus)
                allocated_waifu_images.add(selected_waifu)
                return {"filename": selected_waifu, "directory": "img1"}

            selected_waifu = random.choice(available_waifus_3)
            allocated_waifu_images_3.add(selected_waifu)
            return {"filename": selected_waifu, "directory": "img3"}
        else:  # 80%概率进入img1
            # 过滤掉已分配的二次元老婆
            available_waifus = [
                waifu
                for waifu in self.waifu_images
                if waifu not in allocated_waifu_images
            ]

            if not available_waifus:
                # 如果没有可用的二次元老婆，重置分配
                allocated_waifu_images.clear()
                available_waifu_images = self.waifu_images.copy()
                available_waifus = [
                    waifu
                    for waifu in available_waifu_images
                    if waifu not in allocated_waifu_images
                ]

            if not available_waifus:
                return None

            selected_waifu = random.choice(available_waifus)
            allocated_waifu_images.add(selected_waifu)
            return {"filename": selected_waifu, "directory": "img1"}

    def get_waifu_name(self, filename: str) -> str:
        """从文件名获取二次元老婆名字"""
        # 移除文件扩展名
        name = os.path.splitext(filename)[0]
        return name

    @on_message
    async def handle_message(self, input: GroupMessage):
        if not input.message:
            return
        """处理消息"""

        # 适配新的 MessageArray 结构
        message = ""
        for msg_segment in input.message:
            if hasattr(msg_segment, "text"):
                message = msg_segment.text
                break
        user_id = input.sender.user_id
        group_id = input.group_id

        # 检查日期是否已经跨天，如果是，则执行重置操作
        current_date = date.today()
        if current_date != self.last_reset_date:
            self.reset_all_allocated_waifus()
            self.last_reset_date = current_date

        # 触发指令列表
        trigger_commands = [
            "今日二次元老婆",
            "今日二刺猿老婆",
            "今日二刺螈老婆",
            "今日2次元老婆",
            "今日二次元",
            "今日二刺猿",
            "今日二刺螈",
        ]

        if message in trigger_commands:
            user_to_waifu_map = self.user_to_waifu_map_by_group.setdefault(group_id, {})
            allocated_waifus = self.allocated_waifus_by_group.setdefault(
                group_id, set()
            )

            # 检查当前用户是否已经有二次元老婆
            if user_id in user_to_waifu_map:
                waifu_data = user_to_waifu_map[user_id]
                if isinstance(waifu_data, dict):
                    waifu_filename = waifu_data["filename"]
                    directory = waifu_data["directory"]
                    if directory == "img3":
                        waifu_path = os.path.join(ANIME_WAIFU_IMG_DIR_3, waifu_filename)
                    else:
                        waifu_path = os.path.join(ANIME_WAIFU_IMG_DIR, waifu_filename)
                else:
                    # 兼容旧数据格式
                    waifu_filename = waifu_data
                    waifu_path = os.path.join(ANIME_WAIFU_IMG_DIR, waifu_filename)

                waifu_name = self.get_waifu_name(waifu_filename)

                return await self.api.post_group_msg(
                    group_id=input.group_id,
                    rtf=MessageChain(
                        [
                            At(user_id),
                            Text("你今天的二次元老婆是："),
                            ImageElement(waifu_path),
                            Text(f" {waifu_name}"),
                        ]
                    ),
                )

            new_waifu_data = self.get_random_waifu(group_id, user_id)
            if not new_waifu_data:
                return await self.api.post_group_msg(
                    group_id=input.group_id, text="获取二次元老婆失败，请稍后再试。"
                )

            user_to_waifu_map[user_id] = new_waifu_data
            new_waifu_filename = new_waifu_data["filename"]
            directory = new_waifu_data["directory"]

            if directory == "img3":
                allocated_waifus_3 = self.allocated_waifus_3_by_group.setdefault(
                    group_id, set()
                )
                allocated_waifus_3.add(new_waifu_filename)
                waifu_path = os.path.join(ANIME_WAIFU_IMG_DIR_3, new_waifu_filename)
            else:
                allocated_waifus.add(new_waifu_filename)
                waifu_path = os.path.join(ANIME_WAIFU_IMG_DIR, new_waifu_filename)

            waifu_name = self.get_waifu_name(new_waifu_filename)

            # 发送消息
            return await self.api.post_group_msg(
                group_id=input.group_id,
                rtf=MessageChain(
                    [
                        At(user_id),
                        Text(" 你今天的二次元老婆是："),
                        ImageElement(waifu_path),
                        Text(f" {waifu_name}"),
                    ]
                ),
            )

        # 换一个二次元老婆功能
        if (
            input.raw_message.startswith("换")
            and "的二次元老婆" in input.raw_message
            and (user_id == HMMT.HMMT_ID or user_id == "3860435136")
        ):
            target_user_id = None
            new_waifu_filename = None
            at_count = 0

            for isAt in input.message:
                if hasattr(isAt, "msg_seg_type") and isAt.msg_seg_type == "at":
                    if at_count == 0:
                        target_user_id = int(isAt.qq)
                    elif at_count == 1:
                        # 这里可以扩展为指定特定的二次元老婆
                        pass
                    at_count += 1

            if not target_user_id:
                return await self.api.post_group_msg(
                    group_id=input.group_id,
                    text="请艾特要更换二次元老婆的用户。",
                )

            # 获取目标用户信息
            target_info = await self.api.get_group_member_info(
                group_id=group_id, user_id=target_user_id, no_cache=True
            )
            if not isinstance(target_info, dict) or target_info.get("status") != "ok":
                return await self.api.post_group_msg(
                    group_id=input.group_id, text="获取目标用户信息失败，请稍后再试。"
                )
            target_data = target_info.get("data", {})
            if not target_data:
                return await self.api.post_group_msg(
                    group_id=input.group_id, text="获取目标用户信息失败，请稍后再试。"
                )
            target_member = GroupMember(target_data)

            user_to_waifu_map = self.user_to_waifu_map_by_group.setdefault(group_id, {})
            allocated_waifus = self.allocated_waifus_by_group.setdefault(
                group_id, set()
            )

            # 如果目标用户已经有二次元老婆，从已分配集合中移除
            if target_user_id in user_to_waifu_map:
                old_waifu_data = user_to_waifu_map[target_user_id]
                if isinstance(old_waifu_data, dict):
                    old_waifu_filename = old_waifu_data["filename"]
                    old_directory = old_waifu_data["directory"]
                else:
                    old_waifu_filename = old_waifu_data
                    old_directory = "img1"

                if old_directory == "img3":
                    allocated_waifus_3 = self.allocated_waifus_3_by_group.setdefault(
                        group_id, set()
                    )
                    allocated_waifus_3.discard(old_waifu_filename)
                else:
                    allocated_waifus.discard(old_waifu_filename)

            # 分配新的二次元老婆
            new_waifu_data = self.get_random_waifu(group_id, user_id)
            if not new_waifu_data:
                return await self.api.post_group_msg(
                    group_id=input.group_id, text="获取新的二次元老婆失败，请稍后再试。"
                )

            user_to_waifu_map[target_user_id] = new_waifu_data
            new_waifu_filename = new_waifu_data["filename"]
            directory = new_waifu_data["directory"]

            if directory == "img3":
                allocated_waifus_3 = self.allocated_waifus_3_by_group.setdefault(
                    group_id, set()
                )
                allocated_waifus_3.add(new_waifu_filename)
            else:
                allocated_waifus.add(new_waifu_filename)

            new_waifu_name = self.get_waifu_name(new_waifu_filename)

            return await self.api.post_group_msg(
                group_id=input.group_id,
                rtf=MessageChain(
                    [
                        At(user_id),
                        Text(
                            f" 成功将 {target_member.nickname} 的二次元老婆更换为 {new_waifu_name}"
                        ),
                    ]
                ),
            )

        # 换一个二次元老婆功能（管理员专用）
        if message == "换一个二次元老婆" and user_id == HMMT.HMMT_ID:
            user_to_waifu_map = self.user_to_waifu_map_by_group.setdefault(group_id, {})
            allocated_waifus = self.allocated_waifus_by_group.setdefault(
                group_id, set()
            )

            # 检查是否有艾特消息
            target_user_id = None
            for msg in input.message:
                if hasattr(msg, "msg_seg_type") and msg.msg_seg_type == "at":
                    target_user_id = int(msg.qq)
                    break

            if target_user_id:
                # 获取被艾特用户的信息
                target_info = await self.api.get_group_member_info(
                    group_id=group_id, user_id=target_user_id, no_cache=True
                )
                if isinstance(target_info, dict) and target_info.get("status") == "ok":
                    target_data = target_info.get("data", {})
                    if target_data:
                        target_member = GroupMember(target_data)

                        # 如果目标用户已经有二次元老婆，从已分配集合中移除
                        if target_user_id in user_to_waifu_map:
                            old_waifu_data = user_to_waifu_map[target_user_id]
                            if isinstance(old_waifu_data, dict):
                                old_waifu_filename = old_waifu_data["filename"]
                                old_directory = old_waifu_data["directory"]
                            else:
                                old_waifu_filename = old_waifu_data
                                old_directory = "img1"

                            if old_directory == "img3":
                                allocated_waifus_3 = (
                                    self.allocated_waifus_3_by_group.setdefault(
                                        group_id, set()
                                    )
                                )
                                allocated_waifus_3.discard(old_waifu_filename)
                            else:
                                allocated_waifus.discard(old_waifu_filename)

                        # 分配新的二次元老婆
                        new_waifu_data = self.get_random_waifu(group_id, user_id)
                        if not new_waifu_data:
                            return await self.api.post_group_msg(
                                group_id=input.group_id,
                                text="获取新的二次元老婆失败，请稍后再试。",
                            )

                        user_to_waifu_map[target_user_id] = new_waifu_data
                        new_waifu_filename = new_waifu_data["filename"]
                        directory = new_waifu_data["directory"]

                        if directory == "img3":
                            allocated_waifus_3 = (
                                self.allocated_waifus_3_by_group.setdefault(
                                    group_id, set()
                                )
                            )
                            allocated_waifus_3.add(new_waifu_filename)
                        else:
                            allocated_waifus.add(new_waifu_filename)

                        new_waifu_name = self.get_waifu_name(new_waifu_filename)

                        return await self.api.post_group_msg(
                            group_id=input.group_id,
                            rtf=MessageChain(
                                [
                                    At(user_id),
                                    Text(
                                        f" 成功更换了 {target_member.nickname} 的二次元老婆，新老婆是：{new_waifu_name}"
                                    ),
                                ]
                            ),
                        )
                return await self.api.post_group_msg(
                    group_id=input.group_id, text="获取目标用户信息失败，请稍后再试。"
                )

            # 原有的随机抽取逻辑
            if user_id not in user_to_waifu_map:
                return await self.api.post_group_msg(
                    group_id=input.group_id,
                    text="你还没有二次元老婆，无法换一个二次元老婆。",
                )

            current_waifu_data = user_to_waifu_map[user_id]
            if isinstance(current_waifu_data, dict):
                current_waifu_filename = current_waifu_data["filename"]
                current_directory = current_waifu_data["directory"]
            else:
                current_waifu_filename = current_waifu_data
                current_directory = "img1"

            if current_directory == "img3":
                allocated_waifus_3 = self.allocated_waifus_3_by_group.setdefault(
                    group_id, set()
                )
                if current_waifu_filename in allocated_waifus_3:
                    allocated_waifus_3.remove(current_waifu_filename)
            else:
                if current_waifu_filename in allocated_waifus:
                    allocated_waifus.remove(current_waifu_filename)

            user_to_waifu_map.pop(user_id)

            new_waifu_data = self.get_random_waifu(group_id, user_id)
            if new_waifu_data is None:
                return await self.api.post_group_msg(
                    group_id=input.group_id, text="无法获取新的二次元老婆，请稍后再试。"
                )

            user_to_waifu_map[user_id] = new_waifu_data
            new_waifu_filename = new_waifu_data["filename"]
            directory = new_waifu_data["directory"]

            if directory == "img3":
                allocated_waifus_3 = self.allocated_waifus_3_by_group.setdefault(
                    group_id, set()
                )
                allocated_waifus_3.add(new_waifu_filename)
            else:
                allocated_waifus.add(new_waifu_filename)

            new_waifu_name = self.get_waifu_name(new_waifu_filename)

            return await self.api.post_group_msg(
                group_id=input.group_id,
                rtf=MessageChain(
                    [
                        At(user_id),
                        Text(f" 成功更换了二次元老婆，你的新老婆是：{new_waifu_name}"),
                    ]
                ),
            )
