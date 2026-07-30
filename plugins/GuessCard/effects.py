"""PJSK 猜卡面图片效果处理器（上游移植）。"""

from __future__ import annotations

import logging
import random
from collections import OrderedDict
from typing import Any, Optional

from PIL import Image, ImageDraw, ImageFilter

_log = logging.getLogger("PjskGuessCard.effects")

try:
    from PIL.Image import Resampling

    LANCZOS = Resampling.LANCZOS
except ImportError:
    LANCZOS = 1  # type: ignore[assignment]

DEFAULT_EFFECT_PARAMS = {
    # blur_radius = 短边 512px 时的基准；小图按比例缩小，大图不再加大
    "light_blur": {"blur_radius": 14, "difficulty": 1},
    "heavy_blur": {"blur_radius": 28, "difficulty": 2},
    "shuffle_blocks_easy": {"block_size": 125, "difficulty": 1},
    "shuffle_blocks_hard": {"block_size": 55, "difficulty": 3},
    "horizontal_slice": {"slice_count": 45, "difficulty": 1},
    "vertical_slice": {"slice_count": 65, "difficulty": 1},
    "crop_area": {"crop_ratio": 0.3, "difficulty": 2},
    "two_strips": {"difficulty": 2},
    "three_strips": {"difficulty": 2},
}


class LRUCache:
    def __init__(self, max_size: int = 50):
        self.cache: OrderedDict[str, Any] = OrderedDict()
        self.max_size = max_size

    def get(self, key: str) -> Optional[Any]:
        if key not in self.cache:
            return None
        self.cache.move_to_end(key)
        return self.cache[key]

    def set(self, key: str, value: Any) -> None:
        if key in self.cache:
            self.cache.move_to_end(key)
        else:
            if len(self.cache) >= self.max_size:
                self.cache.popitem(last=False)
            self.cache[key] = value

    def clear(self) -> None:
        self.cache.clear()


class ImageEffectProcessor:
    EFFECT_NAMES = {
        "light_blur": "轻度模糊",
        "heavy_blur": "重度模糊",
        "shuffle_blocks_easy": "分块打乱(简易)",
        "shuffle_blocks_hard": "分块打乱(困难)",
        "horizontal_slice": "横向切割",
        "vertical_slice": "纵向切割",
        "crop_area": "截取区域",
        "two_strips": "两长条截取",
        "three_strips": "三长条截取",
    }

    EFFECT_PARAMS = {
        "light_blur": ["blur_radius"],
        "heavy_blur": ["blur_radius"],
        "shuffle_blocks_easy": ["block_size"],
        "shuffle_blocks_hard": ["block_size"],
        "horizontal_slice": ["slice_count"],
        "vertical_slice": ["slice_count"],
        "crop_area": ["crop_ratio"],
        "two_strips": [],
        "three_strips": [],
    }

    EFFECT_NAME_TO_KEY = {v: k for k, v in EFFECT_NAMES.items()}
    COMBINATIONS: dict = {}

    def __init__(self, config: Optional[dict] = None):
        self.EFFECTS: dict[str, dict] = {}
        effects_config = (config or {}).get("effects", {}) or {}
        for effect_name in self.EFFECT_NAMES:
            defaults = DEFAULT_EFFECT_PARAMS.get(effect_name, {})
            effect_cfg = effects_config.get(effect_name, {}) or {}
            entry = {
                "name": self.EFFECT_NAMES[effect_name],
                "enabled": effect_cfg.get("enabled", True),
                "difficulty": effect_cfg.get(
                    "difficulty", defaults.get("difficulty", 1)
                ),
            }
            for param in self.EFFECT_PARAMS.get(effect_name, []):
                entry[param] = effect_cfg.get(param, defaults[param])
            self.EFFECTS[effect_name] = entry

    def get_enabled_effects(self, allowed: Optional[list[str]] = None) -> list[str]:
        enabled = [k for k, v in self.EFFECTS.items() if v["enabled"]]
        if allowed is None:
            return enabled
        allow = set(allowed)
        return [k for k in enabled if k in allow]

    def calculate_difficulty(self, effect_names: list[str]) -> int:
        if not effect_names:
            return 1
        total = 0
        count = 0
        for name in effect_names:
            if name in self.EFFECTS:
                total += self.EFFECTS[name]["difficulty"]
                count += 1
        if count == 0:
            return 1
        return min(5, max(1, round(total / count)))

    @classmethod
    def _scaled_blur_radius(
        cls, img, configured: float, *, max_ratio: float, floor: float = 2.0
    ) -> float:
        """按短边缩放模糊半径，避免小图被糊成色块。"""
        short = max(1, min(img.size))
        # 以 512 为基准：更小的图同比例减弱；更大的图不再超过配置值
        radius = float(configured) * min(1.0, short / 512.0)
        radius = min(radius, short * max_ratio)
        return max(floor, radius)

    @classmethod
    def apply_light_blur(cls, img, radius=14):
        r = cls._scaled_blur_radius(img, radius, max_ratio=0.045, floor=1.5)
        return img.filter(ImageFilter.GaussianBlur(radius=r))

    @classmethod
    def apply_heavy_blur(cls, img, radius=28):
        r = cls._scaled_blur_radius(img, radius, max_ratio=0.08, floor=2.5)
        return img.filter(ImageFilter.GaussianBlur(radius=r))

    @classmethod
    def apply_shuffle_blocks(cls, img, block_size=50):
        try:
            w, h = img.size
            result = Image.new(img.mode, (w, h))
            positions = []
            for y in range(0, h, block_size):
                for x in range(0, w, block_size):
                    positions.append((x, y))
            shuffled_positions = positions.copy()
            random.shuffle(shuffled_positions)
            for idx, (orig_x, orig_y) in enumerate(positions):
                current_w = min(block_size, w - orig_x)
                current_h = min(block_size, h - orig_y)
                block = img.crop(
                    (orig_x, orig_y, orig_x + current_w, orig_y + current_h)
                )
                new_x, new_y = shuffled_positions[idx]
                new_w = min(block_size, w - new_x)
                new_h = min(block_size, h - new_y)
                if current_w != new_w or current_h != new_h:
                    block = block.resize((new_w, new_h), LANCZOS)
                result.paste(block, (new_x, new_y))
            return result
        except Exception as e:
            _log.error("分块打乱处理失败: %s", e, exc_info=True)
            return img

    @classmethod
    def apply_horizontal_slice(cls, img, slice_count=8):
        try:
            w, h = img.size
            slice_height = max(h // slice_count, 1)
            slices = []
            for i in range(slice_count):
                y_start = i * slice_height
                y_end = (i + 1) * slice_height if i < slice_count - 1 else h
                slices.append(img.crop((0, y_start, w, y_end)))
            random.shuffle(slices)
            result = Image.new(img.mode, (w, h))
            y_offset = 0
            for slice_img in slices:
                result.paste(slice_img, (0, y_offset))
                y_offset += slice_img.size[1]
            return result
        except Exception as e:
            _log.error("横向切割处理失败: %s", e, exc_info=True)
            return img

    @classmethod
    def apply_vertical_slice(cls, img, slice_count=8):
        try:
            w, h = img.size
            slice_width = max(w // slice_count, 1)
            slices = []
            for i in range(slice_count):
                x_start = i * slice_width
                x_end = (i + 1) * slice_width if i < slice_count - 1 else w
                slices.append(img.crop((x_start, 0, x_end, h)))
            random.shuffle(slices)
            result = Image.new(img.mode, (w, h))
            x_offset = 0
            for slice_img in slices:
                result.paste(slice_img, (x_offset, 0))
                x_offset += slice_img.size[0]
            return result
        except Exception as e:
            _log.error("纵向切割处理失败: %s", e, exc_info=True)
            return img

    @classmethod
    def apply_crop_area(cls, img, crop_ratio=0.5):
        try:
            w, h = img.size
            crop_w = max(int(w * crop_ratio), 50)
            crop_h = max(int(h * crop_ratio), 50)
            max_x = w - crop_w
            max_y = h - crop_h
            x = random.randint(0, max_x) if max_x > 0 else 0
            y = random.randint(0, max_y) if max_y > 0 else 0
            return img.crop((x, y, x + crop_w, y + crop_h))
        except Exception as e:
            _log.error("截取区域处理失败: %s", e, exc_info=True)
            return img

    @classmethod
    def apply_two_strips(cls, img):
        try:
            w, h = img.size
            strip_h = random.randint(max(int(h * 0.1), 25), max(int(h * 0.35), 35))
            strip_h = min(strip_h, h // 2 - 1)
            strip_w = random.randint(max(int(w * 0.5), 60), max(int(w * 0.85), 80))
            possible_ys = list(range(0, h - strip_h + 1))
            random.shuffle(possible_ys)
            found: list[int] = []
            for y in possible_ys:
                if not any(y < ey + strip_h and y + strip_h > ey for ey in found):
                    found.append(y)
                    if len(found) == 2:
                        break
            if len(found) < 2:
                return img
            found.sort()
            divider_h = 3
            total_h = strip_h * 2 + divider_h
            result = Image.new(img.mode, (strip_w, total_h))
            y_offset = 0
            for i, orig_y in enumerate(found):
                max_x = w - strip_w
                x = random.randint(0, max_x) if max_x > 0 else 0
                strip = img.crop((x, orig_y, x + strip_w, orig_y + strip_h))
                result.paste(strip, (0, y_offset))
                y_offset += strip_h
                if i < len(found) - 1:
                    draw = ImageDraw.Draw(result)
                    draw.rectangle(
                        [(0, y_offset), (strip_w - 1, y_offset + divider_h - 1)],
                        fill=(255, 255, 255),
                    )
                    y_offset += divider_h
            return result
        except Exception as e:
            _log.error("两长条截取处理失败: %s", e, exc_info=True)
            return img

    @classmethod
    def apply_three_strips(cls, img):
        try:
            w, h = img.size
            strip_h = random.randint(max(int(h * 0.08), 20), max(int(h * 0.2), 28))
            strip_h = min(strip_h, h // 3 - 1)
            strip_w = random.randint(max(int(w * 0.5), 60), max(int(w * 0.85), 80))
            possible_ys = list(range(0, h - strip_h + 1))
            random.shuffle(possible_ys)
            found: list[int] = []
            for y in possible_ys:
                if not any(y < ey + strip_h and y + strip_h > ey for ey in found):
                    found.append(y)
                    if len(found) == 3:
                        break
            if len(found) < 3:
                return img
            found.sort()
            divider_h = 3
            total_h = strip_h * 3 + divider_h * 2
            result = Image.new(img.mode, (strip_w, total_h))
            y_offset = 0
            for i, orig_y in enumerate(found):
                max_x = w - strip_w
                x = random.randint(0, max_x) if max_x > 0 else 0
                strip = img.crop((x, orig_y, x + strip_w, orig_y + strip_h))
                result.paste(strip, (0, y_offset))
                y_offset += strip_h
                if i < len(found) - 1:
                    draw = ImageDraw.Draw(result)
                    draw.rectangle(
                        [(0, y_offset), (strip_w - 1, y_offset + divider_h - 1)],
                        fill=(255, 255, 255),
                    )
                    y_offset += divider_h
            return result
        except Exception as e:
            _log.error("三长条截取处理失败: %s", e, exc_info=True)
            return img

    def apply_effect(self, img, effect_name: str, **kwargs):
        if effect_name == "none":
            return img
        if effect_name == "light_blur":
            return self.apply_light_blur(
                img,
                kwargs.get("blur_radius", self.EFFECTS["light_blur"]["blur_radius"]),
            )
        if effect_name == "heavy_blur":
            return self.apply_heavy_blur(
                img,
                kwargs.get("blur_radius", self.EFFECTS["heavy_blur"]["blur_radius"]),
            )
        if effect_name == "shuffle_blocks_easy":
            return self.apply_shuffle_blocks(
                img,
                kwargs.get(
                    "block_size", self.EFFECTS["shuffle_blocks_easy"]["block_size"]
                ),
            )
        if effect_name == "shuffle_blocks_hard":
            return self.apply_shuffle_blocks(
                img,
                kwargs.get(
                    "block_size", self.EFFECTS["shuffle_blocks_hard"]["block_size"]
                ),
            )
        if effect_name == "horizontal_slice":
            return self.apply_horizontal_slice(
                img,
                kwargs.get(
                    "slice_count", self.EFFECTS["horizontal_slice"]["slice_count"]
                ),
            )
        if effect_name == "vertical_slice":
            return self.apply_vertical_slice(
                img,
                kwargs.get(
                    "slice_count", self.EFFECTS["vertical_slice"]["slice_count"]
                ),
            )
        if effect_name == "crop_area":
            return self.apply_crop_area(
                img, kwargs.get("crop_ratio", self.EFFECTS["crop_area"]["crop_ratio"])
            )
        if effect_name == "two_strips":
            return self.apply_two_strips(img)
        if effect_name == "three_strips":
            return self.apply_three_strips(img)
        return img

    @staticmethod
    def ensure_processable(img, background=(255, 255, 255)):
        """转成可模糊的不透明 RGB。

        E7 等透明立绘透明像素 RGB 常为 0；直接对 RGBA 做 GaussianBlur
        会把黑色渗进角色，重模糊后接近纯黑。先铺白底再处理。
        """
        if img.mode == "RGB":
            return img
        if img.mode == "L":
            return img.convert("RGB")
        if img.mode == "P":
            img = (
                img.convert("RGBA")
                if "transparency" in getattr(img, "info", {})
                else img.convert("RGB")
            )
        elif img.mode in ("PA", "LA", "RGBa"):
            img = img.convert("RGBA")
        elif img.mode != "RGBA":
            return img.convert("RGB")

        if img.mode == "RGBA":
            base = Image.new("RGB", img.size, background)
            base.paste(img, mask=img.split()[3])
            return base
        return img

    def apply_effects(self, img, effect_names: list[str]):
        result = self.ensure_processable(img)
        for name in effect_names:
            if name in self.EFFECTS:
                result = self.apply_effect(result, name)
        return result

    def random_effect(self, allowed: Optional[list[str]] = None) -> str:
        enabled = self.get_enabled_effects(allowed)
        if not enabled:
            return "light_blur"
        return random.choice(enabled)

    def random_effect_combination(
        self, allowed: Optional[list[str]] = None
    ) -> tuple[list[str], str]:
        if not allowed and self.COMBINATIONS and random.random() < 0.3:
            combo_key = random.choice(list(self.COMBINATIONS.keys()))
            combo = self.COMBINATIONS[combo_key]
            return combo["effects"], combo["name"]
        effect = self.random_effect(allowed)
        return [effect], self.EFFECTS[effect]["name"]
