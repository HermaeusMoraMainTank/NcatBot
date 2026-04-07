"""IGitHubAPIClient — GitHub 平台 API 接口

定义 GitHub 适配器所有可用 API 方法。
由 GitHubBotAPI 实现。
"""

from __future__ import annotations

from abc import abstractmethod
from typing import List, Optional, Union

from ..base import IAPIClient
from ncatbot.types.github import GitHubMergeMethod
from ncatbot.types.github import (
    GitHubCommentInfo,
    GitHubIssueInfo,
    GitHubLabelInfo,
    GitHubMergeResult,
    GitHubPullRequestInfo,
    GitHubReleaseAsset,
    GitHubReleaseInfo,
    GitHubRepoInfo,
    GitHubUserInfo,
)
from ncatbot.types.common import Attachment, AttachmentList


class IGitHubAPIClient(IAPIClient):
    """GitHub 平台 Bot API 接口"""

    # ---- Issue 操作 ----

    @abstractmethod
    async def create_issue(
        self,
        repo: str,
        title: str,
        body: str = "",
        labels: Optional[List[str]] = None,
        assignees: Optional[List[str]] = None,
    ) -> GitHubIssueInfo:
        """创建 Issue"""

    @abstractmethod
    async def update_issue(
        self,
        repo: str,
        issue_number: int,
        *,
        title: Optional[str] = None,
        body: Optional[str] = None,
        state: Optional[str] = None,
        labels: Optional[List[str]] = None,
        assignees: Optional[List[str]] = None,
    ) -> GitHubIssueInfo:
        """更新 Issue"""

    @abstractmethod
    async def close_issue(self, repo: str, issue_number: int) -> GitHubIssueInfo:
        """关闭 Issue"""

    @abstractmethod
    async def reopen_issue(self, repo: str, issue_number: int) -> GitHubIssueInfo:
        """重新打开 Issue"""

    @abstractmethod
    async def get_issue(self, repo: str, issue_number: int) -> GitHubIssueInfo:
        """获取 Issue 详情"""

    @abstractmethod
    async def add_labels(
        self, repo: str, issue_number: int, labels: List[str]
    ) -> List[GitHubLabelInfo]:
        """添加标签"""

    @abstractmethod
    async def remove_label(self, repo: str, issue_number: int, label: str) -> None:
        """移除标签"""

    @abstractmethod
    async def set_assignees(
        self, repo: str, issue_number: int, assignees: List[str]
    ) -> GitHubIssueInfo:
        """设置负责人"""

    # ---- 评论操作 ----

    @abstractmethod
    async def create_issue_comment(
        self, repo: str, issue_number: int, body: str
    ) -> GitHubCommentInfo:
        """创建 Issue/PR 评论"""

    @abstractmethod
    async def update_comment(
        self, repo: str, comment_id: int, body: str
    ) -> GitHubCommentInfo:
        """更新评论"""

    @abstractmethod
    async def delete_comment(self, repo: str, comment_id: int) -> None:
        """删除评论"""

    @abstractmethod
    async def list_issue_comments(
        self, repo: str, issue_number: int, page: int = 1, per_page: int = 30
    ) -> List[GitHubCommentInfo]:
        """列出评论"""

    # ---- PR 操作 ----

    @abstractmethod
    async def create_pr_comment(
        self, repo: str, pr_number: int, body: str
    ) -> GitHubCommentInfo:
        """创建 PR 评论 (等同 create_issue_comment)"""

    @abstractmethod
    async def merge_pr(
        self,
        repo: str,
        pr_number: int,
        *,
        merge_method: Union[GitHubMergeMethod, str] = GitHubMergeMethod.MERGE,
        commit_title: Optional[str] = None,
        commit_message: Optional[str] = None,
    ) -> GitHubMergeResult:
        """合并 PR"""

    @abstractmethod
    async def close_pr(self, repo: str, pr_number: int) -> GitHubPullRequestInfo:
        """关闭 PR"""

    @abstractmethod
    async def request_review(
        self, repo: str, pr_number: int, reviewers: List[str]
    ) -> GitHubPullRequestInfo:
        """请求 Review"""

    @abstractmethod
    async def get_pr(self, repo: str, pr_number: int) -> GitHubPullRequestInfo:
        """获取 PR 详情"""

    # ---- 查询 ----

    @abstractmethod
    async def get_repo(self, repo: str) -> GitHubRepoInfo:
        """获取仓库信息"""

    @abstractmethod
    async def get_user(self, username: str) -> GitHubUserInfo:
        """获取用户信息"""

    @abstractmethod
    async def get_authenticated_user(self) -> GitHubUserInfo:
        """获取当前认证用户"""

    # ---- Release 操作 ----

    @abstractmethod
    async def get_release(
        self, repo: str, release_id: Union[str, int]
    ) -> GitHubReleaseInfo:
        """获取指定 Release 详情"""

    @abstractmethod
    async def get_release_by_tag(self, repo: str, tag: str) -> GitHubReleaseInfo:
        """按 Tag 获取 Release 详情"""

    @abstractmethod
    async def get_latest_release(self, repo: str) -> GitHubReleaseInfo:
        """获取最新 Release"""

    @abstractmethod
    async def list_release_assets(
        self, repo: str, release_id: Union[str, int]
    ) -> List[GitHubReleaseAsset]:
        """列出 Release 的所有 Assets"""

    # ---- 网络 / 附件工具 ----

    @abstractmethod
    def resolve_download_url(self, url: str) -> str:
        """根据 network_mode 处理下载 URL（mirror 模式加镜像前缀）"""

    @abstractmethod
    def assets_to_attachments(
        self, assets: List[GitHubReleaseAsset]
    ) -> AttachmentList[Attachment]:
        """将 GitHubReleaseAsset 列表转换为跨平台 Attachment 列表"""
