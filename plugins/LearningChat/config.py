"""
LearningChat 配置管理模块
"""

import os
from typing import List, Dict, Optional
from pathlib import Path
from dataclasses import dataclass, field
import logging

try:
    from ruamel.yaml import YAML

    yaml = YAML()
    yaml.preserve_quotes = True
except ImportError:
    import yaml as pyyaml

    yaml = None

log = logging.getLogger(__name__)

# 配置文件路径
CONFIG_PATH = Path("data") / "LearningChat" / "config.yaml"
CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

# 默认昵称
NICKNAME = "蓝晴"

# 超级用户列表（可以从主配置读取）
SUPERUSERS: List[int] = []


@dataclass
class ChatGroupConfig:
    """群聊配置"""

    enable: bool = True  # 群聊学习开关
    ban_words: List[str] = field(default_factory=list)  # 屏蔽词
    ban_users: List[int] = field(default_factory=list)  # 屏蔽用户
    answer_threshold: int = 4  # 回复阈值
    answer_threshold_weights: List[int] = field(
        default_factory=lambda: [10, 30, 60]
    )  # 回复阈值权重
    repeat_threshold: int = 3  # 复读阈值
    break_probability: float = 0.25  # 打断复读概率
    speak_enable: bool = True  # 主动发言开关
    speak_threshold: int = 5  # 主动发言阈值
    speak_min_interval: int = 300  # 主动发言最小间隔（秒）
    speak_continuously_probability: float = 0.5  # 连续主动发言概率
    speak_continuously_max_len: int = 3  # 最大连续主动发言句数
    speak_poke_probability: float = 0.5  # 主动发言附带戳一戳概率

    def update(self, **kwargs):
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)

    def to_dict(self) -> dict:
        return {
            "群聊学习开关": self.enable,
            "屏蔽词": self.ban_words,
            "屏蔽用户": self.ban_users,
            "回复阈值": self.answer_threshold,
            "回复阈值权重": self.answer_threshold_weights,
            "复读阈值": self.repeat_threshold,
            "打断复读概率": self.break_probability,
            "主动发言开关": self.speak_enable,
            "主动发言阈值": self.speak_threshold,
            "主动发言最小间隔": self.speak_min_interval,
            "连续主动发言概率": self.speak_continuously_probability,
            "最大连续主动发言句数": self.speak_continuously_max_len,
            "主动发言附带戳一戳概率": self.speak_poke_probability,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ChatGroupConfig":
        return cls(
            enable=data.get("群聊学习开关", True),
            ban_words=data.get("屏蔽词", []),
            ban_users=data.get("屏蔽用户", []),
            answer_threshold=data.get("回复阈值", 4),
            answer_threshold_weights=data.get("回复阈值权重", [10, 30, 60]),
            repeat_threshold=data.get("复读阈值", 3),
            break_probability=data.get("打断复读概率", 0.25),
            speak_enable=data.get("主动发言开关", True),
            speak_threshold=data.get("主动发言阈值", 5),
            speak_min_interval=data.get("主动发言最小间隔", 300),
            speak_continuously_probability=data.get("连续主动发言概率", 0.5),
            speak_continuously_max_len=data.get("最大连续主动发言句数", 3),
            speak_poke_probability=data.get("主动发言附带戳一戳概率", 0.5),
        )


@dataclass
class ChatConfig:
    """全局配置"""

    total_enable: bool = True  # 群聊学习总开关
    ban_words: List[str] = field(default_factory=list)  # 全局屏蔽词
    ban_users: List[int] = field(default_factory=list)  # 全局屏蔽用户
    keywords_size: int = 3  # 单句关键词分词数量
    cross_group_threshold: int = 3  # 跨群回复阈值
    learn_max_count: int = 6  # 最高学习次数
    dictionary: List[str] = field(default_factory=list)  # 自定义词典
    group_config: Dict[int, ChatGroupConfig] = field(default_factory=dict)  # 分群配置

    def update(self, **kwargs):
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)

    def to_dict(self) -> dict:
        return {
            "群聊学习总开关": self.total_enable,
            "全局屏蔽词": self.ban_words,
            "全局屏蔽用户": self.ban_users,
            "单句关键词分词数量": self.keywords_size,
            "跨群回复阈值": self.cross_group_threshold,
            "最高学习次数": self.learn_max_count,
            "自定义词典": self.dictionary,
            "分群配置": {str(k): v.to_dict() for k, v in self.group_config.items()},
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ChatConfig":
        group_config = {}
        for k, v in data.get("分群配置", {}).items():
            try:
                group_id = int(k)
                group_config[group_id] = ChatGroupConfig.from_dict(v)
            except (ValueError, TypeError):
                pass

        return cls(
            total_enable=data.get("群聊学习总开关", True),
            ban_words=data.get("全局屏蔽词", []),
            ban_users=data.get("全局屏蔽用户", []),
            keywords_size=data.get("单句关键词分词数量", 3),
            cross_group_threshold=data.get("跨群回复阈值", 3),
            learn_max_count=data.get("最高学习次数", 6),
            dictionary=data.get("自定义词典", []),
            group_config=group_config,
        )


class ChatConfigManager:
    """配置管理器"""

    def __init__(self):
        self.file_path = CONFIG_PATH
        self.config = self._load_config()
        self.save()

    def _load_config(self) -> ChatConfig:
        """加载配置"""
        if self.file_path.exists():
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    if yaml:
                        data = yaml.load(f)
                    else:
                        data = pyyaml.safe_load(f)
                    if data:
                        return ChatConfig.from_dict(data)
            except Exception as e:
                log.error(f"加载配置失败: {e}")
        return ChatConfig()

    def get_group_config(self, group_id: int) -> ChatGroupConfig:
        """获取群配置"""
        if group_id not in self.config.group_config:
            self.config.group_config[group_id] = ChatGroupConfig()
            self.save()
        return self.config.group_config[group_id]

    def save(self):
        """保存配置"""
        try:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.file_path, "w", encoding="utf-8") as f:
                if yaml:
                    yaml.dump(self.config.to_dict(), f)
                else:
                    pyyaml.dump(
                        self.config.to_dict(),
                        f,
                        allow_unicode=True,
                        default_flow_style=False,
                    )
        except Exception as e:
            log.error(f"保存配置失败: {e}")


# 全局配置管理器实例
config_manager = ChatConfigManager()


def log_debug(command: str, info: str):
    log.debug(f"[{command}] {info}")


def log_info(command: str, info: str):
    log.info(f"[{command}] {info}")
