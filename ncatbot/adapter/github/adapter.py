"""GitHubAdapter — GitHub 平台适配器

支持 Webhook (HTTP Server) 和 Polling (REST API) 两种模式。
Webhook 模式可直接接收 GitHub webhook 或经 gosmee/smee.io 转发。
"""

from __future__ import annotations

from typing import Any, Dict, Optional, TYPE_CHECKING

from ..base import BaseAdapter
from ncatbot.utils import get_log
from .api import GitHubBotAPI
from .config import GitHubConfig
from .parser import GitHubEventParser
from .source.manager import SourceManager

if TYPE_CHECKING:
    from ncatbot.api import IAPIClient
    from ncatbot.types import BaseEventData

LOG = get_log("GitHubAdapter")


class GitHubAdapter(BaseAdapter):
    name = "github"
    description = "GitHub 适配器 (Webhook/Polling)"
    supported_protocols = ["github_webhook", "github_polling"]
    platform = "github"

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        *,
        bot_uin: str = "",
        websocket_timeout: int = 15,
    ) -> None:
        super().__init__(
            config=config,
            bot_uin=bot_uin,
            websocket_timeout=websocket_timeout,
        )
        self._config = GitHubConfig.model_validate(self._raw_config)
        self._source_manager: Any = None
        self._api: Any = None
        self._parser: Any = None
        self._connected = False

    async def setup(self) -> None:
        """验证 token 有效性"""
        if not self._config.token:
            LOG.warning("未配置 GitHub token，API 调用将受到严格速率限制")
            return

        proxy = self._resolve_proxy()
        api = GitHubBotAPI(self._config.token, proxy=proxy)
        try:
            user = await api.get_authenticated_user()
            LOG.info("GitHub 认证成功: %s", user.get("login", "unknown"))
        except Exception as e:
            LOG.error("GitHub token 验证失败: %s", e)
            raise
        finally:
            await api.close()

    async def connect(self) -> None:
        proxy = self._resolve_proxy()
        self._parser = GitHubEventParser(self_id=self._bot_uin)
        self._api = GitHubBotAPI(self._config.token, proxy=proxy)
        self._api.set_network_config(
            mode=self._config.network_mode,
            mirror_url=self._config.mirror_url,
            mirror_hosts=self._config.mirror_hosts,
        )

        self._source_manager = SourceManager(
            callback=self._on_source_event,
            mode=self._config.mode,
            token=self._config.token,
            repos=self._config.repos,
            webhook_host=self._config.webhook_host,
            webhook_port=self._config.webhook_port,
            webhook_path=self._config.webhook_path,
            webhook_secret=self._config.webhook_secret,
            poll_interval=self._config.poll_interval,
            proxy=proxy,
        )

        self._connected = True
        LOG.info(
            "GitHub 适配器已连接 (mode=%s, network=%s, repos=%s)",
            self._config.mode,
            self._config.network_mode,
            self._config.repos,
        )

    def _resolve_proxy(self) -> Optional[str]:
        """仅 proxy 模式返回主配置 http_proxy，其他模式返回 None。"""
        if self._config.network_mode != "proxy":
            return None
        try:
            from ncatbot.utils import get_config_manager

            proxy = get_config_manager().config.http_proxy or None
        except Exception:
            proxy = None
        if not proxy:
            LOG.warning("network_mode=proxy 但主配置未设置 http_proxy，将直连")
        return proxy

    async def listen(self) -> None:
        await self._source_manager.run_forever()

    async def disconnect(self) -> None:
        if self._source_manager is not None:
            await self._source_manager.stop()
        if self._api is not None:
            await self._api.close()
        self._connected = False
        LOG.debug("GitHub 适配器已断开")

    def get_api(self) -> "IAPIClient":
        return self._api

    @property
    def connected(self) -> bool:
        return self._connected

    async def _on_source_event(self, event_type: str, payload: dict) -> None:
        """数据源回调 → 解析 → 事件回调"""
        parsed: Optional["BaseEventData"] = self._parser.parse(event_type, payload)
        if parsed is not None and self._event_callback is not None:
            await self._event_callback(parsed)
