"""
消息段体系规范测试

规范:
  T-02: to_dict() 输出 OB11 格式
  T-03: parse_segment() 反序列化
  T-04: 类型注册机制
  T-05: MessageArray 链式构造
"""

import pytest

from ncatbot.types.common.segment.base import parse_segment, SEGMENT_MAP
from ncatbot.types.common.segment.text import PlainText, At, Reply
from ncatbot.types.common.segment.media import Image, Record, Video
from ncatbot.types.qq.segment import Face


# ---- T-02: to_dict() 输出 OB11 格式 ----


def test_plaintext_to_dict():
    """T-02: PlainText.to_dict() → {"type": "text", "data": {"text": ...}}"""
    seg = PlainText(text="hello world")
    d = seg.to_dict()
    assert d == {"type": "text", "data": {"text": "hello world"}}


def test_at_to_dict():
    """T-02: At.to_dict() → {"type": "at", "data": {"qq": ...}}"""
    seg = At(user_id="12345")
    d = seg.to_dict()
    assert d["type"] == "at"
    assert d["data"]["qq"] == "12345"


def test_face_to_dict():
    """T-02: Face.to_dict() → {"type": "face", "data": {"id": ...}}"""
    seg = Face(id="123")
    d = seg.to_dict()
    assert d["type"] == "face"
    assert d["data"]["id"] == "123"


def test_image_to_dict():
    """T-02: Image.to_dict() → {"type": "image", "data": {"file": ...}}"""
    seg = Image(file="http://example.com/img.jpg")
    d = seg.to_dict()
    assert d["type"] == "image"
    assert d["data"]["file"] == "http://example.com/img.jpg"


# ---- T-03: parse_segment() 反序列化 ----


def test_parse_text_segment():
    """T-03: 从 dict 还原 PlainText"""
    seg = parse_segment({"type": "text", "data": {"text": "hi"}})
    assert isinstance(seg, PlainText)
    assert seg.text == "hi"


def test_parse_at_segment():
    """T-03: 从 dict 还原 At"""
    seg = parse_segment({"type": "at", "data": {"qq": "all"}})
    assert isinstance(seg, At)
    assert seg.user_id == "all"


def test_parse_reply_segment():
    """T-03: 从 dict 还原 Reply"""
    seg = parse_segment({"type": "reply", "data": {"id": "42"}})
    assert isinstance(seg, Reply)
    assert seg.id == "42"


def test_parse_unknown_type_raises():
    """T-03 补充: 未知类型抛 ValueError"""
    with pytest.raises(ValueError, match="Unknown segment type"):
        parse_segment({"type": "unknown_segment", "data": {}})


# ---- T-04: 类型注册机制 ----


def test_segment_type_registration():
    """T-04: _type ClassVar 自动注册到 SEGMENT_MAP"""
    assert "text" in SEGMENT_MAP
    assert SEGMENT_MAP["text"] is PlainText
    assert "at" in SEGMENT_MAP
    assert SEGMENT_MAP["at"] is At
    assert "face" in SEGMENT_MAP
    assert SEGMENT_MAP["face"] is Face
    assert "image" in SEGMENT_MAP
    assert issubclass(SEGMENT_MAP["image"], Image)
    assert "record" in SEGMENT_MAP
    assert issubclass(SEGMENT_MAP["record"], Record)
    assert "video" in SEGMENT_MAP
    assert SEGMENT_MAP["video"] is Video


def test_parse_image_from_registered_type():
    """T-04 补充: parse_segment 使用注册表还原 Image"""
    seg = parse_segment({"type": "image", "data": {"file": "test.jpg"}})
    assert isinstance(seg, Image)
    assert seg.file == "test.jpg"


# ---- T-05: MessageArray ----


def test_message_array_from_list():
    """T-05: MessageArray.from_list 从 OB11 列表构造"""
    from ncatbot.types.common.segment.array import MessageArray

    arr = MessageArray.from_list(
        [
            {"type": "text", "data": {"text": "hi "}},
            {"type": "at", "data": {"qq": "123"}},
        ]
    )
    assert len(arr) == 2
    segments = list(arr)
    assert isinstance(segments[0], PlainText)
    assert isinstance(segments[1], At)


def test_message_array_iter():
    """T-05 补充: MessageArray 支持迭代"""
    from ncatbot.types.common.segment.array import MessageArray

    arr = MessageArray([PlainText(text="a"), PlainText(text="b")])
    texts = [seg.text for seg in arr]
    assert texts == ["a", "b"]
