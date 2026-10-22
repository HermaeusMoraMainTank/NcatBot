"""Skill：插件能力的统一描述层。"""

from .registry import SkillRegistry, skill
from .bridge import BRIDGE_TOOL_NAMES, BRIDGE_TOOLS, SkillBridge
from .spec import (
    Param,
    SkillContext,
    SkillDenied,
    SkillError,
    SkillNotFound,
    SkillResult,
    SkillSpec,
)

__all__ = [
    "SkillRegistry",
    "skill",
    "SkillBridge",
    "BRIDGE_TOOLS",
    "BRIDGE_TOOL_NAMES",
    "SkillSpec",
    "SkillResult",
    "SkillContext",
    "SkillError",
    "SkillDenied",
    "SkillNotFound",
    "Param",
]
