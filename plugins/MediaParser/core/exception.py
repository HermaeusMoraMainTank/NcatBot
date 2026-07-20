class ParseException(Exception):
    """异常基类"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class TipException(ParseException):
    """提示异常"""

    pass


class DownloadException(ParseException):
    """下载异常"""

    def __init__(self, message: str | None = None):
        super().__init__(message or "媒体下载失败")


class DownloadLimitException(DownloadException):
    """下载超过限制异常"""

    pass


class SizeLimitException(DownloadLimitException):
    """下载大小超过限制异常"""

    def __init__(
        self,
        *,
        size_bytes: int | None = None,
        limit_mb: float | int | None = None,
    ):
        self.size_bytes = size_bytes
        self.limit_mb = limit_mb
        if size_bytes is not None and limit_mb is not None:
            msg = (
                f"媒体大小 {size_bytes / 1024 / 1024:.2f} MB "
                f"超过上限 {limit_mb} MB，取消下载"
            )
        elif limit_mb is not None:
            msg = f"媒体大小超过上限 {limit_mb} MB，取消下载"
        else:
            msg = "媒体大小超过配置限制，取消下载"
        super().__init__(msg)


class DurationLimitException(DownloadLimitException):
    """下载时长超过限制异常"""

    def __init__(
        self,
        *,
        duration: float | int | None = None,
        limit_seconds: int | None = None,
    ):
        self.duration = duration
        self.limit_seconds = limit_seconds
        if duration is not None and limit_seconds is not None:
            msg = f"媒体时长 {int(duration)} 秒超过上限 {limit_seconds} 秒，取消下载"
        elif limit_seconds is not None:
            msg = f"媒体时长超过上限 {limit_seconds} 秒，取消下载"
        else:
            msg = "媒体时长超过配置限制，取消下载"
        super().__init__(msg)


class ZeroSizeException(DownloadException):
    """下载大小为 0 异常"""

    def __init__(self):
        super().__init__("媒体大小为 0, 取消下载")
