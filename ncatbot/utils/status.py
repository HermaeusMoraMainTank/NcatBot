"""全局状态管理。"""

from threading import Lock
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    pass


class Status:
    """全局状态管理类。"""

    def __init__(self):
        self.exit = False
        self._lock = Lock()
        self.current_github_proxy: Optional[str] = None

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            setattr(self, key, value)


status = Status()
