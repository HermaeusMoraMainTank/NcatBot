"""
MessageArray 单元测试

MA-01 ~ MA-04：覆盖查询、属性、链式构造。
"""

from ncatbot.types.common.segment.array import MessageArray
from ncatbot.types.common.segment.text import PlainText, At
from ncatbot.types.common.segment.media import Image


class TestFilter:
    """MA-01: filter 按类型过滤"""

    def test_ma01_filter_plain_text(self):
        """MA-01: filter(PlainText) 只返回纯文本段"""
        arr = MessageArray(
            [PlainText(text="hi"), At(user_id="123"), PlainText(text="!")]
        )
        result = arr.filter(PlainText)
        assert len(result) == 2
        assert all(isinstance(s, PlainText) for s in result)


class TestIsAt:
    """MA-02: is_at 判断 @"""

    def test_ma02_is_at_specific_user(self):
        """MA-02: is_at(user_id) 精确匹配"""
        arr = MessageArray([At(user_id="123"), PlainText(text="hello")])
        assert arr.is_at("123") is True
        assert arr.is_at("999") is False

    def test_ma02_is_at_all(self):
        """MA-02: @all 被 is_at 检测到"""
        arr = MessageArray([At(user_id="all")])
        assert arr.is_at("123") is True
        # all_except=True 时 @all 不算
        assert arr.is_at("123", all_except=True) is False


class TestTextProperty:
    """MA-03: text 属性拼接"""

    def test_ma03_text_concatenation(self):
        """MA-03: .text 拼接所有 PlainText 段"""
        arr = MessageArray(
            [PlainText(text="hello "), At(user_id="1"), PlainText(text="world")]
        )
        assert arr.text == "hello world"

    def test_ma03_text_empty(self):
        """MA-03: 无文本段时 .text 为空串"""
        arr = MessageArray([At(user_id="1"), Image(file="x.png")])
        assert arr.text == ""


class TestChainBuild:
    """MA-04: 链式构造"""

    def test_ma04_chain(self):
        """MA-04: add_text().add_image().add_at() 链式调用后段数正确"""
        arr = (
            MessageArray().add_text("hello").add_image("file:///test.png").add_at("456")
        )
        assert len(arr) == 3
        types = [seg.to_dict()["type"] for seg in arr]
        assert types == ["text", "image", "at"]

    def test_ma04_add_reply(self):
        """MA-04: add_reply 产生 reply 段"""
        arr = MessageArray().add_reply("789")
        assert len(arr) == 1
        assert arr.to_list()[0]["type"] == "reply"
