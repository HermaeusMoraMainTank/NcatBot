import base64
import io
import os
import random
from datetime import date
from typing import Dict, List, Set, Tuple

from PIL import Image as PILImage, ImageDraw, ImageFont

from common.constants.HMMT import HMMT
from common.entity.GroupMember import GroupMember
from common.utils.CommonUtil import CommonUtil
from ncatbot.event.qq import GroupMessageEvent as GroupMessage
from ncatbot.types import At, Image, MessageArray as MessageChain, PlainText
from ncatbot.plugin import NcatBotPlugin
from ncatbot.core import registrar

from ncatbot.utils import get_log


_log = get_log()

_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp")

# 二次元老婆图片根目录
ANIME_WAIFU_BASE_DIR = r"C:\Users\Administrator\Documents\GitHub\wife"
PREFERRED_WAIFU_DIR = "img1"
PREFERRED_DIR_CHANCE = 0.30
HMMT_WAIFU_DIR = "img3"


class TodayAnimeWaifu(NcatBotPlugin):
    name = "TodayAnimeWaifu"  # 插件名称
    version = "1.2"  # 插件版本

    allocated_waifus_by_group: Dict[int, Set[str]] = {}
    user_to_waifu_map_by_group: Dict[int, Dict[int, dict]] = {}
    last_reset_date = date.today()  # 记录上一次重置的日期

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.waifu_images_by_dir = self._load_waifu_images_all()

    def _list_image_dirs(self) -> List[str]:
        if not os.path.isdir(ANIME_WAIFU_BASE_DIR):
            return []
        dirs: List[str] = []
        for name in sorted(os.listdir(ANIME_WAIFU_BASE_DIR)):
            path = os.path.join(ANIME_WAIFU_BASE_DIR, name)
            if os.path.isdir(path) and not name.startswith("."):
                dirs.append(name)
        return dirs

    def _load_waifu_images_all(self) -> Dict[str, List[str]]:
        images_by_dir: Dict[str, List[str]] = {}
        for dir_name in self._list_image_dirs():
            dir_path = os.path.join(ANIME_WAIFU_BASE_DIR, dir_name)
            files = [
                filename
                for filename in os.listdir(dir_path)
                if filename.lower().endswith(_IMAGE_EXTS)
            ]
            if files:
                images_by_dir[dir_name] = files
        return images_by_dir

    def reset_all_allocated_waifus(self):
        """重置所有已分配的二次元老婆"""
        self.allocated_waifus_by_group.clear()
        self.user_to_waifu_map_by_group.clear()
        self.waifu_images_by_dir = self._load_waifu_images_all()

    @staticmethod
    def _waifu_slot(directory: str, filename: str) -> str:
        return f"{directory}/{filename}"

    @classmethod
    def _parse_waifu_data(cls, data) -> tuple[str, str]:
        if isinstance(data, dict):
            return data.get("directory", PREFERRED_WAIFU_DIR), data["filename"]
        return PREFERRED_WAIFU_DIR, data

    def _allocated(self, group_id: int) -> Set[str]:
        return self.allocated_waifus_by_group.setdefault(group_id, set())

    def _release_waifu(self, group_id: int, waifu_data) -> None:
        directory, filename = self._parse_waifu_data(waifu_data)
        self._allocated(group_id).discard(self._waifu_slot(directory, filename))

    def _claim_waifu(self, group_id: int, waifu_data: dict) -> None:
        self._allocated(group_id).add(
            self._waifu_slot(waifu_data["directory"], waifu_data["filename"])
        )

    def _available_candidates(
        self, group_id: int, *, include_dirs: List[str] | None = None
    ) -> List[tuple[str, str]]:
        allocated = self._allocated(group_id)
        candidates: List[tuple[str, str]] = []
        for directory, filenames in self.waifu_images_by_dir.items():
            if include_dirs is not None and directory not in include_dirs:
                continue
            for filename in filenames:
                slot = self._waifu_slot(directory, filename)
                if slot not in allocated and self._waifu_file_exists(
                    directory, filename
                ):
                    candidates.append((directory, filename))
        return candidates

    def _reset_allocation_if_needed(self, group_id: int) -> None:
        total = sum(len(files) for files in self.waifu_images_by_dir.values())
        if total and len(self._allocated(group_id)) >= total:
            self._allocated(group_id).clear()

    def _pick_random_candidate(
        self, group_id: int, candidates: List[tuple[str, str]]
    ) -> dict | None:
        if not candidates:
            self._reset_allocation_if_needed(group_id)
            candidates = self._available_candidates(group_id)
        if not candidates:
            return None
        directory, filename = random.choice(candidates)
        waifu_data = {"filename": filename, "directory": directory}
        self._claim_waifu(group_id, waifu_data)
        return waifu_data

    def get_random_waifu(self, group_id: int, user_id: int):
        """随机获取一个二次元老婆"""
        if not self.waifu_images_by_dir:
            return None

        preferred_dir = (
            HMMT_WAIFU_DIR if str(user_id) == HMMT.HMMT_ID else PREFERRED_WAIFU_DIR
        )
        preferred = self._available_candidates(group_id, include_dirs=[preferred_dir])
        others = self._available_candidates(
            group_id,
            include_dirs=[d for d in self.waifu_images_by_dir if d != preferred_dir],
        )

        if random.random() < PREFERRED_DIR_CHANCE:
            pool = preferred if preferred else others
        else:
            pool = others if others else preferred

        return self._pick_random_candidate(group_id, pool)

    def get_waifu_name(self, filename: str) -> str:
        """从文件名获取二次元老婆名字"""
        return os.path.splitext(filename)[0]

    def _build_waifu_path(self, directory: str, filename: str) -> str:
        return os.path.join(ANIME_WAIFU_BASE_DIR, directory, filename)

    def _waifu_file_exists(self, directory: str, filename: str) -> bool:
        return os.path.isfile(self._build_waifu_path(directory, filename))

    def _assign_random_waifu(
        self,
        group_id: int,
        user_id: int,
        user_to_waifu_map: dict,
        *,
        map_key: int | None = None,
        max_attempts: int = 5,
    ) -> tuple[dict, str] | None:
        target_key = map_key if map_key is not None else user_id
        for _ in range(max_attempts):
            waifu_data = self.get_random_waifu(group_id, user_id)
            if not waifu_data:
                return None
            directory = waifu_data["directory"]
            filename = waifu_data["filename"]
            waifu_path = self._build_waifu_path(directory, filename)
            if self._waifu_file_exists(directory, filename):
                user_to_waifu_map[target_key] = waifu_data
                return waifu_data, waifu_path
            self._release_waifu(group_id, waifu_data)
            _log.warning(
                "二次元老婆图片不存在: %s/%s，已释放并重新抽取",
                directory,
                filename,
            )
        return None

    def _release_user_waifu(
        self, group_id: int, user_id: int, user_to_waifu_map: dict
    ) -> None:
        waifu_data = user_to_waifu_map.pop(user_id, None)
        if waifu_data is not None:
            self._release_waifu(group_id, waifu_data)

    def _resolve_waifu_path(self, waifu_data) -> tuple[str, str, str]:
        directory, filename = self._parse_waifu_data(waifu_data)
        return directory, filename, self._build_waifu_path(directory, filename)

    @staticmethod
    def _display_name(member: GroupMember | None, fallback_id: str) -> str:
        if member is None:
            return fallback_id
        return member.card or member.nickname or str(member.user_id)

    def _load_font(self, size: int):
        for path in ("data/font/sakura.ttf", "simhei.ttf"):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
        return ImageFont.load_default()

    @staticmethod
    def _truncate_line(
        draw: ImageDraw.ImageDraw, text: str, font, max_width: int
    ) -> str:
        if not text:
            return text
        bbox = draw.textbbox((0, 0), text, font=font)
        if bbox[2] - bbox[0] <= max_width:
            return text
        ellipsis = "…"
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            probe = text[:mid] + ellipsis
            probe_bbox = draw.textbbox((0, 0), probe, font=font)
            if probe_bbox[2] - probe_bbox[0] <= max_width:
                lo = mid
            else:
                hi = mid - 1
        return text[:lo] + ellipsis if lo > 0 else ellipsis

    def _generate_waifu_list_image(self, pairs: List[Tuple[str, str, str]]) -> str:
        width = 920
        padding = 28
        row_height = 42
        title_height = 56
        height = padding * 2 + title_height + len(pairs) * row_height + 16

        img = PILImage.new("RGB", (width, height), color=(255, 248, 252))
        draw = ImageDraw.Draw(img)

        title_font = self._load_font(28)
        text_font = self._load_font(20)
        meta_font = self._load_font(15)

        y = padding
        title = "今日群二次元老婆列表"
        title_bbox = draw.textbbox((0, 0), title, font=title_font)
        title_width = title_bbox[2] - title_bbox[0]
        draw.text(
            ((width - title_width) // 2, y),
            title,
            font=title_font,
            fill=(180, 120, 220),
        )
        y += title_height - 8
        draw.line(
            [(padding, y), (width - padding, y)],
            fill=(230, 220, 240),
            width=2,
        )
        y += 16

        inner_width = width - padding * 2
        for user_name, user_id, waifu_name in pairs:
            left = f"{user_name}（{user_id}）"
            arrow = " →→→ "
            right = waifu_name
            line = self._truncate_line(
                draw, f"{left}{arrow}{right}", text_font, inner_width
            )
            draw.text((padding, y), line, font=text_font, fill=(51, 58, 72))
            y += row_height

        footer = f"共 {len(pairs)} 人"
        footer_bbox = draw.textbbox((0, 0), footer, font=meta_font)
        footer_width = footer_bbox[2] - footer_bbox[0]
        draw.text(
            (width - padding - footer_width, y - 6),
            footer,
            font=meta_font,
            fill=(126, 136, 156),
        )

        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format="PNG")
        img_byte_arr.seek(0)
        img_base64 = base64.b64encode(img_byte_arr.getvalue()).decode()
        return f"base64://{img_base64}"

    async def _build_waifu_list_pairs(
        self, group_id: int
    ) -> List[Tuple[str, str, str]]:
        user_to_waifu_map = self.user_to_waifu_map_by_group.get(group_id, {})
        if not user_to_waifu_map:
            return []

        members_response = await self.api.qq.query.get_group_member_list(
            group_id=group_id
        )
        members = CommonUtil.parse_group_member_list(members_response)
        member_by_id = {str(m.user_id): m for m in members}

        pairs: List[Tuple[str, str, str]] = []
        for user_id, waifu_data in user_to_waifu_map.items():
            uid = str(user_id)
            if uid not in member_by_id:
                continue
            _, filename = self._parse_waifu_data(waifu_data)
            pairs.append(
                (
                    self._display_name(member_by_id[uid], uid),
                    uid,
                    self.get_waifu_name(filename),
                )
            )

        pairs.sort(key=lambda item: int(item[1]))
        return pairs

    @registrar.qq.on_group_message()
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

        command = input.raw_message.strip()
        if command == "群二次元老婆列表":
            pairs = await self._build_waifu_list_pairs(group_id)
            if not pairs:
                return await self.api.qq.post_group_msg(
                    group_id=input.group_id,
                    text="今日还没有人抽到二次元老婆哦~",
                )
            img_base64 = self._generate_waifu_list_image(pairs)
            return await self.api.qq.post_group_msg(
                group_id=input.group_id,
                rtf=MessageChain([Image(file=img_base64)]),
            )

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

            if user_id in user_to_waifu_map:
                waifu_data = user_to_waifu_map[user_id]
                directory, waifu_filename, waifu_path = self._resolve_waifu_path(
                    waifu_data
                )

                if not self._waifu_file_exists(directory, waifu_filename):
                    self._release_user_waifu(group_id, user_id, user_to_waifu_map)
                else:
                    waifu_name = self.get_waifu_name(waifu_filename)
                    return await self.api.qq.post_group_msg(
                        group_id=input.group_id,
                        rtf=MessageChain(
                            [
                                At(user_id=user_id),
                                PlainText(text="你今天的二次元老婆是："),
                                Image(file=waifu_path),
                                PlainText(text=f" {waifu_name}"),
                            ]
                        ),
                    )

            assigned = self._assign_random_waifu(group_id, user_id, user_to_waifu_map)
            if not assigned:
                return await self.api.qq.post_group_msg(
                    group_id=input.group_id, text="获取二次元老婆失败，请稍后再试。"
                )

            new_waifu_data, waifu_path = assigned
            waifu_name = self.get_waifu_name(new_waifu_data["filename"])

            return await self.api.qq.post_group_msg(
                group_id=input.group_id,
                rtf=MessageChain(
                    [
                        At(user_id=user_id),
                        PlainText(text=" 你今天的二次元老婆是："),
                        Image(file=waifu_path),
                        PlainText(text=f" {waifu_name}"),
                    ]
                ),
            )

        # 换一个二次元老婆功能
        if (
            input.raw_message.startswith("换")
            and "的二次元老婆" in input.raw_message
            and str(user_id) == HMMT.HMMT_ID
        ):
            target_user_id = None
            at_count = 0

            for isAt in input.message:
                if isinstance(isAt, At):
                    if at_count == 0:
                        target_user_id = int(isAt.user_id)
                    elif at_count == 1:
                        # 这里可以扩展为指定特定的二次元老婆
                        pass
                    at_count += 1

            if not target_user_id:
                return await self.api.qq.post_group_msg(
                    group_id=input.group_id,
                    text="请艾特要更换二次元老婆的用户。",
                )

            # 获取目标用户信息
            target_info = await self.api.qq.query.get_group_member_info(
                group_id=group_id, user_id=target_user_id, no_cache=True
            )
            if not isinstance(target_info, dict) or target_info.get("status") != "ok":
                return await self.api.qq.post_group_msg(
                    group_id=input.group_id, text="获取目标用户信息失败，请稍后再试。"
                )
            target_data = target_info.get("data", {})
            if not target_data:
                return await self.api.qq.post_group_msg(
                    group_id=input.group_id, text="获取目标用户信息失败，请稍后再试。"
                )
            target_member = GroupMember(target_data)

            user_to_waifu_map = self.user_to_waifu_map_by_group.setdefault(group_id, {})

            if target_user_id in user_to_waifu_map:
                self._release_user_waifu(group_id, target_user_id, user_to_waifu_map)

            assigned = self._assign_random_waifu(
                group_id,
                user_id,
                user_to_waifu_map,
                map_key=target_user_id,
            )
            if not assigned:
                return await self.api.qq.post_group_msg(
                    group_id=input.group_id, text="获取新的二次元老婆失败，请稍后再试。"
                )

            new_waifu_data, _ = assigned
            new_waifu_name = self.get_waifu_name(new_waifu_data["filename"])

            return await self.api.qq.post_group_msg(
                group_id=input.group_id,
                rtf=MessageChain(
                    [
                        At(user_id=user_id),
                        PlainText(
                            text=f" 成功将 {target_member.nickname} 的二次元老婆更换为 {new_waifu_name}"
                        ),
                    ]
                ),
            )

        # 换一个二次元老婆功能（管理员专用）
        if message == "换一个二次元老婆" and str(user_id) == HMMT.HMMT_ID:
            user_to_waifu_map = self.user_to_waifu_map_by_group.setdefault(group_id, {})

            target_user_id = None
            for msg in input.message:
                if isinstance(msg, At):
                    target_user_id = int(msg.user_id)
                    break

            if target_user_id:
                # 获取被艾特用户的信息
                target_info = await self.api.qq.query.get_group_member_info(
                    group_id=group_id, user_id=target_user_id, no_cache=True
                )
                if isinstance(target_info, dict) and target_info.get("status") == "ok":
                    target_data = target_info.get("data", {})
                    if target_data:
                        target_member = GroupMember(target_data)

                        self._release_user_waifu(
                            group_id, target_user_id, user_to_waifu_map
                        )

                        assigned = self._assign_random_waifu(
                            group_id,
                            user_id,
                            user_to_waifu_map,
                            map_key=target_user_id,
                        )
                        if not assigned:
                            return await self.api.qq.post_group_msg(
                                group_id=input.group_id,
                                text="获取新的二次元老婆失败，请稍后再试。",
                            )

                        new_waifu_data, _ = assigned
                        new_waifu_name = self.get_waifu_name(new_waifu_data["filename"])

                        return await self.api.qq.post_group_msg(
                            group_id=input.group_id,
                            rtf=MessageChain(
                                [
                                    At(user_id=user_id),
                                    PlainText(
                                        text=f" 成功更换了 {target_member.nickname} 的二次元老婆，新老婆是：{new_waifu_name}"
                                    ),
                                ]
                            ),
                        )
                return await self.api.qq.post_group_msg(
                    group_id=input.group_id, text="获取目标用户信息失败，请稍后再试。"
                )

            # 原有的随机抽取逻辑
            if user_id not in user_to_waifu_map:
                return await self.api.qq.post_group_msg(
                    group_id=input.group_id,
                    text="你还没有二次元老婆，无法换一个二次元老婆。",
                )

            self._release_user_waifu(group_id, user_id, user_to_waifu_map)

            assigned = self._assign_random_waifu(group_id, user_id, user_to_waifu_map)
            if not assigned:
                return await self.api.qq.post_group_msg(
                    group_id=input.group_id, text="无法获取新的二次元老婆，请稍后再试。"
                )

            new_waifu_data, _ = assigned
            new_waifu_name = self.get_waifu_name(new_waifu_data["filename"])

            return await self.api.qq.post_group_msg(
                group_id=input.group_id,
                rtf=MessageChain(
                    [
                        At(user_id=user_id),
                        PlainText(
                            text=f" 成功更换了二次元老婆，你的新老婆是：{new_waifu_name}"
                        ),
                    ]
                ),
            )
