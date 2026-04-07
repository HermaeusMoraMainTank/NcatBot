"""QQ 消息段冲突检测与自动拆分

QQ 平台对同一 MessageArray 中同时出现多种"独占段"（Video / File / Forward）时，
会静默吞掉部分段甚至导致客户端崩溃。本模块提供检测与拆分工具，由 Sugar 层在发送前调用。

行为概述：
- Forward + 任何其他段 → 抛 QQSegmentConflictError（客户端崩溃级）
- Video / File 与其他段冲突 → 自动拆为多条消息 + warning 日志
- strict=True → 任何冲突均抛异常

详细测试结论见 memory/2026-04-02_qq_message_segment_conflicts.md
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Literal

from ncatbot.types import File, Image, MessageArray, MessageSegment, Reply, Video
from ncatbot.types.qq import Forward
from ncatbot.utils import get_log

__all__ = [
    "QQSegmentConflictError",
    "classify_segments",
    "detect_conflicts",
    "validate_and_prepare",
]

LOG = get_log("SegmentValidator")

# 段类别名
SegmentCategory = Literal["forward", "file", "video", "visual", "reply", "light"]

# ── 异常 ──────────────────────────────────────────────


class QQSegmentConflictError(ValueError):
    """QQ 消息段冲突异常

    当 MessageArray 中包含互相冲突的段（如 Forward + Text）时抛出。
    """


# ── 冲突信息 ──────────────────────────────────────────


@dataclass
class ConflictInfo:
    level: Literal["crash", "swallow"]
    description: str
    categories_involved: List[str] = field(default_factory=list)


# ── 段分类 ────────────────────────────────────────────


def _category_of(seg: MessageSegment) -> SegmentCategory:
    if isinstance(seg, Forward):
        return "forward"
    if isinstance(seg, File):
        return "file"
    if isinstance(seg, Video):
        return "video"
    if isinstance(seg, Image):
        return "visual"
    if isinstance(seg, Reply):
        return "reply"
    return "light"


def classify_segments(
    segments: List[MessageSegment],
) -> Dict[SegmentCategory, List[MessageSegment]]:
    """将段列表按类别分组"""
    groups: Dict[SegmentCategory, List[MessageSegment]] = {}
    for seg in segments:
        cat = _category_of(seg)
        groups.setdefault(cat, []).append(seg)
    return groups


# ── 冲突检测 ──────────────────────────────────────────


def detect_conflicts(segments: List[MessageSegment]) -> List[ConflictInfo]:
    """检测段列表中的冲突，返回所有冲突信息"""
    groups = classify_segments(segments)
    cats = set(groups.keys())
    conflicts: List[ConflictInfo] = []

    has_forward = "forward" in cats
    has_file = "file" in cats
    has_video = "video" in cats
    non_forward = cats - {"forward"}

    # Rule 1: Forward + 任何其他段 → crash
    if has_forward and non_forward:
        others = ", ".join(sorted(non_forward))
        conflicts.append(
            ConflictInfo(
                level="crash",
                description=(
                    f"Forward 段与 [{others}] 段共存于同一 MessageArray 会导致"
                    "客户端崩溃。Forward 必须通过 post_group_forward_msg 单独发送"
                ),
                categories_involved=["forward"] + sorted(non_forward),
            )
        )
        return conflicts  # crash 级直接返回，无需继续检测

    # Rule 2: Video + 非独占段 → swallow
    if has_video:
        swallowed = cats & {"light", "visual", "reply"}
        if swallowed:
            conflicts.append(
                ConflictInfo(
                    level="swallow",
                    description=(
                        f"Video 段会吞掉同一消息中的 [{', '.join(sorted(swallowed))}] 段"
                    ),
                    categories_involved=["video"] + sorted(swallowed),
                )
            )

    # Rule 3: Video + File → swallow (Video 被吞)
    if has_video and has_file:
        conflicts.append(
            ConflictInfo(
                level="swallow",
                description="Video 和 File 共存时，Video 会被吞掉",
                categories_involved=["video", "file"],
            )
        )

    # Rule 4: File + 非独占段 → swallow
    if has_file:
        swallowed = cats & {"light", "visual"}
        if swallowed:
            conflicts.append(
                ConflictInfo(
                    level="swallow",
                    description=(
                        f"File 段会吞掉同一消息中的 [{', '.join(sorted(swallowed))}] 段"
                    ),
                    categories_involved=["file"] + sorted(swallowed),
                )
            )
        # File + Reply → Reply 被吞
        if "reply" in cats:
            conflicts.append(
                ConflictInfo(
                    level="swallow",
                    description="File 段会吞掉 Reply 段",
                    categories_involved=["file", "reply"],
                )
            )

    # Rule 5: Reply + Video (无 File) → 视频被吞（双输）
    if "reply" in cats and has_video and not has_file:
        conflicts.append(
            ConflictInfo(
                level="swallow",
                description="Reply + Video 组合导致视频被吞（双输）",
                categories_involved=["reply", "video"],
            )
        )

    return conflicts


# ── 自动拆分 ──────────────────────────────────────────


def _split(segments: List[MessageSegment]) -> List[MessageArray]:
    """将冲突段按兼容性分组为多个 MessageArray"""
    groups = classify_segments(segments)

    # Group A: reply + light + visual（互相兼容）
    group_a: List[MessageSegment] = []
    for cat in ("reply", "light", "visual"):
        group_a.extend(groups.get(cat, []))

    # Group B: video（独占）
    group_b = groups.get("video", [])

    # Group C: file（独占）
    group_c = groups.get("file", [])

    # 如果 group_a 仅含 reply 且无可渲染内容段 → reply 无意义，丢弃
    if group_a:
        has_content = any(not isinstance(s, Reply) for s in group_a)
        if not has_content:
            LOG.warning("Reply 段因无可搭配的内容段被丢弃（独占段吞掉了所有内容段）")
            group_a = []

    result: List[MessageArray] = []
    if group_a:
        result.append(MessageArray(group_a))
    if group_b:
        result.append(MessageArray(group_b))
    if group_c:
        result.append(MessageArray(group_c))

    return result if result else [MessageArray()]


# ── 入口 ──────────────────────────────────────────────


def validate_and_prepare(
    msg: MessageArray,
    strict: bool = False,
) -> List[MessageArray]:
    """验证 MessageArray 并按需拆分

    Args:
        msg: 待发送的消息数组
        strict: 为 True 时任何冲突都抛出 QQSegmentConflictError

    Returns:
        拆分后的 MessageArray 列表（无冲突时为 ``[msg]``）

    Raises:
        QQSegmentConflictError: Forward 冲突（始终）或 strict 模式下的任何冲突
    """
    segments = msg.filter()
    if not segments:
        return [msg]

    conflicts = detect_conflicts(segments)
    if not conflicts:
        return [msg]

    # crash 级始终抛异常
    crash = [c for c in conflicts if c.level == "crash"]
    if crash:
        raise QQSegmentConflictError(crash[0].description)

    # strict 模式下 swallow 也抛异常
    if strict:
        descriptions = "; ".join(c.description for c in conflicts)
        raise QQSegmentConflictError(f"strict 模式检测到段冲突: {descriptions}")

    # 非 strict：自动拆分 + warning
    for c in conflicts:
        LOG.warning("消息段冲突（自动拆分）: %s", c.description)

    return _split(segments)


def check_forward_node_conflicts(forward: Forward) -> None:
    """检查 Forward 内每个 node 的段冲突，仅记录 warning"""
    if not forward.content:
        return
    for i, node in enumerate(forward.content):
        conflicts = detect_conflicts(list(node.content))
        for c in conflicts:
            LOG.warning(
                "转发消息 node[%d] (%s) 段冲突: %s", i, node.nickname, c.description
            )
