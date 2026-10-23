"""事件循环卡顿看门狗。

后台线程定期检查主线程心跳；如果 asyncio 事件循环超过阈值没有心跳，
就把所有线程的 Python 栈写入 logs/eventloop-freeze-*.txt，便于定位是哪个
同步调用把机器人卡死。
"""

from __future__ import annotations

import faulthandler
import threading
import time
from pathlib import Path


class LoopWatchdog:
    def __init__(
        self,
        *,
        threshold: float = 5.0,
        log_dir: Path | str = "logs",
    ) -> None:
        self.threshold = max(1.0, float(threshold))
        self.log_dir = Path(log_dir)
        self._last_beat = time.monotonic()
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="LoopWatchdog", daemon=True
        )

    def beat(self) -> None:
        self._last_beat = time.monotonic()

    def start(self) -> None:
        if not self._thread.is_alive():
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(1.0):
            lag = time.monotonic() - self._last_beat
            if lag < self.threshold:
                continue
            try:
                self.log_dir.mkdir(parents=True, exist_ok=True)
                path = self.log_dir / time.strftime(
                    "eventloop-freeze-%Y%m%d-%H%M%S.txt"
                )
                with path.open("a", encoding="utf-8") as fh:
                    fh.write(
                        f"event loop stalled for {lag:.1f}s at "
                        f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                    )
                    faulthandler.dump_traceback(file=fh, all_threads=True)
            except Exception:
                pass
            # 一次卡顿只写一份，等循环恢复后重新计时
            self._last_beat = time.monotonic()
