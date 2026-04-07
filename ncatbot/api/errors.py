"""API 异常层级

adapter 层在收到 retcode != 0 时抛出对应的 APIError 子类,
上层业务通过 try/except 处理.
"""

from __future__ import annotations

from typing import Any


class APIError(Exception):
    """所有 API 调用错误的基类"""

    def __init__(
        self,
        retcode: int,
        message: str = "",
        wording: str = "",
        action: str = "",
        data: Any = None,
    ) -> None:
        self.retcode = retcode
        self.message = message
        self.wording = wording
        self.action = action
        self.data = data
        super().__init__(self._format())

    def _format(self) -> str:
        parts = [f"API 调用失败 [{self.retcode}]"]
        if self.action:
            parts.append(f"action={self.action}")
        if self.message:
            parts.append(self.message)
        return " ".join(parts)


class APIRequestError(APIError):
    """retcode 1400 — 请求参数错误或业务逻辑执行失败"""

    pass


class APIPermissionError(APIError):
    """retcode 1401 — 权限不足"""

    pass


class APINotFoundError(APIError):
    """retcode 1404 — 资源不存在"""

    pass


# retcode → 异常类映射
_ERROR_MAP: dict[int, type[APIError]] = {
    1400: APIRequestError,
    1401: APIPermissionError,
    1404: APINotFoundError,
}


def raise_for_retcode(
    resp: dict,
    *,
    action: str = "",
) -> None:
    """检查响应信封, retcode != 0 时抛出对应异常"""
    retcode = resp.get("retcode", 0)
    if retcode == 0:
        return
    cls = _ERROR_MAP.get(retcode, APIError)
    raise cls(
        retcode=retcode,
        message=resp.get("message", ""),
        wording=resp.get("wording", ""),
        action=action,
        data=resp.get("data"),
    )
