"""
CQ 码解析规范测试

来源: test_legacy/core/event/ (CQ 码 → OB11 格式转换逻辑)

规范:
  CQ-01: 纯文本不含 CQ 码 → 单个 text 段
  CQ-02: 单个 CQ 码 → 对应类型段
  CQ-03: 混合文本 + CQ 码 → 按序生成 text + CQ 段
  CQ-04: 多参数 CQ 码 → params 字典完整
  CQ-05: HTML 转义字符还原 (&amp; &#91; &#93; &#44;)
  CQ-06: 无参数 CQ 码 → data 为空字典
  CQ-07: 连续两个 CQ 码 → 中间无文本段
  CQ-08: 空字符串 → 空列表
"""

from ncatbot.types.qq.segment.cq import parse_cq_code_to_onebot11


class TestPureCQ:
    """CQ-01 ~ CQ-02: 基本解析"""

    def test_pure_text(self):
        result = parse_cq_code_to_onebot11("你好，世界！")
        assert len(result) == 1
        assert result[0] == {"type": "text", "data": {"text": "你好，世界！"}}

    def test_single_cq_at(self):
        result = parse_cq_code_to_onebot11("[CQ:at,qq=1234567]")
        assert len(result) == 1
        assert result[0] == {"type": "at", "data": {"qq": "1234567"}}

    def test_single_cq_face(self):
        result = parse_cq_code_to_onebot11("[CQ:face,id=178]")
        assert result == [{"type": "face", "data": {"id": "178"}}]


class TestMixedCQ:
    """CQ-03 ~ CQ-04: 混合与多参数"""

    def test_text_with_at(self):
        result = parse_cq_code_to_onebot11("hello [CQ:at,qq=123] world")
        assert len(result) == 3
        assert result[0] == {"type": "text", "data": {"text": "hello "}}
        assert result[1] == {"type": "at", "data": {"qq": "123"}}
        assert result[2] == {"type": "text", "data": {"text": " world"}}

    def test_multi_params(self):
        result = parse_cq_code_to_onebot11(
            "[CQ:image,file=abc.jpg,url=https://example.com/abc.jpg,sub_type=1]"
        )
        assert len(result) == 1
        assert result[0]["type"] == "image"
        assert result[0]["data"]["file"] == "abc.jpg"
        assert result[0]["data"]["url"] == "https://example.com/abc.jpg"
        assert result[0]["data"]["sub_type"] == "1"

    def test_multiple_cq_interleaved(self):
        cq = "前缀[CQ:at,qq=111]中间[CQ:face,id=14]后缀"
        result = parse_cq_code_to_onebot11(cq)
        assert len(result) == 5
        assert result[0]["data"]["text"] == "前缀"
        assert result[1]["data"]["qq"] == "111"
        assert result[2]["data"]["text"] == "中间"
        assert result[3]["data"]["id"] == "14"
        assert result[4]["data"]["text"] == "后缀"


class TestCQEscape:
    """CQ-05: HTML 转义还原"""

    def test_ampersand(self):
        result = parse_cq_code_to_onebot11("a&amp;b")
        assert result[0]["data"]["text"] == "a&b"

    def test_brackets(self):
        result = parse_cq_code_to_onebot11("&#91;hello&#93;")
        assert result[0]["data"]["text"] == "[hello]"

    def test_comma(self):
        result = parse_cq_code_to_onebot11("a&#44;b")
        assert result[0]["data"]["text"] == "a,b"

    def test_escaped_in_param(self):
        result = parse_cq_code_to_onebot11("[CQ:text,text=a&amp;b&#91;c&#93;]")
        assert result[0]["data"]["text"] == "a&b[c]"


class TestCQEdgeCases:
    """CQ-06 ~ CQ-08: 边界情况"""

    def test_no_params(self):
        result = parse_cq_code_to_onebot11("[CQ:shake]")
        assert len(result) == 1
        assert result[0] == {"type": "shake", "data": {}}

    def test_consecutive_cq_codes(self):
        result = parse_cq_code_to_onebot11("[CQ:at,qq=111][CQ:at,qq=222]")
        assert len(result) == 2
        assert result[0]["data"]["qq"] == "111"
        assert result[1]["data"]["qq"] == "222"

    def test_empty_string(self):
        result = parse_cq_code_to_onebot11("")
        assert result == []
