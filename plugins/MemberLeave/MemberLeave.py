"""群成员离开插件。

离开提示不再写死：notice 事件由 FakeAi 统一感知，由模型自己决定要不要说。
"""

from ncatbot.plugin import NcatBotPlugin
from ncatbot.utils import get_log

_log = get_log()


class MemberLeave(NcatBotPlugin):
    name = "MemberLeave"
    version = "1.1"
    author = "NcatBot"
    description = "群成员离开事件（表达由 FakeAi 负责）"

    usage_instructions = """成员离开功能：
1. 本插件不再发送固定的退群/被踢提示
2. 退群事件会进入 FakeAi 的上下文，由模型自由决定要不要接话
"""

    async def on_load(self):
        _log.info(f"{self.name} 插件加载完成：退群表达由 FakeAi 负责")
