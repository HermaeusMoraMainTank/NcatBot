from pathlib import Path

RESOURCES_PATH = Path("data/stats_render/resources")
TEMP_PATH = Path("data/stats_render/temp")


def ensure_dirs() -> None:
    TEMP_PATH.mkdir(parents=True, exist_ok=True)
