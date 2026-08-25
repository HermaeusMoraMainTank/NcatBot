from plugins.MediaParser.config import DEFAULT_CONFIG
from plugins.MediaParser.core.compat import create_config, video_max_duration_seconds


def test_media_parser_default_video_limits():
    assert DEFAULT_CONFIG["source_max_size"] == 300
    assert DEFAULT_CONFIG["source_max_minute"] == 10
    assert DEFAULT_CONFIG["video_send_max_seconds"] == 600


def test_media_parser_compat_defaults_and_effective_duration(tmp_path):
    config = create_config(tmp_path / "data", tmp_path / "cache")

    assert config["source_max_size"] == 300
    assert config["source_max_minute"] == 10
    assert config["video_send_max_seconds"] == 600
    assert video_max_duration_seconds(config) == 600
