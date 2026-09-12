"""Helpers for Douyin media metadata."""


def duration_ms_to_seconds(duration_ms: int) -> int:
    """Convert Douyin's millisecond duration to whole seconds."""
    return max(0, duration_ms // 1000)
