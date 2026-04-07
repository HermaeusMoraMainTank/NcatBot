"""GitHub 适配器配置模型"""

from __future__ import annotations

from typing import List, Literal

from pydantic import BaseModel, Field

_DEFAULT_MIRROR_HOSTS: List[str] = [
    "https://github.com/",
    "https://objects.githubusercontent.com/",
]


class GitHubConfig(BaseModel):
    """GitHub 适配器专属配置"""

    # 认证
    token: str = ""

    # 监听仓库
    repos: List[str] = Field(default_factory=list)

    # 连接模式
    mode: Literal["webhook", "polling"] = "webhook"

    # Webhook 配置
    webhook_host: str = "0.0.0.0"
    webhook_port: int = 8080
    webhook_path: str = "/webhook"
    webhook_secret: str = ""

    # Polling 配置
    poll_interval: float = 60.0

    # 网络模式
    network_mode: Literal["proxy", "direct", "mirror"] = "mirror"
    """网络模式：
    - ``proxy``：所有请求（含 API 轮询）走主配置 ``http_proxy`` 代理
    - ``direct``：所有请求直连，不做任何处理
    - ``mirror``：API 请求直连，大型资源下载 URL 替换为镜像前缀（默认）
    """
    mirror_url: str = "https://ghfast.top/"
    """镜像 URL 前缀，仅 ``network_mode=mirror`` 时生效。"""
    mirror_hosts: List[str] = Field(default_factory=lambda: list(_DEFAULT_MIRROR_HOSTS))
    """需要走镜像的 GitHub 域名前缀列表。"""
