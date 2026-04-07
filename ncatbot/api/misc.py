"""MiscAPI — 杂项工具 API（下载、HTTP 请求、代理检查）

通过 ``api.misc`` 访问，提供与平台无关的 HTTP/下载/代理工具::

    # 下载文件
    path = await api.misc.download_to_file(url, "./downloads")

    # 下载到内存
    data = await api.misc.download_to_bytes(url)

    # GET 请求
    body = await api.misc.http_get("https://api.example.com/data")

    # 检查代理
    ok = await api.misc.is_proxy_valid()
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union, TYPE_CHECKING

from ncatbot.utils import get_log
from ncatbot.utils import (
    async_check_proxy,
    async_download_to_bytes,
    async_download_to_file,
    async_http_get,
)

if TYPE_CHECKING:
    from ncatbot.utils.config.models import Config

LOG = get_log("MiscAPI")


class MiscAPI:
    """杂项工具 API — 下载、HTTP 请求、代理检查。

    所有带 ``proxy`` 参数的方法：显式传值时使用传入的代理；
    省略 / ``None`` 时自动使用主配置 ``http_proxy``；
    配置也为空则直连。
    """

    def __init__(self, config: "Config") -> None:
        self._config = config

    def get_proxy(self) -> Optional[str]:
        """返回当前主配置中的 ``http_proxy``，无配置时返回 ``None``。"""
        return self._config.http_proxy or None

    def _resolve_proxy(self, proxy: Optional[str]) -> Optional[str]:
        """解析代理：显式传值优先，否则取配置。"""
        if proxy is not None:
            return proxy
        return self.get_proxy()

    async def download_to_file(
        self,
        url: str,
        dest_dir: Union[str, Path],
        *,
        filename: Optional[str] = None,
        proxy: Optional[str] = None,
    ) -> Path:
        """下载 URL 到目录，返回文件路径。

        Parameters
        ----------
        url : str
            下载地址。
        dest_dir : str | Path
            目标目录。
        filename : str, optional
            文件名，省略则从 URL 推断。
        proxy : str, optional
            代理地址，省略则使用主配置 ``http_proxy``。
        """
        resolved = self._resolve_proxy(proxy)
        return await async_download_to_file(
            url, dest_dir, filename=filename, proxy=resolved
        )

    async def download_to_bytes(
        self,
        url: str,
        *,
        proxy: Optional[str] = None,
    ) -> bytes:
        """下载 URL 到内存，返回原始字节。

        Parameters
        ----------
        url : str
            下载地址。
        proxy : str, optional
            代理地址，省略则使用主配置 ``http_proxy``。
        """
        resolved = self._resolve_proxy(proxy)
        return await async_download_to_bytes(url, proxy=resolved)

    async def http_get(
        self,
        url: str,
        *,
        headers: Optional[dict] = None,
        proxy: Optional[str] = None,
        timeout: float = 10.0,
    ) -> bytes:
        """异步 GET 请求，返回响应体字节。

        Parameters
        ----------
        url : str
            请求地址。
        headers : dict, optional
            额外请求头。
        proxy : str, optional
            代理地址，省略则使用主配置 ``http_proxy``。
        timeout : float
            超时秒数，默认 10。
        """
        resolved = self._resolve_proxy(proxy)
        return await async_http_get(
            url, headers=headers, proxy=resolved, timeout=timeout
        )

    async def is_proxy_valid(self, proxy: Optional[str] = None) -> bool:
        """检查代理是否可用。

        Parameters
        ----------
        proxy : str, optional
            要检查的代理地址。省略则检查主配置 ``http_proxy``。
            如果配置也为空，直接返回 ``False``。

        Returns
        -------
        bool
        """
        resolved = self._resolve_proxy(proxy)
        if not resolved:
            return False
        return await async_check_proxy(resolved)
