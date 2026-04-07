"""
Forward / ForwardNode 单元测试

FW-01 ~ FW-03：覆盖反序列化、序列化、内容解析。
"""

from ncatbot.types.qq.segment.forward import Forward, ForwardNode
from ncatbot.types.common.segment.text import PlainText


class TestFromDict:
    """FW-01: Forward.from_dict 反序列化"""

    def test_fw01_legacy_api_format(self):
        """FW-01: from_dict 解析 legacy API 格式（message/sender 键）"""
        raw = {
            "type": "forward",
            "data": {
                "content": [
                    {
                        "user_id": "111",
                        "sender": {"nickname": "Alice"},
                        "message": [{"type": "text", "data": {"text": "hello"}}],
                    },
                    {
                        "user_id": "222",
                        "sender": {"nickname": "Bob"},
                        "message": [{"type": "text", "data": {"text": "world"}}],
                    },
                ]
            },
        }
        fwd = Forward.from_dict(raw)
        assert fwd.content is not None
        assert len(fwd.content) == 2
        assert fwd.content[0].nickname == "Alice"
        assert fwd.content[1].user_id == "222"

    def test_fw01_id_only(self):
        """FW-01: from_dict 仅有 id 时 content 为 None"""
        raw = {"type": "forward", "data": {"id": "abc123"}}
        fwd = Forward.from_dict(raw)
        assert fwd.id == "abc123"
        assert fwd.content is None


class TestToDict:
    """FW-02: Forward.to_dict 序列化"""

    def test_fw02_content_mode(self):
        """FW-02: to_dict 有 content 时输出 node 列表"""
        node = ForwardNode(
            user_id="111",
            nickname="Alice",
            content=[PlainText(text="hi")],
        )
        fwd = Forward(content=[node])
        d = fwd.to_dict()
        assert d["type"] == "forward"
        assert "content" in d["data"]
        assert len(d["data"]["content"]) == 1
        assert d["data"]["content"][0]["type"] == "node"

    def test_fw02_id_mode(self):
        """FW-02: to_dict 仅 id 时输出 id"""
        fwd = Forward(id="xyz")
        d = fwd.to_dict()
        assert d == {"type": "forward", "data": {"id": "xyz"}}


class TestForwardNodeContent:
    """FW-03: ForwardNode.content 解析 list[dict]"""

    def test_fw03_parse_raw_dicts(self):
        """FW-03: content 字段接受 list[dict] 并解析为 MessageSegment"""
        node = ForwardNode(
            user_id="333",
            nickname="Charlie",
            content=[
                {"type": "text", "data": {"text": "parsed"}},
                {"type": "image", "data": {"file": "test.png"}},
            ],
        )
        assert len(node.content) == 2
        assert isinstance(node.content[0], PlainText)
        assert node.content[0].text == "parsed"
