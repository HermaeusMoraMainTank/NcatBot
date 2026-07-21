"""水印叠加逻辑（移植自 astrbot-plugin-fake-ai-watermark）。"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Optional

from PIL import Image

from ncatbot.utils import get_log

_log = get_log()

DOUBAO_ASPECT_RATIO = 8742 / 1660
MAX_IMAGE_PIXELS = 10000 * 10000
WARNING_PIXELS = 5000 * 5000
LARGE_IMAGE_MARGIN = 64
SMALL_IMAGE_MARGIN = 32
LARGE_IMAGE_THRESHOLD = 1024
ALPHA_THRESHOLD = 10
DOUBAO_SIZE_RATIO = 0.13
DOUBAO_MARGIN_RATIO = 0.03


class WatermarkProcessor:
    def __init__(self, assets_dir: Path):
        self.assets_dir = assets_dir
        self._cache: dict[str, Image.Image] = {}

    def load_watermark(self, filename: str) -> Optional[Image.Image]:
        if filename in self._cache:
            return self._cache[filename].copy()
        path = self.assets_dir / filename
        if not path.is_file():
            _log.error("[FakeAiWatermark] 水印文件不存在: %s", path)
            return None
        try:
            watermark = Image.open(path).convert("RGBA")
            self._cache[filename] = watermark.copy()
            return watermark.copy()
        except Exception as e:
            _log.error("[FakeAiWatermark] 加载水印失败 %s: %s", filename, e)
            return None

    def preprocess(self, image_data: bytes) -> Optional[Image.Image]:
        try:
            img = Image.open(BytesIO(image_data))
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")
            if not self._check_safety(img):
                return None
            return img
        except Exception as e:
            _log.error("[FakeAiWatermark] 图片预处理失败: %s", e)
            return None

    def _check_safety(self, img: Image.Image) -> bool:
        pixels = img.width * img.height
        if pixels > MAX_IMAGE_PIXELS:
            _log.error(
                "[FakeAiWatermark] 图像过大: %s 像素（上限 %s）",
                pixels,
                MAX_IMAGE_PIXELS,
            )
            return False
        if pixels > WARNING_PIXELS:
            _log.warning(
                "[FakeAiWatermark] 处理大图: %sx%s", img.width, img.height
            )
        return True

    @staticmethod
    def _apply_opacity(watermark: Image.Image, opacity: float) -> Image.Image:
        alpha = watermark.getchannel("A")
        new_alpha = alpha.point(
            lambda v: int(255 * opacity) if v > ALPHA_THRESHOLD else 0
        )
        watermark.putalpha(new_alpha)
        return watermark

    def apply_gemini(
        self, image: Image.Image, opacity: float = 0.25
    ) -> Optional[Image.Image]:
        if not self._check_safety(image):
            return None

        if image.width > LARGE_IMAGE_THRESHOLD and image.height > LARGE_IMAGE_THRESHOLD:
            watermark = self.load_watermark("gemini_96px.png")
            margin = LARGE_IMAGE_MARGIN
        else:
            watermark = self.load_watermark("gemini_48px.png")
            margin = SMALL_IMAGE_MARGIN
        if watermark is None:
            return None

        result = image.convert("RGBA") if image.mode != "RGBA" else image.copy()
        wm_w, wm_h = watermark.size
        x = result.width - margin - wm_w
        y = result.height - margin - wm_h
        if x < 0 or y < 0:
            _log.warning("[FakeAiWatermark] 水印大于原图，跳过叠加")
            return image.convert("RGB")

        watermark = self._apply_opacity(watermark.convert("RGBA"), opacity)
        result.paste(watermark, (x, y), watermark)
        return result.convert("RGB")

    def apply_doubao(
        self, image: Image.Image, opacity: float = 0.7
    ) -> Optional[Image.Image]:
        if not self._check_safety(image):
            return None

        watermark = self.load_watermark("doubao.png")
        if watermark is None:
            return None

        result = image.convert("RGBA") if image.mode != "RGBA" else image.copy()
        wm_w = int(image.width * DOUBAO_SIZE_RATIO)
        wm_h = int(wm_w / DOUBAO_ASPECT_RATIO)
        watermark = watermark.resize((wm_w, wm_h), Image.Resampling.LANCZOS)
        watermark = self._apply_opacity(watermark.convert("RGBA"), opacity)

        margin_x = int(image.width * DOUBAO_MARGIN_RATIO)
        margin_y = int(image.height * DOUBAO_MARGIN_RATIO)
        x = image.width - margin_x - wm_w
        y = image.height - margin_y - wm_h
        result.paste(watermark, (x, y), watermark)
        return result.convert("RGB")
