"""插件侧 QQ 消息异常统一处理。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Optional, Union

from ncatbot.types import Image, MessageArray
from ncatbot.utils import get_log
from hmmthelp.utils.GroupSilence import is_group_silenced

_log = get_log("PluginQQMessageErrorHandler")
_TIMEOUT_IMAGE = (
    Path(__file__).resolve().parents[1] / "assets" / "timeout_error.jpg"
)


class QQMessageErrorHandler:
    """插件侧统一识别发送超时并发送提示图片。"""

    @staticmethod
    def is_timeout_error(exc: BaseException) -> bool:
        current: Optional[BaseException] = exc
        checked: set[int] = set()
        while current is not None and id(current) not in checked:
            checked.add(id(current))
            error_text = str(current).lower()
            error_type = type(current).__name__.lower()
            if (
                isinstance(current, (TimeoutError, asyncio.TimeoutError))
                or "timeout" in error_type
                or "timeout" in error_text
                or "超时" in error_text
                or "[1200]" in error_text
            ):
                return True
            current = current.__cause__ or current.__context__
        return False

    @classmethod
    async def send_group_error(
        cls,
        qq_api: Any,
        group_id: Union[str, int],
        error: BaseException,
        *,
        fallback_text: str,
        reply: Optional[Union[str, int]] = None,
    ) -> Optional[Any]:
        """群聊超时发送图片，其他异常发送传入的文本。"""
        return await cls.send_error(
            qq_api,
            group_id,
            error,
            fallback_text=fallback_text,
            reply=reply,
        )

    @classmethod
    async def send_private_error(
        cls,
        qq_api: Any,
        user_id: Union[str, int],
        error: BaseException,
        *,
        fallback_text: str,
        reply: Optional[Union[str, int]] = None,
    ) -> Optional[Any]:
        """私聊超时发送图片，其他异常发送传入的文本。"""
        return await cls.send_error(
            qq_api,
            user_id,
            error,
            private=True,
            fallback_text=fallback_text,
            reply=reply,
        )

    @classmethod
    async def send_error(
        cls,
        qq_api: Any,
        target_id: Union[str, int],
        error: BaseException,
        *,
        private: bool = False,
        fallback_text: str,
        reply: Optional[Union[str, int]] = None,
    ) -> Optional[Any]:
        """按目标会话类型发送统一异常提示。"""
        if not private and is_group_silenced(target_id):
            _log.info("跳过静默群 %s 的异常提示", target_id)
            return None
        try:
            if cls.is_timeout_error(error) and _TIMEOUT_IMAGE.is_file():
                method = (
                    qq_api.post_private_msg if private else qq_api.post_group_msg
                )
                return await method(
                    target_id,
                    rtf=MessageArray([Image(file=str(_TIMEOUT_IMAGE))]),
                    reply=reply,
                )
            method = qq_api.post_private_msg if private else qq_api.post_group_msg
            return await method(target_id, text=fallback_text, reply=reply)
        except Exception as send_error:
            _log.warning("发送统一异常提示失败: %s", send_error)
            return None
