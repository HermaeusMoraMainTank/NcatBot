"""幻影坦克合成算法。

移植自 https://github.com/Yuzi-Liang/astrbot_plugin_mirage_tank
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Literal, Optional

import numpy as np
from PIL import Image

Mode = Literal["gray", "color"]


def generate_mirage(
    front_path: str | Path,
    back_path: str | Path,
    *,
    mode: Mode = "gray",
    a: float = 0.5,
    b: float = 20.0,
    w: float = 0.7,
    save_dir: Optional[str | Path] = None,
) -> str:
    if mode == "color":
        return _generate_color_tank(front_path, back_path, save_dir, a, b, w)
    return _generate_gray_tank(front_path, back_path, save_dir)


def _tmp_png(save_dir: Optional[str | Path]) -> tempfile.NamedTemporaryFile:
    dir_arg = str(save_dir) if save_dir else None
    if dir_arg:
        Path(dir_arg).mkdir(parents=True, exist_ok=True)
    return tempfile.NamedTemporaryFile(delete=False, suffix=".png", dir=dir_arg)


def _generate_gray_tank(
    front_img_path: str | Path,
    back_img_path: str | Path,
    save_dir: Optional[str | Path],
    a: float = 5.0,
    b: float = 5.0,
) -> str:
    with Image.open(front_img_path) as f_img, Image.open(back_img_path) as b_img:
        image_f = f_img.convert("L")
        image_b = b_img.convert("L")

        width = min(image_f.width, image_b.width)
        height = min(image_f.height, image_b.height)
        image_f = image_f.resize((width, height), Image.Resampling.LANCZOS)
        image_b = image_b.resize((width, height), Image.Resampling.LANCZOS)

        array_f = np.array(image_f, dtype=np.float64)
        array_b = np.array(image_b, dtype=np.float64)
        new_pixels = np.zeros((height, width, 4), dtype=np.uint8)

        wf = array_f * a / 10 + 128
        wb = array_b * b / 10
        alpha = 1.0 - wf / 255.0 + wb / 255.0
        r_new = np.where(np.abs(alpha) > 1e-6, wb / alpha, 255.0)

        new_pixels[:, :, 0] = np.clip(r_new, 0, 255).astype(np.uint8)
        new_pixels[:, :, 1] = new_pixels[:, :, 0]
        new_pixels[:, :, 2] = new_pixels[:, :, 0]
        new_pixels[:, :, 3] = np.clip(alpha * 255.0, 0, 255).astype(np.uint8)

        img = Image.fromarray(new_pixels, mode="RGBA")
        with _tmp_png(save_dir) as temp_file:
            img.save(temp_file, format="PNG")
            return temp_file.name


def _generate_color_tank(
    front_img_path: str | Path,
    back_img_path: str | Path,
    save_dir: Optional[str | Path],
    a: float,
    b: float,
    w: float,
) -> str:
    with Image.open(front_img_path) as a_raw, Image.open(back_img_path) as b_raw:
        a_img = a_raw.convert("RGB")
        b_img = b_raw.convert("RGB")

        w_img, h_img = a_img.size
        b_img = b_img.resize((w_img, h_img), Image.Resampling.LANCZOS)

        a_arr = np.array(a_img, dtype=np.float32)
        b_arr = np.array(b_img, dtype=np.float32)

        a_gray = (
            0.299 * a_arr[:, :, 0] + 0.587 * a_arr[:, :, 1] + 0.114 * a_arr[:, :, 2]
        )
        b_gray = (
            0.299 * b_arr[:, :, 0] + 0.587 * b_arr[:, :, 1] + 0.114 * b_arr[:, :, 2]
        )
        b_gray = a * b_gray + b

        alpha = 255.0 - a_gray + b_gray
        alpha = np.clip(alpha, 1, 255).astype(np.uint8)
        alpha_3d = alpha.reshape(h_img, w_img, 1)

        alpha_f = alpha_3d.astype(np.float32)
        base = (1.0 - w) * a_arr + w * b_arr
        p = (base - (255.0 - alpha_f)) / (alpha_f / 255.0)
        p = np.clip(p, 0, 255).astype(np.uint8)

        rgba = np.concatenate([p, alpha_3d], axis=2)
        img_out = Image.fromarray(rgba, mode="RGBA")
        with _tmp_png(save_dir) as tmp:
            img_out.save(tmp.name, format="PNG")
            return tmp.name
