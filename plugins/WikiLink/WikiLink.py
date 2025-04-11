import re
import ast
import html
import urllib.parse
from ncatbot.core.message import GroupMessage
from ncatbot.plugin import CompatibleEnrollment, BasePlugin

bot = CompatibleEnrollment


class WikiLink(BasePlugin):
    name = "WikiLink"
    version = "1.0"

    # 定义正则表达式模式
    REGEX_PATTERNS = {
        "wiki": re.compile(r"\[\[(?P<content>.*?)\]\]"),
        "lang": re.compile(r"\[\[:(?P<lang>[a-zA-Z0-9\-]+):(?P<entry>.*?)\]\]"),
        "template": re.compile(r"\{\{(?P<content>.*?)\}\}"),
        "template_colon": re.compile(r":\s*(?P<lang>[a-zA-Z0-9\-]+):(?P<template>.*)"),
    }

    usage_instructions = """维基百科链接生成器使用方法：
1. 基本格式：
   - [[关键词]] - 生成中文维基百科链接
   - [[:语言:关键词]] - 生成指定语言的维基百科链接
   - {{模板名}} - 生成中文维基百科模板链接
   - {{:语言:模板名}} - 生成指定语言的维基百科模板链接

示例：
- [[Python]] -> https://zh.wikipedia.org/wiki/Python
- [[:en:Python]] -> https://en.wikipedia.org/wiki/Python
- {{Infobox}} -> https://zh.wikipedia.org/wiki/Template:Infobox
- {{:en:Infobox}} -> https://en.wikipedia.org/wiki/Template:Infobox
"""
    def parse_raw_message(self, raw: str) -> str:
        """处理原始消息文本"""
        raw = html.unescape(raw)

        if not raw.lstrip().startswith("[{"):
            return raw

        try:
            parsed = ast.literal_eval(raw)
            if not isinstance(parsed, list):
                return str(parsed)

            return "".join(
                element.get("data", {}).get("text", "")
                if isinstance(element, dict) and element.get("type") == "text"
                else str(element)
                for element in parsed
            )
        except Exception:
            return raw

    def build_wiki_url(self, text: str) -> str:
        """构造维基百科URL"""

        # 尝试匹配 [[:lang:entry]] 格式
        if match := self.REGEX_PATTERNS["lang"].search(text):
            lang = match.group("lang").strip()
            entry = match.group("entry").strip().replace(" ", "_")
            return f"https://{lang}.wikipedia.org/wiki/{urllib.parse.quote(entry)}"

            # 尝试匹配 [[keyword]] 格式
        if match := self.REGEX_PATTERNS["wiki"].search(text):
            keyword = match.group("content").strip().replace(" ", "_")
            return f"https://zh.wikipedia.org/wiki/{urllib.parse.quote(keyword)}"

        # 尝试匹配模板格式
        if match := self.REGEX_PATTERNS["template"].search(text):
            content = match.group("content").strip()
            if colon_match := self.REGEX_PATTERNS["template_colon"].search(content):
                lang = colon_match.group("lang").strip()
                template = colon_match.group("template").strip().replace(" ", "_")
                return f"https://{lang}.wikipedia.org/wiki/Template:{urllib.parse.quote(template)}"
            else:
                template = content.replace(" ", "_")
                return f"https://zh.wikipedia.org/wiki/Template:{urllib.parse.quote(template)}"

        return ""

    @bot.group_event()
    async def handle_wiki_link(self, input: GroupMessage):
        """处理群消息"""
        message_parts = input.raw_message.split(" ")
        if message_parts[0] != "wiki":
            return

        if len(message_parts) == 1:
            await self.api.post_group_msg(
                group_id=input.group_id, text=self.usage_instructions
            )
            return

        text_content = self.parse_raw_message(" ".join(message_parts[1:]))
        wiki_url = self.build_wiki_url(text_content)
        if wiki_url:
            try:
                await self.api.post_group_msg(group_id=input.group_id, text=wiki_url)
            except Exception as e:
                error_message = (
                    f"生成维基链接时出错了喵: {str(e)}\n\n{self.usage_instructions}"
                )
                await self.api.post_group_msg(
                    group_id=input.group_id, text=error_message
                )
