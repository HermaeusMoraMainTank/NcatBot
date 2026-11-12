"""开箱插件共享数据层。

- :mod:`models`   数据类
- :mod:`rules`    CS2 概率/磨损/汰换，Dota 递增概率
- :mod:`prices`   Skinport 价格与缓存
- :mod:`images`   图片缓存
- :mod:`snapshot` 快照构建与加载
"""

from __future__ import annotations

from .images import ImageCache
from .models import CrateInfo, PriceQuote, SkinItem, Treasure, TreasureItem
from .prices import (
    PriceBook,
    PriceSourceError,
    fetch_skinport,
    is_fresh,
    load_prices,
    save_prices,
)
from .snapshot import (
    CS_FILE,
    DOTA_FILE,
    DOTA_SELECTED,
    PRICES_570,
    PRICES_730,
    SELECTED_CASES,
    SNAPSHOT_DIR,
    build_cs_snapshot,
    build_dota_snapshot,
    load_cs_snapshot,
    load_dota_snapshot,
    parse_dota_page,
    save_snapshot,
)

__all__ = [
    "CS_FILE",
    "DOTA_FILE",
    "DOTA_SELECTED",
    "PRICES_570",
    "PRICES_730",
    "SELECTED_CASES",
    "SNAPSHOT_DIR",
    "CrateInfo",
    "ImageCache",
    "PriceBook",
    "PriceQuote",
    "PriceSourceError",
    "SkinItem",
    "Treasure",
    "TreasureItem",
    "build_cs_snapshot",
    "build_dota_snapshot",
    "fetch_skinport",
    "is_fresh",
    "load_cs_snapshot",
    "load_dota_snapshot",
    "load_prices",
    "parse_dota_page",
    "save_prices",
    "save_snapshot",
]
