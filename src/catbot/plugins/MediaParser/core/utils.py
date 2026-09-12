import asyncio
import hashlib
import json
import os
import shutil
from collections import OrderedDict
from functools import lru_cache
from http import cookiejar
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import urlparse

from .compat import _log

K = TypeVar("K")
V = TypeVar("V")


class LimitedSizeDict(OrderedDict[K, V]):
    """
    定长字典
    """

    def __init__(self, *args, max_size=20, **kwargs):
        self.max_size = max_size
        super().__init__(*args, **kwargs)

    def __setitem__(self, key: K, value: V):
        super().__setitem__(key, value)
        if len(self) > self.max_size:
            self.popitem(last=False)  # 移除最早添加的项


async def safe_unlink(path: Path):
    """
    安全删除文件
    """
    try:
        await asyncio.to_thread(path.unlink, missing_ok=True)
    except Exception:
        _log.warning(f"删除 {path} 失败")


async def exec_ffmpeg_cmd(cmd: list[str]) -> None:
    """执行命令

    Args:
        cmd (list[str]): 命令序列
    """
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await process.communicate()
        return_code = process.returncode
    except FileNotFoundError:
        raise RuntimeError("ffmpeg 未安装或无法找到可执行文件")

    if return_code != 0:
        error_msg = stderr.decode().strip()
        raise RuntimeError(f"ffmpeg 执行失败: {error_msg}")


async def merge_av(
    *,
    v_path: Path,
    a_path: Path,
    output_path: Path,
) -> None:
    """合并视频和音频

    Args:
        v_path (Path): 视频文件路径
        a_path (Path): 音频文件路径
        output_path (Path): 输出文件路径
    """
    target_path = output_path
    if output_path in (v_path, a_path):
        output_path = output_path.with_name(
            f"{output_path.stem}_merged{output_path.suffix}"
        )
    _log.info(f"Merging {v_path.name} and {a_path.name} to {output_path.name}")

    cmd = [
        resolve_ffmpeg_executable(),
        "-y",
        "-i",
        str(v_path),
        "-i",
        str(a_path),
        "-c",
        "copy",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        str(output_path),
    ]

    await exec_ffmpeg_cmd(cmd)
    if output_path != target_path:
        await safe_unlink(target_path)
        await asyncio.to_thread(output_path.replace, target_path)
        output_path = target_path
    cleanup = [p for p in (v_path, a_path) if p != output_path]
    await asyncio.gather(*(safe_unlink(p) for p in cleanup))
    _log.info(f"Merged {output_path.name}, {fmt_size(output_path)}")


async def merge_av_h264(
    *,
    v_path: Path,
    a_path: Path,
    output_path: Path,
) -> None:
    """合并视频和音频，并使用 H.264 编码

    Args:
        v_path (Path): 视频文件路径
        a_path (Path): 音频文件路径
        output_path (Path): 输出文件路径
    """
    _log.info(
        f"Merging {v_path.name} and {a_path.name} to {output_path.name} with H.264"
    )

    # 修改命令以确保视频使用 H.264 编码
    cmd = [
        resolve_ffmpeg_executable(),
        "-y",
        "-i",
        str(v_path),
        "-i",
        str(a_path),
        "-c:v",
        "libx264",  # 明确指定使用 H.264 编码
        "-preset",
        "medium",  # 编码速度和质量的平衡
        "-crf",
        "23",  # 质量因子，值越低质量越高
        "-c:a",
        "aac",  # 音频使用 AAC 编码
        "-b:a",
        "128k",  # 音频比特率
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        str(output_path),
    ]

    await exec_ffmpeg_cmd(cmd)
    await asyncio.gather(safe_unlink(v_path), safe_unlink(a_path))
    _log.info(f"Merged {output_path.name} with H.264, {fmt_size(output_path)}")


async def encode_video_to_h264(video_path: Path) -> Path:
    """将视频重新编码到 h264

    Args:
        video_path (Path): 视频路径

    Returns:
        Path: 编码后的视频路径
    """
    output_path = video_path.with_name(f"{video_path.stem}_h264{video_path.suffix}")
    if output_path.exists():
        return output_path
    cmd = [
        resolve_ffmpeg_executable(),
        "-y",
        "-i",
        str(video_path),
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "23",
        str(output_path),
    ]
    await exec_ffmpeg_cmd(cmd)
    _log.info(f"视频重新编码为 H.264 成功: {output_path}, {fmt_size(output_path)}")
    await safe_unlink(video_path)
    return output_path


@lru_cache(maxsize=1)
def resolve_ffmpeg_executable() -> str:
    """解析 ffmpeg 可执行文件路径。

    优先级：
    1) 环境变量 NCBOT_FFMPEG / FFMPEG_PATH
    2) PATH 中的 ffmpeg
    3) Windows 常见安装目录（含 winget）
    """
    env_path = os.getenv("NCBOT_FFMPEG") or os.getenv("FFMPEG_PATH")
    if env_path:
        p = Path(env_path)
        if p.exists():
            return str(p)

    cmd_path = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if cmd_path:
        return cmd_path

    candidates = [
        Path(r"C:\ffmpeg\bin\ffmpeg.exe"),
        Path(r"C:\Program Files\ffmpeg\bin\ffmpeg.exe"),
        Path(r"C:\Program Files\FFmpeg\bin\ffmpeg.exe"),
    ]
    local = os.getenv("LOCALAPPDATA")
    if local:
        # winget 会在 Links 下放可执行链接，优先检查
        candidates.append(Path(local) / "Microsoft" / "WinGet" / "Links" / "ffmpeg.exe")
        winget_base = Path(local) / "Microsoft" / "WinGet" / "Packages"
        if winget_base.exists():
            for pkg in winget_base.glob("Gyan.FFmpeg*"):
                for exe in pkg.glob("**/bin/ffmpeg.exe"):
                    candidates.append(exe)

    for p in candidates:
        if p.exists():
            return str(p)

    return "ffmpeg"


def fmt_size(file_path: Path) -> str:
    """格式化文件大小

    Args:
        video_path (Path): 视频路径
    """
    return f"大小: {file_path.stat().st_size / 1024 / 1024:.2f} MB"


def generate_file_name(url: str, default_suffix: str = "") -> str:
    """根据 url 生成文件名

    Args:
        url (str): url
        default_suffix (str): 默认后缀. Defaults to "".

    Returns:
        str: 文件名
    """
    # 根据 url 获取文件后缀
    path = Path(urlparse(url).path)
    suffix = path.suffix if path.suffix else default_suffix
    # 获取 url 的 md5 值
    url_hash = hashlib.md5(url.encode()).hexdigest()[:16]
    file_name = f"{url_hash}{suffix}"
    return file_name


def save_cookies_with_netscape(cookies_str: str, file_path: Path, domain: str):
    """以 netscape 格式保存 cookies

    Args:
        cookies_str: cookies 字符串
        file_path: 保存的文件路径
        domain: 域名
    """
    # 创建 MozillaCookieJar 对象
    cj = cookiejar.MozillaCookieJar(file_path)

    # 从字符串创建 cookies 并添加到 MozillaCookieJar 对象
    for cookie in cookies_str.split(";"):
        name, value = cookie.strip().split("=", 1)
        cj.set_cookie(
            cookiejar.Cookie(
                version=0,
                name=name,
                value=value,
                port=None,
                port_specified=False,
                domain="." + domain,
                domain_specified=True,
                domain_initial_dot=False,
                path="/",
                path_specified=True,
                secure=True,
                expires=0,
                discard=True,
                comment=None,
                comment_url=None,
                rest={"HttpOnly": ""},
                rfc2109=False,
            )
        )

    # 保存 cookies 到文件
    cj.save(ignore_discard=True, ignore_expires=True)


def ck2dict(cookies_str: str) -> dict[str, str]:
    """将 cookies 字符串转换为字典

    Args:
        cookies_str: cookies 字符串

    Returns:
        dict[str, str]: 字典
    """
    res = {}
    for cookie in cookies_str.split(";"):
        name, value = cookie.strip().split("=", 1)
        res[name] = value
    return res


def extract_json_url(data: dict | str) -> str | None:
    """处理 JSON 类型的消息段，提取 URL

    Args:
        data: JSON 类型的消息字典，或包含 [CQ:json,data=...] 的原始消息字符串

    Returns:
        Optional[str]: 提取的 URL, 如果提取失败则返回 None
    """
    if isinstance(data, str):
        # 如果是 CQ 码格式，先提取 data 部分并解码 HTML 实体
        if "[CQ:json" in data or "&#91;CQ:json" in data:
            data = extract_cq_json_data(data)
            if data is None:
                return None

        try:
            data = json.loads(data)
        except Exception:
            return None

    if not isinstance(data, dict):
        return None

    meta: dict[str, Any] | None = data.get("meta")
    if not meta:
        return None

    for key1, key2 in (
        ("music", "musicUrl"),
        ("detail_1", "qqdocurl"),
        ("news", "jumpUrl"),
        ("music", "jumpUrl"),
    ):
        if url := meta.get(key1, {}).get(key2):
            return url
    return None


def extract_cq_json_data(message: str) -> str | None:
    """从 CQ 码消息中提取 JSON data 部分并解码 HTML 实体

    Args:
        message: 包含 [CQ:json,data=...] 的原始消息字符串

    Returns:
        Optional[str]: 解码后的 JSON 字符串，如果提取失败则返回 None
    """
    import html
    import re

    # 匹配 [CQ:json,data=...] 格式
    # JSON 内的特殊字符已被 HTML 实体编码（如 &#93; 代表 ]），
    # 所以可以直接匹配到结尾的 ]
    pattern = r"\[CQ:json,data=(.+)\]"
    match = re.search(pattern, message, re.DOTALL)

    if not match:
        # 尝试匹配 HTML 实体编码的版本
        pattern_encoded = r"&#91;CQ:json,data=(.+)&#93;"
        match = re.search(pattern_encoded, message, re.DOTALL)

    if not match:
        return None

    json_data = match.group(1)

    # 解码 HTML 实体
    # &#44; -> ,
    # &#91; -> [
    # &#93; -> ]
    # 等等
    json_data = html.unescape(json_data)

    return json_data
