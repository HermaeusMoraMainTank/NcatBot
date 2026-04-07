from __future__ import annotations

from typing import List, Optional

from ...common.segment import (
    File,
    Image,
    MessageArray,
    PlainText,
    Video,
)
from ..segment import Forward, ForwardNode

__all__ = ["ForwardConstructor"]


class ForwardConstructor:
    """转发消息构造器，用于便捷地构建合并转发消息。"""

    def __init__(
        self,
        user_id: str = "123456",
        nickname: str = "QQ用户",
        content: Optional[List[ForwardNode]] = None,
    ):
        self.user_id = user_id
        self.nickname = nickname
        self.content: List[ForwardNode] = content if content else []

    def set_author(self, user_id: str, nickname: str) -> None:
        """设置当前作者信息，后续添加的消息将使用此作者"""
        self.user_id = user_id
        self.nickname = nickname

    def attach(
        self,
        content: MessageArray,
        user_id: Optional[str] = None,
        nickname: Optional[str] = None,
    ) -> None:
        """添加一条消息节点"""
        self.content.append(
            ForwardNode(
                user_id=user_id or self.user_id,
                nickname=nickname or self.nickname,
                content=list(content),
            )
        )

    def attach_message(
        self,
        message: MessageArray,
        user_id: Optional[str] = None,
        nickname: Optional[str] = None,
    ) -> None:
        """添加一条 MessageArray 消息"""
        self.attach(message, user_id, nickname)

    def attach_text(
        self,
        text: str,
        user_id: Optional[str] = None,
        nickname: Optional[str] = None,
    ) -> None:
        """添加一条纯文本消息"""
        self.attach(MessageArray([PlainText(text=text)]), user_id, nickname)

    def attach_image(
        self,
        image: str,
        user_id: Optional[str] = None,
        nickname: Optional[str] = None,
    ) -> None:
        """添加一条图片消息"""
        self.attach(MessageArray([Image(file=image)]), user_id, nickname)

    def attach_file(
        self,
        file: str,
        user_id: Optional[str] = None,
        nickname: Optional[str] = None,
    ) -> None:
        """添加一条文件消息"""
        self.attach(MessageArray([File(file=file)]), user_id, nickname)

    def attach_video(
        self,
        video: str,
        user_id: Optional[str] = None,
        nickname: Optional[str] = None,
    ) -> None:
        """添加一条视频消息"""
        self.attach(MessageArray([Video(file=video)]), user_id, nickname)

    def attach_forward(
        self,
        forward: Forward,
        user_id: Optional[str] = None,
        nickname: Optional[str] = None,
    ) -> None:
        """添加一条嵌套转发消息"""
        if forward.content is None:
            raise ValueError("Forward 对象的 content 不能为空")
        self.attach(MessageArray([forward]), user_id, nickname)

    def to_forward(self) -> Forward:
        """构建并返回 Forward 消息段"""
        return Forward(content=self.content)

    def build(self) -> Forward:
        """构建并返回 Forward 消息段 (to_forward 的别名)"""
        return self.to_forward()
