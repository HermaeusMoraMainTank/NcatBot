"""
Schedule Parser 单元测试

TS-01 ~ TS-06：覆盖各种时间格式的解析。
"""

import pytest

from ncatbot.service.builtin.schedule.parser import TimeTaskParser


class TestTimeTaskParser:
    """TS-01 ~ TS-06: 时间解析器"""

    def test_ts01_once_datetime(self):
        """TS-01: 一次性时间 'YYYY-MM-DD HH:MM:SS'"""
        result = TimeTaskParser.parse("2099-12-31 23:59:59")
        assert result["type"] == "once"
        assert isinstance(result["value"], float)
        assert result["value"] > 0

    def test_ts02_daily_time(self):
        """TS-02: 每日时间 'HH:MM'"""
        result = TimeTaskParser.parse("08:30")
        assert result["type"] == "daily"
        assert result["value"] == "08:30"

    def test_ts03_interval_seconds(self):
        """TS-03: 秒间隔 '120s'"""
        result = TimeTaskParser.parse("120s")
        assert result["type"] == "interval"
        assert result["value"] == 120

    def test_ts04_interval_compound(self):
        """TS-04: 冒号格式 '00:02:30' → 0d2h30m = 9000s"""
        result = TimeTaskParser.parse("00:02:30")
        assert result["type"] == "interval"
        assert result["value"] == 2 * 3600 + 30 * 60

    def test_ts05_chinese_interval(self):
        """TS-05: 中文间隔 '2天3小时5秒'"""
        result = TimeTaskParser.parse("2天3小时5秒")
        assert result["type"] == "interval"
        assert result["value"] == 2 * 86400 + 3 * 3600 + 5

    def test_ts06_invalid_format(self):
        """TS-06: 无效格式抛 ValueError"""
        with pytest.raises(ValueError, match="无效的时间格式"):
            TimeTaskParser.parse("not-a-time")
