from .catalog import StickerCatalog, sticker_catalog
from .replace import extract_stickers
from .tone import apply_tone, strip_pet_voice
from .whale import whale_catalog

EXPRESSION_ENABLED = True
MAX_STICKERS_PER_REPLY = 1
# 表情包图库：darkdog / gengjing / both
STICKER_LIBRARY = "both"
# 是否启用梗鲸（网络梗图）—— 本地图库由 catalog.active 决定
USE_WHALE = True
# 本轮模型没发图时，自动补一张的概率
STICKER_AUTO_RATE = 0.25
# 人味后处理强度：off / light / normal / max
TONE_STRENGTH = "light"
# 草稿法第三步：生成后再跑一次「删废话」编辑调用（实验功能，默认关）
EDITOR_PASS = False

__all__ = [
    "StickerCatalog",
    "sticker_catalog",
    "extract_stickers",
    "apply_tone",
    "strip_pet_voice",
    "whale_catalog",
    "EXPRESSION_ENABLED",
    "MAX_STICKERS_PER_REPLY",
    "STICKER_LIBRARY",
    "USE_WHALE",
    "STICKER_AUTO_RATE",
    "TONE_STRENGTH",
    "EDITOR_PASS",
]
