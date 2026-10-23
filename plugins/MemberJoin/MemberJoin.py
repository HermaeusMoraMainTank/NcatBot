"""群成员加入插件。

欢迎语不再写死：notice 事件由 FakeAi 统一感知，由模型自己决定说不说话、
说什么。本插件只保留加载钩子，避免和蓝晴抢话。
"""

from ncatbot.plugin import NcatBotPlugin
from ncatbot.utils import get_log

_log = get_log()


class MemberJoin(NcatBotPlugin):
    name = "MemberJoin"
    version = "1.1"
    author = "NcatBot"
    description = "群成员加入事件（表达由 FakeAi 负责）"

    usage_instructions = """成员加入功能：
1. 本插件不再发送固定欢迎语
2. 入群事件会进入 FakeAi 的上下文，由模型自由决定要不要招呼
"""

    async def on_load(self):
        _log.info(f"{self.name} 插件加载完成：入群表达由 FakeAi 负责")
