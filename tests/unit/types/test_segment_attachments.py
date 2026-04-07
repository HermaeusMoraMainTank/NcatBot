"""
消息段附件桥接测试 — MessageArray.get_attachments()

规范:
  SEG-01: 纯文本消息 get_attachments() 返回空 AttachmentList
"""

from __future__ import annotations

from ncatbot.types.common.attachment_list import AttachmentList
from ncatbot.types.common.segment.array import MessageArray
from ncatbot.types.common.segment.text import PlainText


# ---- SEG-01: 纯文本消息返回空 ----


def test_seg01_text_only_empty():
    """SEG-01: 纯文本消息返回空 AttachmentList"""
    ma = MessageArray([PlainText(text="hello")])
    atts = ma.get_attachments()
    assert isinstance(atts, AttachmentList)
    assert len(atts) == 0
