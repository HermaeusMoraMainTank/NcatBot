"""群管理 API Mixin"""

from typing import List, Union

from ncatbot.types.napcat import GroupNotice


class GroupAPIMixin:
    async def set_group_kick(
        self,
        group_id: Union[str, int],
        user_id: Union[str, int],
        reject_add_request: bool = False,
    ) -> None:
        await self._call(
            "set_group_kick",
            {
                "group_id": int(group_id),
                "user_id": int(user_id),
                "reject_add_request": reject_add_request,
            },
        )

    async def set_group_ban(
        self,
        group_id: Union[str, int],
        user_id: Union[str, int],
        duration: int = 30 * 60,
    ) -> None:
        await self._call(
            "set_group_ban",
            {
                "group_id": int(group_id),
                "user_id": int(user_id),
                "duration": duration,
            },
        )

    async def set_group_whole_ban(
        self,
        group_id: Union[str, int],
        enable: bool = True,
    ) -> None:
        await self._call(
            "set_group_whole_ban",
            {
                "group_id": int(group_id),
                "enable": enable,
            },
        )

    async def set_group_admin(
        self,
        group_id: Union[str, int],
        user_id: Union[str, int],
        enable: bool = True,
    ) -> None:
        await self._call(
            "set_group_admin",
            {
                "group_id": int(group_id),
                "user_id": int(user_id),
                "enable": enable,
            },
        )

    async def set_group_card(
        self,
        group_id: Union[str, int],
        user_id: Union[str, int],
        card: str = "",
    ) -> None:
        await self._call(
            "set_group_card",
            {
                "group_id": int(group_id),
                "user_id": int(user_id),
                "card": card,
            },
        )

    async def set_group_name(
        self,
        group_id: Union[str, int],
        name: str,
    ) -> None:
        await self._call(
            "set_group_name",
            {
                "group_id": int(group_id),
                "group_name": name,
            },
        )

    async def set_group_leave(
        self,
        group_id: Union[str, int],
        is_dismiss: bool = False,
    ) -> None:
        await self._call(
            "set_group_leave",
            {
                "group_id": int(group_id),
                "is_dismiss": is_dismiss,
            },
        )

    async def set_group_special_title(
        self,
        group_id: Union[str, int],
        user_id: Union[str, int],
        special_title: str = "",
    ) -> None:
        await self._call(
            "set_group_special_title",
            {
                "group_id": int(group_id),
                "user_id": int(user_id),
                "special_title": special_title,
            },
        )

    # ---- 群公告 ----

    async def get_group_notice(self, group_id: Union[str, int]) -> List[GroupNotice]:
        data = (
            await self._call_data("_get_group_notice", {"group_id": int(group_id)})
            or []
        )
        return [GroupNotice(**item) for item in data]

    async def send_group_notice(
        self,
        group_id: Union[str, int],
        content: str,
        image: str = "",
    ) -> None:
        params: dict = {"group_id": int(group_id), "content": content}
        if image:
            params["image"] = image
        await self._call("_send_group_notice", params)

    async def delete_group_notice(
        self, group_id: Union[str, int], notice_id: str
    ) -> None:
        await self._call(
            "_del_group_notice",
            {"group_id": int(group_id), "notice_id": notice_id},
        )

    # ---- 群精华消息 ----

    async def set_essence_msg(self, message_id: Union[str, int]) -> None:
        await self._call("set_essence_msg", {"message_id": int(message_id)})

    async def delete_essence_msg(self, message_id: Union[str, int]) -> None:
        await self._call("delete_essence_msg", {"message_id": int(message_id)})

    # ---- 批量踢人 ----

    async def set_group_kick_members(
        self,
        group_id: Union[str, int],
        user_ids: list,
        reject_add_request: bool = False,
    ) -> None:
        await self._call(
            "set_group_kick_members",
            {
                "group_id": int(group_id),
                "user_ids": [int(uid) for uid in user_ids],
                "reject_add_request": reject_add_request,
            },
        )

    # ---- 群设置扩展 ----

    async def set_group_remark(self, group_id: Union[str, int], remark: str) -> None:
        await self._call(
            "set_group_remark",
            {"group_id": int(group_id), "remark": remark},
        )

    async def set_group_sign(self, group_id: Union[str, int]) -> None:
        await self._call("set_group_sign", {"group_id": int(group_id)})

    async def set_group_todo(
        self, group_id: Union[str, int], message_id: Union[str, int]
    ) -> None:
        await self._call(
            "set_group_todo",
            {"group_id": int(group_id), "message_id": str(message_id)},
        )

    async def set_group_portrait(self, group_id: Union[str, int], file: str) -> None:
        await self._call(
            "set_group_portrait",
            {"group_id": int(group_id), "file": file},
        )
