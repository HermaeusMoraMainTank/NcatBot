"""GitHub 平台发送者类型"""

from __future__ import annotations

from typing import Optional

from ..common.sender import BaseSender

from .enums import GitHubUserType

__all__ = [
    "GitHubSender",
]


class GitHubSender(BaseSender):
    """GitHub 用户信息"""

    login: str = ""
    avatar_url: Optional[str] = None
    html_url: Optional[str] = None
    user_type: GitHubUserType = GitHubUserType.USER
