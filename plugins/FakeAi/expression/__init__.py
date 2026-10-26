from .catalog import StickerCatalog, sticker_catalog
from .replace import extract_stickers
from .tone import apply_tone
from .whale import whale_catalog

EXPRESSION_ENABLED = True
MAX_STICKERS_PER_REPLY = 1
# 表情包图库：darkdog / gengjing / both
STICKER_LIBRARY = "both"
# 本轮模型没发图时，自动补一张的概率
STICKER_AUTO_RATE = 0.25
# 人味后处理强度：off / light / normal / max
TONE_STRENGTH = "light"

__all__ = [
    "StickerCatalog",
    "sticker_catalog",
    "extract_stickers",
    "apply_tone",
    "whale_catalog",
    "EXPRESSION_ENABLED",
    "MAX_STICKERS_PER_REPLY",
    "STICKER_LIBRARY",
    "STICKER_AUTO_RATE",
    "TONE_STRENGTH",
]
