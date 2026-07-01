"""表情包 RGB 化 GIF 生成，算法参考 https://github.com/Vistyxio/memeToRGB"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import List, Literal

import numpy as np
from PIL import Image

RoiMode = Literal["auto", "dark", "light"]

_log = logging.getLogger(__name__)

_INPUT_FPS = 25


def _iter_channel_steps(step: int) -> List[int]:
    values = list(range(0, 256, step))
    if not values or values[-1] != 255:
        values.append(255)
    return values


def _auto_silhouette_roi(
    gray: np.ndarray, alpha: np.ndarray | None, threshold: int
) -> np.ndarray:
    """
    对齐 memeToRGB 二值化剪影：取暗部为 ROI，自动调节阈值使覆盖 8%~75%。
    剪影面积足够大才能看到红橙黄绿青蓝紫渐变；非 ROI 区域在合成时保持原图。
    """
    candidates: List[np.ndarray] = []

    for t in (threshold, 100, 140, 80, 160, 60, 180):
        dark = gray <= t
        ratio = float(dark.mean())
        if 0.08 <= ratio <= 0.75:
            candidates.append(dark)

    if candidates:
        return min(candidates, key=lambda m: abs(float(m.mean()) - 0.35))

    dark_roi = gray <= threshold
    light_roi = gray > threshold
    dark_ratio = float(dark_roi.mean())
    light_ratio = float(light_roi.mean())

    if dark_ratio < 0.08 and light_ratio > 0.85 and dark_ratio >= 0.015:
        return dark_roi

    if 0.10 <= light_ratio <= 0.90:
        return light_roi

    if alpha is not None:
        alpha_roi = alpha > 128
        if alpha_roi.mean() >= 0.08:
            return alpha_roi

    return dark_roi if dark_ratio >= light_ratio else light_roi


def _build_roi_mask(
    gray: np.ndarray,
    alpha: np.ndarray | None,
    threshold: int,
    roi_mode: RoiMode,
) -> np.ndarray:
    """
    构建 RGB 动画作用区域（二值掩码，非矩形选区）。

    - dark：灰度 <= threshold 的像素做 RGB（表情包线条/暗部）
    - light：灰度 > threshold 的像素做 RGB（亮部/底色）
    - auto：自动在暗/亮/透明之间择优（默认）
    """
    threshold = int(np.clip(threshold, 0, 255))
    if roi_mode == "dark":
        return gray <= threshold
    if roi_mode == "light":
        return gray > threshold
    return _auto_silhouette_roi(gray, alpha, threshold)


def _generate_rgb_frames(
    src_arr: np.ndarray,
    roi_mask: np.ndarray,
    *,
    blend_percent: int,
    frame_step: int,
    white_bg: bool,
) -> np.ndarray:
    """按 memeToRGB 顺序生成 RGB 通道轮换帧；仅 ROI 内混合动画，其余像素保持原图。"""
    h, w = src_arr.shape[:2]
    if white_bg:
        dst = np.full((h, w, 4), 255.0, dtype=np.float32)
    else:
        dst = np.zeros((h, w, 4), dtype=np.float32)

    dst[roi_mask, :3] = 0
    dst[roi_mask, 3] = 255

    blend = blend_percent / 100.0
    steps_arr = np.array(_iter_channel_steps(frame_step), dtype=np.float32)
    roi_rc = np.where(roi_mask)
    batches: List[np.ndarray] = []
    channel_pairs = ((0, 1), (1, 2), (2, 0))

    for ch_a, ch_b in channel_pairs:
        phase_anim = np.broadcast_to(dst, (len(steps_arr), h, w, 4)).copy()
        phase_anim[:, roi_rc[0], roi_rc[1], ch_a] = (255.0 - steps_arr)[:, np.newaxis]
        phase_anim[:, roi_rc[0], roi_rc[1], ch_b] = steps_arr[:, np.newaxis]
        mixed = phase_anim * blend + src_arr * (1.0 - blend)
        frame = np.broadcast_to(src_arr, phase_anim.shape).copy()
        frame[:, roi_mask] = mixed[:, roi_mask]
        batches.append(frame)
        dst[roi_mask, ch_a] = 0
        dst[roi_mask, ch_b] = 255

    return np.clip(np.concatenate(batches, axis=0), 0, 255).astype(np.uint8)


def _save_gif_ffmpeg(frames_rgb: np.ndarray, path: Path, total_sec: float) -> bool:
    """
    ffmpeg + setpts 压缩时间线（对齐 memeToRGB），768 帧可在约 1 秒内播完。
    GIF 格式单帧最小 10ms，不能靠减小 delay 实现，需用 setpts 重映射时间戳。
    """
    n, h, w, _ = frames_rgb.shape
    setpts_factor = total_sec * _INPUT_FPS / n
    raw = np.ascontiguousarray(frames_rgb[:, :, :, :3]).tobytes()

    palette_path = tempfile.mktemp(suffix=".png")
    try:
        gen_palette = [
            "ffmpeg",
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            f"{w}x{h}",
            "-r",
            str(_INPUT_FPS),
            "-i",
            "pipe:0",
            "-frames:v",
            str(n),
            "-vf",
            "palettegen=stats_mode=diff",
            palette_path,
        ]
        subprocess.run(gen_palette, input=raw, capture_output=True, check=True)

        encode_gif = [
            "ffmpeg",
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            f"{w}x{h}",
            "-r",
            str(_INPUT_FPS),
            "-i",
            "pipe:0",
            "-i",
            palette_path,
            "-frames:v",
            str(n),
            "-filter_complex",
            f"[0:v]setpts={setpts_factor}*PTS[v];[v][1:v]paletteuse=dither=bayer:bayer_scale=2",
            "-loop",
            "0",
            str(path),
        ]
        subprocess.run(encode_gif, input=raw, capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        _log.warning(f"ffmpeg GIF 编码失败，回退 PIL: {e}")
        return False
    finally:
        if os.path.exists(palette_path):
            os.unlink(palette_path)


def _save_gif_pil(frames_rgb: np.ndarray, path: Path, total_sec: float) -> None:
    """PIL 回退：GIF 最小 10ms/帧，时长无法低于 frames*10ms。"""
    n = frames_rgb.shape[0]
    centis = max(1, round(total_sec * 100 / n))
    pil_frames = [Image.fromarray(frames_rgb[i, :, :, :3], "RGB") for i in range(n)]
    pil_frames[0].save(
        path,
        save_all=True,
        append_images=pil_frames[1:],
        duration=centis * 10,
        loop=0,
        optimize=True,
    )


def image_to_rgb_gif(
    image: Image.Image,
    *,
    threshold: int = 120,
    roi_mode: RoiMode = "auto",
    blend_percent: int = 72,
    white_bg: bool = True,
    frame_step: int = 1,
    max_size: int = 256,
    total_sec: float = 1.0,
) -> Path:
    """
    将图片转为 RGB 轮换 GIF，返回临时文件路径（调用方负责删除）。

    frame_step=1 对齐原版 768 帧完整渐变；ffmpeg setpts 压缩到约 1 秒播完。
    roi_mode 控制 RGB 作用区域：auto / dark（暗部）/ light（亮部）。
    """
    src = image.convert("RGBA")
    if max(src.size) > max_size:
        src.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)

    src_arr = np.array(src, dtype=np.float32)
    gray = np.array(src.convert("L"))
    alpha = src_arr[:, :, 3]
    roi_mask = _build_roi_mask(gray, alpha, threshold, roi_mode)

    frames_arr = _generate_rgb_frames(
        src_arr,
        roi_mask,
        blend_percent=blend_percent,
        frame_step=frame_step,
        white_bg=white_bg,
    )
    if frames_arr.shape[0] == 0:
        raise ValueError("未生成任何帧")

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".gif")
    tmp_path = Path(tmp.name)
    tmp.close()

    if not _save_gif_ffmpeg(frames_arr, tmp_path, total_sec):
        _save_gif_pil(frames_arr, tmp_path, total_sec)

    return tmp_path
