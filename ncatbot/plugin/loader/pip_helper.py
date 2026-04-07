"""pip 依赖检查与安装 — 向后兼容桩，实际实现已迁移至 ncatbot.utils.pip_helper"""

from ncatbot.utils import (  # noqa: F401
    check_requirements,
    install_packages,
    format_missing_report,
)
