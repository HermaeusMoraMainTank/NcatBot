"""认知层开关（记忆 / 知识注入）。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CognitionConfig:
    memory_record_enabled: bool = True
    auto_recall_enabled: bool = True
    auto_recall_limit: int = 3
    knowledge_inject_max_chars: int = 300
    knowledge_item_max_chars: int = 100
    knowledge_inject_limit: int = 3
    sleep_enabled: bool = True
    # 「记住」短语自动写入 memory_record（无 LLM tool 时的实用路径）
    auto_remember_on_phrase: bool = True


DEFAULT_COGNITION = CognitionConfig()
