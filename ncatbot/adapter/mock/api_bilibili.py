"""
MockBiliAPI — IBiliAPIClient 的内存 Mock 实现
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ncatbot.api.bilibili import IBiliAPIClient
from ncatbot.types.bilibili import LiveRoomInfo

from .api_base import MockAPIBase


class MockBiliAPI(MockAPIBase, IBiliAPIClient):
    """IBiliAPIClient 的完整 Mock 实现 — 命名参数录制"""

    def __init__(self) -> None:
        super().__init__(platform="bilibili")

    # ---- 数据源管理 ----

    async def add_live_room(self, room_id: int) -> None:
        self._record("add_live_room", room_id=room_id)

    async def remove_live_room(self, room_id: int) -> None:
        self._record("remove_live_room", room_id=room_id)

    async def add_comment_watch(
        self,
        resource_id: str,
        resource_type: str = "video",
    ) -> None:
        self._record(
            "add_comment_watch", resource_id=resource_id, resource_type=resource_type
        )

    async def remove_comment_watch(self, resource_id: str) -> None:
        self._record("remove_comment_watch", resource_id=resource_id)

    async def list_sources(self) -> List[Dict[str, Any]]:
        return self._record("list_sources")

    # ---- 直播间操作 ----

    async def send_danmu(self, room_id: int, text: str) -> Any:
        return self._record("send_danmu", room_id=room_id, text=text)

    async def ban_user(self, room_id: int, user_id: int, hour: int = 1) -> Any:
        return self._record("ban_user", room_id=room_id, user_id=user_id, hour=hour)

    async def unban_user(self, room_id: int, user_id: int) -> Any:
        return self._record("unban_user", room_id=room_id, user_id=user_id)

    async def set_room_silent(self, room_id: int, enable: bool, **kwargs: Any) -> Any:
        return self._record("set_room_silent", room_id=room_id, enable=enable, **kwargs)

    async def get_room_info(self, room_id: int) -> Optional[LiveRoomInfo]:
        return self._record("get_room_info", room_id=room_id)

    # ---- 私信 ----

    async def send_private_msg(self, user_id: int, content: str) -> Any:
        return self._record("send_private_msg", user_id=user_id, content=content)

    async def send_private_image(self, user_id: int, image_url: str) -> Any:
        return self._record("send_private_image", user_id=user_id, image_url=image_url)

    async def get_session_history(self, user_id: int, count: int = 20) -> list:
        return self._record("get_session_history", user_id=user_id, count=count)

    # ---- 评论 ----

    async def send_comment(
        self,
        resource_id: str,
        resource_type: str,
        text: str,
    ) -> Any:
        return self._record(
            "send_comment",
            resource_id=resource_id,
            resource_type=resource_type,
            text=text,
        )

    async def reply_comment(
        self,
        resource_id: str,
        resource_type: str,
        root_id: int,
        parent_id: int,
        text: str,
    ) -> Any:
        return self._record(
            "reply_comment",
            resource_id=resource_id,
            resource_type=resource_type,
            root_id=root_id,
            parent_id=parent_id,
            text=text,
        )

    async def delete_comment(
        self,
        resource_id: str,
        resource_type: str,
        comment_id: int,
    ) -> Any:
        return self._record(
            "delete_comment",
            resource_id=resource_id,
            resource_type=resource_type,
            comment_id=comment_id,
        )

    async def like_comment(
        self,
        resource_id: str,
        resource_type: str,
        comment_id: int,
    ) -> Any:
        return self._record(
            "like_comment",
            resource_id=resource_id,
            resource_type=resource_type,
            comment_id=comment_id,
        )

    async def get_comments(
        self,
        resource_id: str,
        resource_type: str,
        page: int = 1,
    ) -> list:
        return self._record(
            "get_comments",
            resource_id=resource_id,
            resource_type=resource_type,
            page=page,
        )

    # ---- 动态 ----

    async def get_user_dynamics(self, uid: int, offset: str = "") -> dict:
        return self._record("get_user_dynamics", uid=uid, offset=offset)

    async def get_user_latest_dynamic(self, uid: int) -> Optional[dict]:
        return self._record("get_user_latest_dynamic", uid=uid)

    async def add_dynamic_watch(self, uid: int) -> None:
        self._record("add_dynamic_watch", uid=uid)

    async def remove_dynamic_watch(self, uid: int) -> None:
        self._record("remove_dynamic_watch", uid=uid)

    async def add_dynamic_page_watch(self, uid: int) -> None:
        self._record("add_dynamic_page_watch", uid=uid)

    async def remove_dynamic_page_watch(self, uid: int) -> None:
        self._record("remove_dynamic_page_watch", uid=uid)

    # ---- 用户关系 ----

    async def follow_user(self, uid: int) -> Any:
        return self._record("follow_user", uid=uid)

    # ---- 用户查询 ----

    async def get_user_info(self, user_id: int) -> dict:
        return self._record("get_user_info", user_id=user_id)
