"""
NapCat WebUI HTTP 客户端

封装 WebUI 的认证和 API 调用能力，供 AuthHandler 和 NapCatConfigManager 共同使用。
"""

import hashlib
import time
from typing import Optional, TYPE_CHECKING

from ncatbot.utils import get_log, post_json
from ..constants import NAPCAT_WEBUI_SALT

if TYPE_CHECKING:
    from ncatbot.utils.config.models import NapCatConfig

LOG = get_log("WebUIClient")


class WebUIAuthError(Exception):
    pass


class WebUIConnectionError(WebUIAuthError):
    pass


class WebUIRateLimitError(WebUIAuthError):
    pass


class WebUIClient:
    """NapCat WebUI HTTP 客户端

    负责 WebUI 认证（获取 Bearer token）和通用 API 调用。

    Parameters
    ----------
    napcat_config:
        NapCatConfig 实例。
    """

    CONNECT_TIMEOUT = 90
    RETRY_INTERVAL = 2

    def __init__(self, napcat_config: "NapCatConfig"):
        self._napcat_config = napcat_config
        self._base_uri = f"http://{napcat_config.webui_host}:{napcat_config.webui_port}"
        self._header: Optional[dict] = None

    @property
    def is_connected(self) -> bool:
        return self._header is not None

    # ==================== 认证 ====================

    def connect(self) -> None:
        """连接 WebUI 并获取 Bearer token，失败抛出异常。"""
        expire_time = time.time() + self.CONNECT_TIMEOUT
        last_error: Optional[Exception] = None
        attempt = 0

        while time.time() < expire_time:
            if attempt > 0:
                time.sleep(self.RETRY_INTERVAL)
            attempt += 1
            try:
                credential = self._try_auth()
                if credential:
                    self._header = {"Authorization": f"Bearer {credential}"}
                    LOG.debug("成功连接到 WebUI")
                    return
            except WebUIRateLimitError as e:
                last_error = e
                LOG.warning(f"WebUI 速率限制, 等待冷却后重试 (第{attempt}次)")
                time.sleep(5)
                continue
            except WebUIAuthError:
                raise
            except Exception as e:
                last_error = e
                remaining = int(expire_time - time.time())
                LOG.debug(f"连接 WebUI 失败 (第{attempt}次, 剩余{remaining}s): {e}")
                continue

        detail = f": {last_error}" if last_error else ""
        raise WebUIConnectionError(
            f"连接 WebUI 超时 (已重试{attempt}次, 超时{self.CONNECT_TIMEOUT}s){detail}"
        )

    def _try_auth(self) -> Optional[str]:
        hashed_token = hashlib.sha256(
            f"{self._napcat_config.webui_token}.{NAPCAT_WEBUI_SALT}".encode()
        ).hexdigest()

        try:
            content = post_json(
                f"{self._base_uri}/api/auth/login",
                payload={"hash": hashed_token},
                timeout=5,
            )
        except TimeoutError:
            raise
        except Exception as e:
            raise ConnectionError(f"无法连接 WebUI ({self._base_uri}): {e}") from e

        code = content.get("code")
        message = content.get("message", "")

        if code == 0:
            credential = content.get("data", {}).get("Credential")
            if credential:
                return credential
            raise WebUIAuthError("认证响应异常: 缺少 Credential 字段")

        if "rate limit" in message.lower():
            raise WebUIRateLimitError(f"WebUI 登录速率限制: {message}")

        raise WebUIAuthError(
            f"WebUI 认证失败 (code={code}): {message}. 请检查 webui_token 配置是否正确"
        )

    # ==================== API 调用 ====================

    def api_call(
        self,
        endpoint: str,
        payload: Optional[dict] = None,
        timeout: int = 5,
    ) -> dict:
        """发送已认证的 API 请求。"""
        return post_json(
            f"{self._base_uri}{endpoint}",
            headers=self._header,
            payload=payload,
            timeout=timeout,
        )
