"""
NapCat 安装/更新

合并了旧 platform.py 的 install_napcat / check_and_update 逻辑
以及 nc_utils.py 的 download_file / unzip_file。
"""

import os
import subprocess
import sys
import zipfile
from typing import Optional

import httpx
from tqdm import tqdm

from ncatbot.utils import gen_url_with_proxy, get_json, get_log
from ..constants import INSTALL_SCRIPT_URL, WINDOWS_NAPCAT_DIR
from .platform import PlatformOps, LinuxOps

LOG = get_log("NapCatInstaller")


# ==================== 文件操作工具 ====================


def download_file(url: str, file_name: str) -> None:
    """下载文件（带进度条）"""
    with httpx.stream("GET", url, follow_redirects=True) as r:
        r.raise_for_status()
        total_size = int(r.headers.get("content-length", 0))

        progress_bar = tqdm(
            total=total_size,
            unit="iB",
            unit_scale=True,
            desc=f"Downloading {file_name}",
            bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]",
            colour="green",
            dynamic_ncols=True,
            smoothing=0.3,
            mininterval=0.1,
            maxinterval=1.0,
        )

        with open(file_name, "wb") as f:
            for data in r.iter_bytes(chunk_size=1024):
                progress_bar.update(len(data))
                f.write(data)

        progress_bar.close()


def unzip_file(file_name: str, extract_path: str, remove: bool = False) -> None:
    """解压 ZIP 文件"""
    with zipfile.ZipFile(file_name, "r") as zip_ref:
        zip_ref.extractall(extract_path)
        LOG.info(f"解压 {file_name} 成功")

    if remove:
        os.remove(file_name)


# ==================== NapCat 安装器 ====================


class NapCatInstaller:
    """NapCat 安装/更新管理"""

    def __init__(self, platform_ops: PlatformOps, napcat_config=None):
        self._platform = platform_ops
        self._napcat_config = napcat_config

    @staticmethod
    def _get_version_from_redirect() -> Optional[str]:
        """通过 releases/latest 重定向获取最新版本号（不依赖 API 限额）"""
        url = "https://github.com/NapNeko/NapCatQQ/releases/latest"
        try:
            resp = httpx.head(url, follow_redirects=True, timeout=10)
            # 最终 URL 形如: https://github.com/NapNeko/NapCatQQ/releases/tag/vX.X.X
            version = str(resp.url).rsplit("/", 1)[-1].lstrip("v")
            if version:
                LOG.debug(f"通过重定向获取版本号成功: {version}")
                return version
        except Exception as e:
            LOG.error(f"备用方式获取版本信息也失败: {e}")
        return None

    @staticmethod
    def get_latest_version() -> Optional[str]:
        """从 GitHub 获取最新版本号"""
        api_url = "https://api.github.com/repos/NapNeko/NapCatQQ/tags"
        LOG.info(f"正在获取版本信息... {api_url}")
        try:
            data = get_json(api_url)
            if data and isinstance(data, list) and len(data) > 0:
                version = data[0].get("name", "").lstrip("v")
                if version:
                    LOG.debug(f"获取最新版本信息成功, 版本号: {version}")
                    return version
            LOG.warning("获取最新版本信息失败")
        except Exception as e:
            LOG.warning(f"通过 API 获取版本信息失败: {e}, 尝试备用方式...")

        return NapCatInstaller._get_version_from_redirect()

    def install(self, *, skip_confirm: bool = False) -> bool:
        """安装 NapCat（供 CLI 直接调用）。

        Args:
            skip_confirm: 跳过交互确认，适用于 Docker build / CI 等非交互场景。
        """
        return self._install("install", skip_confirm=skip_confirm)

    def ensure_installed(self, skip_confirm: bool = False) -> bool:
        """确保 NapCat 已安装并为最新版本"""
        if not self._platform.is_napcat_installed():
            return self._install("install", skip_confirm=skip_confirm)

        if not self._napcat_config or not self._napcat_config.enable_update_check:
            return True

        current = self._platform.get_installed_version()
        latest = self.get_latest_version()

        if current and latest and current != latest:
            LOG.info(f"发现新版本: {latest} (当前: {current})")
            return self._install("update", skip_confirm=skip_confirm)

        LOG.info("当前 NapCat 已是最新版本")
        return True

    def _install(self, install_type: str, *, skip_confirm: bool = False) -> bool:
        """安装或更新 NapCat"""
        if isinstance(self._platform, LinuxOps):
            return self._install_linux(install_type, skip_confirm=skip_confirm)
        return self._install_windows(install_type, skip_confirm=skip_confirm)

    def _install_windows(
        self, install_type: str, *, skip_confirm: bool = False
    ) -> bool:
        if not skip_confirm:
            prompt = (
                "未找到 napcat，是否要自动安装？\n输入 Y 继续安装或 N 退出: "
                if install_type == "install"
                else "输入 Y 继续更新或 N 跳过更新: "
            )
            if not PlatformOps._confirm_action(prompt):
                return False

        version = self.get_latest_version()
        if not version:
            return False

        try:
            download_url = gen_url_with_proxy(
                f"https://github.com/NapNeko/NapCatQQ/releases/download/v{version}/NapCat.Shell.zip"
            )
            LOG.info(f"下载链接: {download_url}")
            LOG.info("正在下载 napcat 客户端...")

            zip_path = f"{WINDOWS_NAPCAT_DIR}.zip"
            download_file(download_url, zip_path)
            unzip_file(zip_path, WINDOWS_NAPCAT_DIR, remove=True)
            return True
        except Exception as e:
            LOG.error(f"安装失败: {e}")
            return False

    def _install_linux(self, install_type: str, *, skip_confirm: bool = False) -> bool:
        if not skip_confirm:
            prompt = (
                "未找到 napcat，是否要使用一键安装脚本安装？\n输入 Y 继续安装或 N 退出: "
                if install_type == "install"
                else "是否要更新 napcat 客户端？\n输入 Y 继续更新或 N 跳过更新: "
            )
            if not PlatformOps._confirm_action(prompt):
                return False

        try:
            LOG.info("正在下载一键安装脚本...")
            cmd = (
                f"bash -c 'curl -sS {INSTALL_SCRIPT_URL} -o install "
                f'&& printf "n\\ny\\n" | bash install\''
            )
            process = subprocess.Popen(
                cmd,
                shell=True,
                stdin=sys.stdin,
                stdout=sys.stdout,
                stderr=sys.stderr,
            )
            process.wait()

            if process.returncode == 0:
                LOG.info("napcat 客户端安装完成")
                return True
            LOG.error("执行一键安装脚本失败")
            return False
        except Exception as e:
            LOG.error(f"执行一键安装脚本失败: {e}")
            raise
