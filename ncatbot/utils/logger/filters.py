"""日志过滤器。"""

import re
import logging


# 需要按前缀压制的第三方动态命名 logger（低于 WARNING 的消息被丢弃）
_SUPPRESSED_PREFIXES = ("LiveDanmaku_",)


class ThirdPartyNameFilter(logging.Filter):
    """按 logger 名称前缀压制第三方库的低级别日志。"""

    def filter(self, record: logging.LogRecord) -> bool:
        for prefix in _SUPPRESSED_PREFIXES:
            if record.name.startswith(prefix):
                return record.levelno >= logging.WARNING
        return True


class MessageFoldFilter(logging.Filter):
    """折叠超长消息和 base64 内容。

    - 替换连续 1000+ 字符的 base64 片段为 [BASE64_CONTENT]
    - 总长超过 2000 字符时截断
    """

    _BASE64_RE = re.compile(r"[A-Za-z0-9+/\\]{1000,}={0,2}")
    MAX_LENGTH = 2000

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if len(msg) > 1000:
            msg = self._BASE64_RE.sub("[BASE64_CONTENT]", msg)
        if len(msg) > self.MAX_LENGTH:
            msg = msg[: self.MAX_LENGTH] + "...[TRUNCATED]"
        record.msg = msg
        record.args = None
        return True
