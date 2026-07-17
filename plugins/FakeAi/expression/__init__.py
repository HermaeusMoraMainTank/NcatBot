from .catalog import StickerCatalog, sticker_catalog
from .replace import extract_stickers

EXPRESSION_ENABLED = True
MAX_STICKERS_PER_REPLY = 1

__all__ = [
    "StickerCatalog",
    "sticker_catalog",
    "extract_stickers",
    "EXPRESSION_ENABLED",
    "MAX_STICKERS_PER_REPLY",
]
