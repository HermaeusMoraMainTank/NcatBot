"""
pip 依赖检查与安装

检查插件声明的 pip 依赖是否满足，并在用户确认后安装缺失的包。
安装工具优先使用 uv，回退到 pip。
"""

import importlib.metadata as _meta
import re
import shutil
import subprocess
import sys
from typing import Dict, List, Tuple

from packaging.specifiers import SpecifierSet
from packaging.version import Version

from .logger import get_log

LOG = get_log("PipHelper")

# 合法 PyPI 包名: 字母/数字/连字符/下划线/点，不允许 URL、本地路径、extras
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?$")


def check_requirements(
    pip_deps: Dict[str, str],
) -> Tuple[List[str], List[str]]:
    """检查 pip 依赖是否满足。

    Args:
        pip_deps: {包名: 版本约束} 映射，版本约束为 PEP 440 字符串或 ``"*"``

    Returns:
        ``(satisfied, missing)`` — 已满足的列表和缺失/不满足的 ``name specifier`` 列表
    """
    satisfied: List[str] = []
    missing: List[str] = []

    for name, constraint in pip_deps.items():
        spec_str = constraint if constraint != "*" else ""
        req_label = f"{name}{spec_str}" if spec_str else name

        try:
            installed_ver = _meta.version(name)
        except _meta.PackageNotFoundError:
            missing.append(req_label)
            continue

        if spec_str:
            try:
                if not SpecifierSet(spec_str).contains(Version(installed_ver)):
                    missing.append(req_label)
                    continue
            except Exception:
                # 无法解析版本约束时视为不满足
                missing.append(req_label)
                continue

        satisfied.append(req_label)

    return satisfied, missing


def install_packages(requirements: List[str]) -> bool:
    """安装 pip 包列表。

    安装策略：优先 ``uv pip install``，回退到 ``pip install``。

    Args:
        requirements: PEP 508 需求字符串列表，如 ``["aiohttp>=3.8.0", "numpy"]``

    Returns:
        是否全部安装成功
    """
    if not requirements:
        return True

    # 安全校验：拒绝 URL / 本地路径 / extras
    for req in requirements:
        _validate_requirement(req)

    uv = shutil.which("uv")
    if uv:
        cmd = [uv, "pip", "install", "--python", sys.executable, *requirements]
        tool_name = "uv"
    else:
        cmd = [sys.executable, "-m", "pip", "install", *requirements]
        tool_name = "pip"

    LOG.info("使用 %s 安装依赖: %s", tool_name, " ".join(requirements))
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            LOG.error(
                "%s 安装失败 (exit %d):\n%s",
                tool_name,
                result.returncode,
                result.stderr,
            )
            return False
        LOG.info("依赖安装完成")
        return True
    except subprocess.TimeoutExpired:
        LOG.error("依赖安装超时")
        return False
    except Exception as e:
        LOG.error("依赖安装异常: %s", e)
        return False


def format_missing_report(plugin_name: str, missing: List[str]) -> str:
    """格式化缺失依赖报告。"""
    items = "\n".join(f"  - {req}" for req in missing)
    return f"插件 [{plugin_name}] 缺失以下 pip 依赖:\n{items}"


def _validate_requirement(req: str) -> None:
    """校验需求字符串只包含包名+版本约束，拒绝危险输入。

    Raises:
        ValueError: 包含 URL、本地路径或不合法字符
    """
    # 提取包名部分（版本约束之前）
    name_part = re.split(r"[><=!~;@\[]", req, maxsplit=1)[0].strip()
    if not name_part or not _SAFE_NAME_RE.match(name_part):
        raise ValueError(f"不合法的 pip 依赖声明: {req!r}（仅允许 PyPI 包名+版本约束）")

    # 拒绝 URL 形式
    if "://" in req or req.startswith("/") or req.startswith("\\"):
        raise ValueError(f"不允许 URL 或本地路径形式的依赖: {req!r}")
