"""戳一戳插件。

回复不再从固定话术里抽：poke 事件由 FakeAi 统一感知，由模型自由决定
回一句、戳回去，还是干脆不理。
"""

from ncatbot.plugin import NcatBotPlugin
from ncatbot.utils import get_log

_log = get_log()


class Poke(NcatBotPlugin):
    name = "Poke"
    version = "1.1"
    author = "NcatBot"
    description = "戳一戳事件（表达由 FakeAi 负责）"

    usage_instructions = """戳一戳功能：
1. 本插件不再随机回固定句子
2. 被戳事件会进入 FakeAi 的上下文，由模型自由决定怎么回应
"""

    async def on_load(self):
        _log.info(f"{self.name} 插件加载完成：戳一戳表达由 FakeAi 负责")
