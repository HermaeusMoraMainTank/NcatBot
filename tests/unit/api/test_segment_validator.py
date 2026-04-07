"""
段冲突验证器单元测试

SV-01 ~ SV-17：覆盖 classify_segments / detect_conflicts / validate_and_prepare
"""

import pytest

from ncatbot.api.qq.segment_validator import (
    QQSegmentConflictError,
    classify_segments,
    detect_conflicts,
    validate_and_prepare,
)
from ncatbot.types import File, Image, MessageArray, PlainText, Video
from ncatbot.types.common.segment.text import At, Reply
from ncatbot.types.qq.segment.forward import Forward


# ── helpers ───────────────────────────────────────────


def _ma(*segs):
    """快速构造 MessageArray"""
    return MessageArray(list(segs))


def _types(parts):
    """提取拆分结果中每个 MessageArray 包含的段类型集合"""
    return [{type(s).__name__ for s in part.filter()} for part in parts]


# ── classify_segments ─────────────────────────────────


class TestClassifySegments:
    """SV-16: classify_segments 单独测试"""

    def test_sv16_classify_all_categories(self):
        """SV-16: 每种段类型归入正确类别"""
        segs = [
            PlainText(text="hi"),
            At(user_id="1"),
            Reply(id="100"),
            Image(file="a.jpg"),
            Video(file="b.mp4"),
            File(file="c.txt"),
            Forward(content=[]),
        ]
        groups = classify_segments(segs)
        assert "light" in groups
        assert "reply" in groups
        assert "visual" in groups
        assert "video" in groups
        assert "file" in groups
        assert "forward" in groups
        assert len(groups["light"]) == 2  # PlainText + At


# ── detect_conflicts ──────────────────────────────────


class TestDetectConflicts:
    def test_no_conflict(self):
        """Text + Image + Reply 无冲突"""
        conflicts = detect_conflicts(
            [PlainText(text="hi"), Image(file="a.jpg"), Reply(id="1")]
        )
        assert conflicts == []

    def test_forward_crash(self):
        """Forward + Text → crash 级冲突"""
        conflicts = detect_conflicts([Forward(content=[]), PlainText(text="hi")])
        assert len(conflicts) == 1
        assert conflicts[0].level == "crash"

    def test_video_swallows_text(self):
        """Video + Text → swallow"""
        conflicts = detect_conflicts([Video(file="v.mp4"), PlainText(text="hi")])
        assert any(c.level == "swallow" for c in conflicts)

    def test_file_swallows_reply(self):
        """File + Reply → swallow"""
        conflicts = detect_conflicts([File(file="f.txt"), Reply(id="1")])
        assert any(c.level == "swallow" for c in conflicts)

    def test_video_and_file(self):
        """Video + File → swallow（Video 被吞）"""
        conflicts = detect_conflicts([Video(file="v.mp4"), File(file="f.txt")])
        assert any(
            "video" in c.categories_involved and "file" in c.categories_involved
            for c in conflicts
        )


# ── validate_and_prepare ──────────────────────────────


class TestValidateAndPrepare:
    def test_sv01_no_conflict(self):
        """SV-01: 无冲突组合（Text + Image + Reply）→ 返回 [原msg]"""
        msg = _ma(PlainText(text="hi"), Image(file="a.jpg"), Reply(id="1"))
        parts = validate_and_prepare(msg)
        assert len(parts) == 1
        assert parts[0] is msg

    def test_sv02_forward_text_crash(self):
        """SV-02: Forward + Text → 抛 QQSegmentConflictError"""
        msg = _ma(Forward(content=[]), PlainText(text="hi"))
        with pytest.raises(QQSegmentConflictError):
            validate_and_prepare(msg)

    def test_sv03_forward_file_crash(self):
        """SV-03: Forward + File → 抛"""
        msg = _ma(Forward(content=[]), File(file="f.txt"))
        with pytest.raises(QQSegmentConflictError):
            validate_and_prepare(msg)

    def test_sv04_forward_alone_ok(self):
        """SV-04: Forward 单独 → 无冲突"""
        msg = _ma(Forward(content=[]))
        parts = validate_and_prepare(msg)
        assert len(parts) == 1

    def test_sv05_video_text_split(self):
        """SV-05: Video + Text → 拆为 [Text_msg, Video_msg]"""
        msg = _ma(Video(file="v.mp4"), PlainText(text="hi"))
        parts = validate_and_prepare(msg)
        assert len(parts) == 2
        types = _types(parts)
        assert {"PlainText"} in types
        assert {"Video"} in types

    def test_sv06_file_text_split(self):
        """SV-06: File + Text → 拆为 [Text_msg, File_msg]"""
        msg = _ma(File(file="f.txt"), PlainText(text="hi"))
        parts = validate_and_prepare(msg)
        assert len(parts) == 2
        types = _types(parts)
        assert {"PlainText"} in types
        assert {"File"} in types

    def test_sv07_file_reply_discard(self):
        """SV-07: File + Reply → Reply 被丢弃（仅 [File_msg]）"""
        msg = _ma(File(file="f.txt"), Reply(id="1"))
        parts = validate_and_prepare(msg)
        # Reply 单独无内容 → 被丢弃，仅剩 File
        assert len(parts) == 1
        types = _types(parts)
        assert {"File"} in types

    def test_sv08_reply_video_discard(self):
        """SV-08: Reply + Video → Reply 丢弃，仅 [Video_msg]"""
        msg = _ma(Reply(id="1"), Video(file="v.mp4"))
        parts = validate_and_prepare(msg)
        assert len(parts) == 1
        types = _types(parts)
        assert {"Video"} in types

    def test_sv09_reply_video_text_split(self):
        """SV-09: Reply + Video + Text → [Reply+Text_msg, Video_msg]"""
        msg = _ma(Reply(id="1"), Video(file="v.mp4"), PlainText(text="hi"))
        parts = validate_and_prepare(msg)
        assert len(parts) == 2
        types = _types(parts)
        assert {"Reply", "PlainText"} in types
        assert {"Video"} in types

    def test_sv10_video_file_split(self):
        """SV-10: Video + File → 拆为 [Video_msg, File_msg]"""
        msg = _ma(Video(file="v.mp4"), File(file="f.txt"))
        parts = validate_and_prepare(msg)
        assert len(parts) == 2
        types = _types(parts)
        assert {"Video"} in types
        assert {"File"} in types

    def test_sv11_file_image_split(self):
        """SV-11: File + Image → [Image_msg, File_msg]"""
        msg = _ma(File(file="f.txt"), Image(file="a.jpg"))
        parts = validate_and_prepare(msg)
        assert len(parts) == 2
        types = _types(parts)
        assert {"Image"} in types
        assert {"File"} in types

    def test_sv12_video_image_text_reply_split(self):
        """SV-12: Video + Image + Text + Reply → [Reply+Text+Image, Video]"""
        msg = _ma(
            Video(file="v.mp4"),
            Image(file="a.jpg"),
            PlainText(text="hi"),
            Reply(id="1"),
        )
        parts = validate_and_prepare(msg)
        assert len(parts) == 2
        types = _types(parts)
        assert {"Reply", "PlainText", "Image"} in types
        assert {"Video"} in types

    def test_sv13_multi_file_no_split(self):
        """SV-13: 多 File → 返回 [原msg]（平台自动拆）"""
        msg = _ma(File(file="a.txt"), File(file="b.txt"))
        parts = validate_and_prepare(msg)
        assert len(parts) == 1
        assert parts[0] is msg

    def test_sv14_strict_video_text_raises(self):
        """SV-14: strict=True + Video + Text → 抛异常"""
        msg = _ma(Video(file="v.mp4"), PlainText(text="hi"))
        with pytest.raises(QQSegmentConflictError, match="strict"):
            validate_and_prepare(msg, strict=True)

    def test_sv15_strict_forward_text_raises(self):
        """SV-15: strict=True + Forward + Text → 抛异常"""
        msg = _ma(Forward(content=[]), PlainText(text="hi"))
        with pytest.raises(QQSegmentConflictError):
            validate_and_prepare(msg, strict=True)

    def test_sv17_empty_message(self):
        """SV-17: 空 MessageArray → 返回 [原msg]"""
        msg = MessageArray()
        parts = validate_and_prepare(msg)
        assert len(parts) == 1
        assert parts[0] is msg
