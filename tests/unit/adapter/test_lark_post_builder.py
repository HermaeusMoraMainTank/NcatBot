"""
飞书 PostBuilder & message_array_to_post 测试

规范:
  LKP-01: LarkPostBuilder.text() 生成文本节点
  LKP-02: LarkPostBuilder.link() 生成超链接节点
  LKP-03: LarkPostBuilder.at() 生成 @用户 节点
  LKP-04: LarkPostBuilder.newline() 分行
  LKP-05: LarkPostBuilder 块级元素（hr/code_block/img/media/md）各自独占一行
  LKP-06: LarkPostBuilder.build() 输出正确的 JSON 结构
  LKP-07: LarkPostBuilder.build_dict() 输出字典
  LKP-08: message_array_to_post 将 MessageArray 转为 post JSON
  LKP-09: message_array_to_post 处理文本换行
  LKP-10: message_array_to_post At 段映射
"""

from __future__ import annotations

import json


from ncatbot.adapter.lark.post_builder import LarkPostBuilder, message_array_to_post
from ncatbot.types import MessageArray


# ---- LKP-01: text ----


class TestText:
    def test_lkp01_text_node(self):
        """LKP-01: LarkPostBuilder.text() 生成文本节点"""
        result = LarkPostBuilder("标题").text("hello").build_dict()
        content = result["zh_cn"]["content"]
        assert len(content) == 1  # 一行
        assert content[0][0] == {"tag": "text", "text": "hello"}

    def test_lkp01_text_with_styles(self):
        """LKP-01: LarkPostBuilder.text() 支持样式"""
        result = LarkPostBuilder().text("bold", styles=["bold"]).build_dict()
        content = result["zh_cn"]["content"]
        assert content[0][0] == {"tag": "text", "text": "bold", "style": ["bold"]}


# ---- LKP-02: link ----


class TestLink:
    def test_lkp02_link_node(self):
        """LKP-02: LarkPostBuilder.link() 生成超链接节点"""
        result = LarkPostBuilder().link("飞书", "https://www.feishu.cn").build_dict()
        content = result["zh_cn"]["content"]
        assert content[0][0] == {
            "tag": "a",
            "href": "https://www.feishu.cn",
            "text": "飞书",
        }


# ---- LKP-03: at ----


class TestAt:
    def test_lkp03_at_node(self):
        """LKP-03: LarkPostBuilder.at() 生成 @用户 节点"""
        result = LarkPostBuilder().at("ou_user001").build_dict()
        content = result["zh_cn"]["content"]
        assert content[0][0] == {"tag": "at", "user_id": "ou_user001"}


# ---- LKP-04: newline ----


class TestNewline:
    def test_lkp04_newline_splits_lines(self):
        """LKP-04: LarkPostBuilder.newline() 分行"""
        result = LarkPostBuilder().text("line1").newline().text("line2").build_dict()
        content = result["zh_cn"]["content"]
        assert len(content) == 2
        assert content[0][0]["text"] == "line1"
        assert content[1][0]["text"] == "line2"


# ---- LKP-05: 块级元素 ----


class TestBlockElements:
    def test_lkp05_hr(self):
        """LKP-05: hr 独占一行"""
        result = LarkPostBuilder().text("before").hr().text("after").build_dict()
        content = result["zh_cn"]["content"]
        assert len(content) == 3
        assert content[1] == [{"tag": "hr"}]

    def test_lkp05_code_block(self):
        """LKP-05: code_block 独占一行"""
        result = (
            LarkPostBuilder()
            .code_block("print('hello')", language="Python")
            .build_dict()
        )
        content = result["zh_cn"]["content"]
        assert content[0] == [
            {"tag": "code_block", "text": "print('hello')", "language": "Python"}
        ]

    def test_lkp05_img(self):
        """LKP-05: img 独占一行"""
        result = LarkPostBuilder().img("img_key_001").build_dict()
        content = result["zh_cn"]["content"]
        assert content[0] == [{"tag": "img", "image_key": "img_key_001"}]

    def test_lkp05_media(self):
        """LKP-05: media 独占一行"""
        result = LarkPostBuilder().media("file_key_001", "img_key_001").build_dict()
        content = result["zh_cn"]["content"]
        assert content[0] == [
            {"tag": "media", "file_key": "file_key_001", "image_key": "img_key_001"}
        ]

    def test_lkp05_md(self):
        """LKP-05: md 独占一行"""
        result = LarkPostBuilder().md("**bold**").build_dict()
        content = result["zh_cn"]["content"]
        assert content[0] == [{"tag": "md", "text": "**bold**"}]


# ---- LKP-06: build() 输出 JSON ----


class TestBuild:
    def test_lkp06_build_returns_valid_json(self):
        """LKP-06: LarkPostBuilder.build() 输出正确的 JSON 结构"""
        json_str = LarkPostBuilder("测试标题").text("内容").build()
        parsed = json.loads(json_str)
        assert parsed["zh_cn"]["title"] == "测试标题"
        assert len(parsed["zh_cn"]["content"]) == 1

    def test_lkp07_build_dict_returns_dict(self):
        """LKP-07: LarkPostBuilder.build_dict() 输出字典"""
        result = LarkPostBuilder("标题").text("内容").build_dict()
        assert isinstance(result, dict)
        assert "zh_cn" in result


# ---- LKP-08: message_array_to_post ----


class TestMessageArrayToPost:
    def test_lkp08_basic_conversion(self):
        """LKP-08: message_array_to_post 将 MessageArray 转为 post JSON"""
        msg = MessageArray().add_text("hello world")
        json_str = message_array_to_post(msg, title="测试")
        parsed = json.loads(json_str)
        assert parsed["zh_cn"]["title"] == "测试"
        content = parsed["zh_cn"]["content"]
        assert any(
            node.get("text") == "hello world" for line in content for node in line
        )

    def test_lkp09_text_with_newlines(self):
        """LKP-09: message_array_to_post 处理文本换行"""
        msg = MessageArray().add_text("line1\nline2\nline3")
        json_str = message_array_to_post(msg)
        parsed = json.loads(json_str)
        content = parsed["zh_cn"]["content"]
        # 换行应产生多行
        text_nodes = [
            node for line in content for node in line if node.get("tag") == "text"
        ]
        texts = [n["text"] for n in text_nodes]
        assert "line1" in texts
        assert "line2" in texts
        assert "line3" in texts

    def test_lkp10_at_segment(self):
        """LKP-10: message_array_to_post At 段映射"""
        msg = MessageArray().add_at("ou_user001")
        json_str = message_array_to_post(msg)
        parsed = json.loads(json_str)
        content = parsed["zh_cn"]["content"]
        at_nodes = [
            node for line in content for node in line if node.get("tag") == "at"
        ]
        assert len(at_nodes) == 1
        assert at_nodes[0]["user_id"] == "ou_user001"
