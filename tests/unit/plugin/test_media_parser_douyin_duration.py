"""Regression test for Douyin duration unit conversion."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _load_duration_module():
    spec = importlib.util.spec_from_file_location(
        "douyin_duration_under_test",
        ROOT
        / "plugins"
        / "MediaParser"
        / "core"
        / "parsers"
        / "douyin"
        / "duration.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_douyin_duration_ms_to_seconds():
    module = _load_duration_module()

    assert module.duration_ms_to_seconds(8058) == 8
    assert module.duration_ms_to_seconds(134300) == 134
    assert module.duration_ms_to_seconds(0) == 0
