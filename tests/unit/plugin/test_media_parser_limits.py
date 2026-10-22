from plugins.MediaParser.config import DEFAULT_CONFIG
from plugins.MediaParser.core.compat import (
    ConfigWrapper,
    create_config,
    video_max_duration_seconds,
)
from plugins.MediaParser.MediaParser import MediaParser


def test_media_parser_default_video_limits():
    assert DEFAULT_CONFIG["source_max_size"] == 300
    assert DEFAULT_CONFIG["source_max_minute"] == 10
    assert DEFAULT_CONFIG["video_send_max_seconds"] == 600


def test_media_parser_whitelist_only_allows_configured_group():
    assert DEFAULT_CONFIG["allowed_groups"] == [1064163905]

    parser = MediaParser.__new__(MediaParser)
    parser.config = ConfigWrapper({"allowed_groups": [1064163905]})
    assert parser._is_group_allowed("1064163905")
    assert not parser._is_group_allowed("853963912")
    assert not parser._is_group_allowed(None)


def test_media_parser_compat_defaults_and_effective_duration(tmp_path):
    config = create_config(tmp_path / "data", tmp_path / "cache")

    assert config["source_max_size"] == 300
    assert config["source_max_minute"] == 10
    assert config["video_send_max_seconds"] == 600
    assert config["allowed_groups"] == [1064163905]
    assert video_max_duration_seconds(config) == 600
