import logging
import re
import requests
from typing import List, Dict, Any
from ncatbot.core import GroupMessage
from ncatbot.plugin_system import NcatBotPlugin, on_message

log = logging.getLogger(__name__)


class Nbnhhsh(NcatBotPlugin):
    name = "Nbnhhsh"  # 插件名称
    version = "1.0"  # 插件版本
    api_url = "https://lab.magiconch.com/api/nbnhhsh/"
    cache = {}  # 缓存翻译结果

    async def on_load(self):
        """异步加载插件"""
        log.info(f"开始加载 {self.name} 插件 v{self.version}")
        log.info(f"{self.name} 插件加载完成")

    @on_message
    async def handle_translate(self, input: GroupMessage):
        """处理翻译请求"""
        message = input.raw_message.strip()

        # 检查是否以"翻译"开头
        if not message.startswith("翻译 "):
            return

        # 提取要翻译的内容
        content = message[3:].strip()  # 去掉"翻译 "前缀

        if not content:
            await self.api.post_group_msg(
                group_id=input.group_id,
                text="请提供要翻译的内容！\n使用方法：翻译 要翻译的内容",
            )
            return

        try:
            # 提取所有可能的缩写词
            abbreviations = self.extract_abbreviations(content)

            if not abbreviations:
                await self.api.post_group_msg(
                    group_id=input.group_id, text="未找到需要翻译的缩写词！"
                )
                return

            # 获取翻译结果
            translations = await self.get_translations(abbreviations)

            # 构建回复消息
            result_message = self.build_result_message(content, translations)

            await self.api.post_group_msg(group_id=input.group_id, text=result_message)

        except Exception as e:
            log.error(f"翻译处理出错: {str(e)}")
            await self.api.post_group_msg(
                group_id=input.group_id, text=f"翻译失败：{str(e)}"
            )

    def extract_abbreviations(self, text: str) -> List[str]:
        """提取文本中的缩写词"""
        # 匹配2个或更多字母数字组合的缩写词
        pattern = r"[a-zA-Z0-9]{2,}"
        matches = re.findall(pattern, text)
        return list(set(matches))  # 去重

    async def get_translations(self, abbreviations: List[str]) -> Dict[str, Any]:
        """获取缩写词的翻译"""
        results = {}

        for abbr in abbreviations:
            # 检查缓存
            if abbr in self.cache:
                results[abbr] = self.cache[abbr]
                continue

            try:
                # 调用API获取翻译
                response = requests.post(
                    f"{self.api_url}guess",
                    json={"text": abbr},
                    headers={"Content-Type": "application/json"},
                    timeout=10,
                )
                response.raise_for_status()

                data = response.json()
                results[abbr] = data

                # 缓存结果
                self.cache[abbr] = data

            except Exception as e:
                log.error(f"获取翻译失败 {abbr}: {str(e)}")
                results[abbr] = None

        return results

    def build_result_message(
        self, original_text: str, translations: Dict[str, Any]
    ) -> str:
        """构建结果消息"""
        if not translations:
            return "未找到任何翻译结果"

        result_lines = [f"原文：{original_text}", ""]

        for abbr, data in translations.items():
            if data is None:
                result_lines.append(f"❌ {abbr}: 翻译失败")
                continue

            if not data:  # 空结果
                result_lines.append(f"❓ {abbr}: 暂无翻译")
                continue

            # 处理每个翻译结果
            for item in data:
                name = item.get("name", abbr)
                trans = item.get("trans")
                inputting = item.get("inputting")

                if trans:
                    # 有翻译结果
                    trans_text = self.format_translations(trans)
                    result_lines.append(f"✅ {name}: {trans_text}")
                elif inputting:
                    # 有猜测结果
                    guess_text = "、".join(inputting)
                    result_lines.append(f"🤔 {name}: 可能是 {guess_text}")
                else:
                    # 无结果
                    result_lines.append(f"❓ {name}: 暂无翻译")

        return "\n".join(result_lines)

    def format_translations(self, trans: List[str]) -> str:
        """格式化翻译结果"""
        formatted = []
        for t in trans:
            # 处理带括号的翻译（来源说明）
            match = re.match(r"^(.+?)([（\(](.+?)[）\)])?$", t)
            if match and len(match.groups()) >= 2 and match.group(2):
                text = match.group(1)
                source = match.group(2)
                formatted.append(f"{text}{source}")
            else:
                formatted.append(t)
        return "、".join(formatted)
