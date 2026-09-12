"""QQ 消息发送辅助：NapCat sendMsg 超时重试、统计报告分条发送。"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, List, Optional, Union

from ncatbot.types import Image, MessageArray, PlainText
from ncatbot.utils import get_log

_log = get_log("QqSendUtil")


class QqSendUtil:
    @staticmethod
    def is_send_timeout_error(exc: Exception) -> bool:
        err_s = str(exc)
        return "Timeout" in err_s or "[1200]" in err_s

    @staticmethod
    async def post_group_msg_retry(
        qq_api: Any,
        group_id: Union[str, int],
        *,
        retries: int = 2,
        **kwargs,
    ) -> None:
        last_err: Optional[Exception] = None
        for attempt in range(retries + 1):
            try:
                await qq_api.post_group_msg(group_id, **kwargs)
                return
            except Exception as e:
                last_err = e
                if attempt < retries and QqSendUtil.is_send_timeout_error(e):
                    _log.warning(
                        "send_group_msg 超时，重试 %s/%s (group=%s)",
                        attempt + 1,
                        retries,
                        group_id,
                    )
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                raise
        if last_err:
            raise last_err

    @staticmethod
    async def send_flip_and_report(
        qq_api: Any,
        group_id: Union[str, int],
        *,
        total_count: int,
        report_path: Optional[str],
        header: str = "",
        reply_id: Optional[Union[str, int]] = None,
        number_to_counter: Callable[[int], List[Image]],
        report_retries: int = 3,
    ) -> None:
        """发送计数器 GIF + 统计长图（分两条，带重试与间隔）。"""
        elements: List = []
        if header:
            elements.append(PlainText(text=header))
        elements.extend(number_to_counter(total_count))

        await QqSendUtil.post_group_msg_retry(
            qq_api,
            group_id,
            rtf=MessageArray(elements),
            reply=reply_id,
        )
        if not report_path:
            return
        await asyncio.sleep(0.8)
        await QqSendUtil.post_group_msg_retry(
            qq_api,
            group_id,
            rtf=MessageArray([Image(file=report_path)]),
            reply=reply_id,
            retries=report_retries,
        )
