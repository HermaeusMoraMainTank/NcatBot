"""SkillHub 的能力分组。"""

from .agents import AgentSkills
from .files_web import FileWebSkills
from .media import MediaSkills
from .messages import MessageSkills
from .plugin_catalog import PluginCatalogSkills
from .sandbox import SandboxSkills
from .tarot import TarotSkills

__all__ = [
    "AgentSkills",
    "FileWebSkills",
    "MediaSkills",
    "MessageSkills",
    "PluginCatalogSkills",
    "SandboxSkills",
    "TarotSkills",
]
