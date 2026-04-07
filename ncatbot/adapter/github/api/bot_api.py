"""GitHubBotAPI — GitHub 平台 API 主实现

组合所有 Mixin 并实现 IGitHubAPIClient 接口。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

from ncatbot.api.github import IGitHubAPIClient
from ncatbot.types.common import (
    Attachment,
    AttachmentList,
    AudioAttachment,
    FileAttachment,
    ImageAttachment,
    VideoAttachment,
)
from ncatbot.types.github import GitHubReleaseAsset
from ncatbot.utils import get_log

from .issue import IssueAPIMixin
from .comment import CommentAPIMixin
from .pr import PRAPIMixin
from .query import QueryAPIMixin
from .release import ReleaseAPIMixin

LOG = get_log("GitHubBotAPI")

_BASE_URL = "https://api.github.com"

_CONTENT_TYPE_MAP: Dict[str, type[Attachment]] = {
    "image": ImageAttachment,
    "video": VideoAttachment,
    "audio": AudioAttachment,
}


def _pick_attachment_cls(content_type: str | None) -> type[Attachment]:
    if content_type:
        major = content_type.split("/", 1)[0]
        if major in _CONTENT_TYPE_MAP:
            return _CONTENT_TYPE_MAP[major]
    return FileAttachment


class GitHubBotAPI(
    IssueAPIMixin,
    CommentAPIMixin,
    PRAPIMixin,
    QueryAPIMixin,
    ReleaseAPIMixin,
    IGitHubAPIClient,
):
    """GitHub 平台 IGitHubAPIClient 实现"""

    @property
    def platform(self) -> str:
        return "github"

    def __init__(self, token: str, *, proxy: Optional[str] = None) -> None:
        self._token = token
        self._base_url = _BASE_URL
        self._proxy = proxy
        self._client: Optional[httpx.AsyncClient] = None
        # 网络模式配置（由 adapter.connect() 注入）
        self._network_mode: str = "direct"
        self._mirror_url: str = ""
        self._mirror_hosts: List[str] = []

    def set_network_config(
        self,
        mode: str,
        mirror_url: str = "",
        mirror_hosts: Optional[List[str]] = None,
    ) -> None:
        """注入网络模式配置（由 GitHubAdapter.connect 调用）。"""
        self._network_mode = mode
        self._mirror_url = mirror_url.rstrip("/") + "/" if mirror_url else ""
        self._mirror_hosts = mirror_hosts or []

    def resolve_download_url(self, url: str) -> str:
        """根据 network_mode 处理下载 URL。

        - ``mirror`` 模式：匹配 ``mirror_hosts`` 的 URL 加镜像前缀
        - 其他模式：原样返回
        """
        if self._network_mode != "mirror" or not self._mirror_url:
            return url
        for host in self._mirror_hosts:
            if url.startswith(host):
                return f"{self._mirror_url}{url}"
        return url

    def assets_to_attachments(
        self, assets: List[GitHubReleaseAsset]
    ) -> AttachmentList[Attachment]:
        """将 GitHubReleaseAsset 列表转换为跨平台 Attachment 列表。

        在 ``mirror`` 模式下自动将匹配的下载 URL 替换为镜像地址，
        原始 URL 保留在 ``extra["original_url"]`` 中。
        """
        items: list[Attachment] = []
        for asset in assets:
            cls = _pick_attachment_cls(asset.content_type or None)
            original_url = asset.browser_download_url
            resolved_url = self.resolve_download_url(original_url)
            extra: Dict[str, Any] = {
                "id": asset.id,
                "download_count": asset.download_count,
            }
            if resolved_url != original_url:
                extra["original_url"] = original_url
            items.append(
                cls(
                    name=asset.name,
                    url=resolved_url,
                    size=asset.size,
                    content_type=asset.content_type or None,
                    extra=extra,
                )
            )
        return AttachmentList(items)

    async def ensure_session(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers: Dict[str, str] = {
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
            if self._token:
                headers["Authorization"] = f"Bearer {self._token}"
            self._client = httpx.AsyncClient(
                headers=headers,
                follow_redirects=True,
                timeout=30,
                proxy=self._proxy,
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        """统一 HTTP 请求"""
        client = await self.ensure_session()
        url = f"{self._base_url}{path}"
        resp = await client.request(method, url, **kwargs)

        # 记录速率限制
        remaining = resp.headers.get("X-RateLimit-Remaining")
        if remaining is not None and int(remaining) < 100:
            LOG.warning("GitHub API 速率限制接近: remaining=%s", remaining)

        if resp.status_code == 204:
            return None
        body = resp.json()
        if resp.status_code >= 400:
            msg = body.get("message", "") if isinstance(body, dict) else str(body)
            LOG.error(
                "GitHub API 错误: %s %s → %d %s", method, path, resp.status_code, msg
            )
            raise httpx.HTTPStatusError(
                message=msg,
                request=resp.request,
                response=resp,
            )
        return body

    async def call(self, action: str, params: Optional[dict] = None) -> Any:
        """通用 API 调用入口 — 按 action 名分派到对应方法"""
        method = getattr(self, action, None)
        if method is None:
            raise ValueError(f"未知的 API action: {action}")
        if params:
            return await method(**params)
        return await method()
