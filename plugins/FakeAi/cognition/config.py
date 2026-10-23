"""认知层开关（记忆 / 知识注入）。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CognitionConfig:
    # 记忆写入总开关
    memory_record_enabled: bool = True
    # 定时巩固/休眠整理任务
    sleep_enabled: bool = True
    # 「记住…」短语自动写入 memory_record
    auto_remember_on_phrase: bool = True


DEFAULT_COGNITION = CognitionConfig()
