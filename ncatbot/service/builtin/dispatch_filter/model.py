"""
分发过滤规则数据模型
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Literal, Optional


@dataclass
class FilterRule:
    """单条分发过滤规则。

    Attributes:
        rule_id: 规则唯一标识（自动生成）
        scope_type: 作用域类型  ``"group"`` 或 ``"user"``
        scope_id: 群号或用户号
        plugin_name: 目标插件名（``"*"`` = 匹配全部插件）
        commands: 目标命令列表（空列表 = 整个插件）
        action: 动作（当前仅 ``"deny"``，预留扩展）
    """

    scope_type: Literal["group", "user"]
    scope_id: str
    plugin_name: str
    commands: List[str] = field(default_factory=list)
    action: Literal["deny"] = "deny"
    rule_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    # ------------------------------------------------------------------
    # 匹配
    # ------------------------------------------------------------------

    def matches(
        self,
        plugin_name: str,
        command: Optional[str],
        group_id: Optional[str],
        user_id: Optional[str],
    ) -> bool:
        """判断本规则是否拦截给定上下文。"""
        # 1. 作用域匹配
        if self.scope_type == "group":
            if group_id is None or self.scope_id != group_id:
                return False
        else:  # user
            if user_id is None or self.scope_id != user_id:
                return False

        # 2. 插件匹配
        if self.plugin_name != "*" and self.plugin_name != plugin_name:
            return False

        # 3. 命令匹配（空 = 整个插件）
        if self.commands and command is not None:
            if command not in self.commands:
                return False
        elif self.commands and command is None:
            # 规则指定了命令，但当前 handler 不是命令类型 → 不拦截
            return False

        return True

    # ------------------------------------------------------------------
    # 序列化
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FilterRule":
        return cls(**data)

    def __repr__(self) -> str:
        cmds = ",".join(self.commands) if self.commands else "*"
        return (
            f"<FilterRule {self.rule_id} "
            f"{self.scope_type}:{self.scope_id} "
            f"plugin={self.plugin_name} cmds={cmds} "
            f"action={self.action}>"
        )
