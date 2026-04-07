"""
消息段解析规范测试 (使用 parse_segment)

来源: test_legacy/core/event/ (适配新架构)

规范:
  S-01: parse_segment 解析文本段
  S-02: parse_segment 解析 face 段 (id 自动转 str)
  S-03: parse_segment 解析 at 段 (含 at all)
  S-04: parse_segment 解析 reply 段
  S-05: parse_segment 解析 image 段 (含可选字段)
  S-06: parse_segment 解析 record / video / file 段
  S-07: parse_segment 对未知 type 抛 ValueError
  S-08: 混合消息序列化/反序列化往返
  S-09: 空 data、额外字段的容错 (extra="allow")
  S-10: SEGMENT_MAP 包含所有已注册类型
"""

import pytest

from ncatbot.types.common.segment.base import SEGMENT_MAP, parse_segment
from ncatbot.types.common.segment.text import At, PlainText, Reply
from ncatbot.types.common.segment.media import Image, Record, Video, File
from ncatbot.types.qq.segment import Face


# ===========================================================================
# S-10: SEGMENT_MAP 完整性
# ===========================================================================


class TestSegmentMapCompleteness:
    def test_all_known_types_registered(self):
        expected = {
            "text",
            "face",
            "at",
            "reply",
            "image",
            "record",
            "video",
            "file",
            "share",
            "location",
            "music",
            "json",
            "markdown",
            "forward",
        }
        assert expected.issubset(set(SEGMENT_MAP.keys()))


# ===========================================================================
# S-01 ~ S-06: parse_segment 解析各类型
# ===========================================================================


class TestParseSegmentBasicTypes:
    """S-01 ~ S-04: 文本类消息段"""

    def test_parse_text(self):
        seg = parse_segment({"type": "text", "data": {"text": "hello world"}})
        assert isinstance(seg, PlainText)
        assert seg.text == "hello world"

    def test_parse_text_unicode(self):
        seg = parse_segment({"type": "text", "data": {"text": "你好🌍"}})
        assert seg.text == "你好🌍"

    def test_parse_face(self):
        seg = parse_segment({"type": "face", "data": {"id": 178}})
        assert isinstance(seg, Face)
        assert seg.id == "178"  # 自动 int → str

    def test_parse_face_str_id(self):
        seg = parse_segment({"type": "face", "data": {"id": "14"}})
        assert seg.id == "14"

    def test_parse_at_user(self):
        seg = parse_segment({"type": "at", "data": {"qq": "1234567"}})
        assert isinstance(seg, At)
        assert seg.user_id == "1234567"

    def test_parse_at_all(self):
        seg = parse_segment({"type": "at", "data": {"qq": "all"}})
        assert seg.user_id == "all"

    def test_parse_at_int_qq(self):
        seg = parse_segment({"type": "at", "data": {"qq": 1234567}})
        assert seg.user_id == "1234567"

    def test_parse_reply(self):
        seg = parse_segment({"type": "reply", "data": {"id": 2009890763}})
        assert isinstance(seg, Reply)
        assert seg.id == "2009890763"


class TestParseSegmentMedia:
    """S-05 ~ S-06: 媒体类消息段"""

    def test_parse_image_minimal(self):
        seg = parse_segment({"type": "image", "data": {"file": "abc.jpg"}})
        assert isinstance(seg, Image)
        assert seg.file == "abc.jpg"
        assert seg.url is None

    def test_parse_image_with_url(self):
        seg = parse_segment(
            {
                "type": "image",
                "data": {
                    "file": "abc.jpg",
                    "url": "https://example.com/abc.jpg",
                    "sub_type": 1,
                },
            }
        )
        assert seg.url == "https://example.com/abc.jpg"
        assert seg.sub_type == 1

    def test_parse_record(self):
        seg = parse_segment(
            {
                "type": "record",
                "data": {"file": "voice.amr", "magic": 1},
            }
        )
        assert isinstance(seg, Record)
        assert seg.magic == 1

    def test_parse_video(self):
        seg = parse_segment({"type": "video", "data": {"file": "clip.mp4"}})
        assert isinstance(seg, Video)

    def test_parse_file(self):
        seg = parse_segment(
            {
                "type": "file",
                "data": {"file": "doc.pdf", "file_size": 1024},
            }
        )
        assert isinstance(seg, File)
        assert seg.file_size == 1024


# ===========================================================================
# S-07: 错误处理
# ===========================================================================


class TestParseSegmentErrors:
    def test_unknown_type_raises(self):
        with pytest.raises(ValueError, match="Unknown segment type"):
            parse_segment({"type": "nonexistent", "data": {}})

    def test_empty_type_raises(self):
        with pytest.raises(ValueError, match="Unknown segment type"):
            parse_segment({"data": {"text": "no type"}})


# ===========================================================================
# S-08: 序列化/反序列化往返
# ===========================================================================


class TestSegmentRoundTrip:
    @pytest.mark.parametrize(
        "raw",
        [
            {"type": "text", "data": {"text": "hello"}},
            {"type": "face", "data": {"id": "14"}},
            {"type": "at", "data": {"qq": "1234567"}},
            {"type": "reply", "data": {"id": "999"}},
            {"type": "image", "data": {"file": "img.png"}},
        ],
        ids=["text", "face", "at", "reply", "image"],
    )
    def test_roundtrip(self, raw):
        seg = parse_segment(raw)
        dumped = seg.to_dict()
        assert dumped["type"] == raw["type"]
        restored = parse_segment(dumped)
        assert type(restored) is type(seg)
        # At 的 wire key 是 "qq"，字段名是 "user_id"
        alias_map = {"qq": "user_id"}
        for k, v in raw["data"].items():
            attr = alias_map.get(k, k)
            assert getattr(restored, attr) == v


# ===========================================================================
# S-09: 容错
# ===========================================================================


class TestSegmentTolerance:
    def test_extra_fields_allowed(self):
        """extra='allow' 不应报错"""
        seg = parse_segment(
            {
                "type": "text",
                "data": {"text": "hi", "unknown_field": 42},
            }
        )
        assert seg.text == "hi"

    def test_empty_data(self):
        """data 缺少必填字段时应正常抛 ValidationError"""
        with pytest.raises(Exception):  # Pydantic ValidationError
            parse_segment({"type": "text", "data": {}})


# ===========================================================================
# 混合消息 — 模拟真实 OB11 message 字段
# ===========================================================================


class TestMixedMessageParsing:
    """模拟真实消息中 message 数组的解析"""

    def test_mixed_text_at_image(self):
        raw_message = [
            {"type": "at", "data": {"qq": "1234567"}},
            {"type": "text", "data": {"text": " 看这张图 "}},
            {"type": "image", "data": {"file": "photo.jpg"}},
        ]
        segments = [parse_segment(seg) for seg in raw_message]
        assert len(segments) == 3
        assert isinstance(segments[0], At)
        assert isinstance(segments[1], PlainText)
        assert isinstance(segments[2], Image)

    def test_reply_with_text(self):
        raw_message = [
            {"type": "reply", "data": {"id": "12345"}},
            {"type": "text", "data": {"text": "回复内容"}},
        ]
        segments = [parse_segment(seg) for seg in raw_message]
        assert isinstance(segments[0], Reply)
        assert segments[0].id == "12345"
        assert segments[1].text == "回复内容"
