"""ImageSender 图库权限与自动创建测试。"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def image_sender_module():
    """独立加载 ImageSender，避免测试污染插件包全局状态。"""
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "plugins"))
    spec = importlib.util.spec_from_file_location(
        "image_sender_under_test",
        ROOT / "plugins" / "ImageSender" / "ImageSender.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_plugin(image_sender_module, *, config=None, commands=None):
    plugin = image_sender_module.ImageSender.__new__(image_sender_module.ImageSender)
    plugin.name = "ImageSender"
    plugin.version = "1.2"
    plugin.config = config or {}
    plugin.get_config = lambda key, default=None: (config or {}).get(key, default)
    plugin.api = MagicMock()
    plugin.api.qq.post_group_msg = AsyncMock()
    plugin.commands = commands or {}
    plugin.allowed_users = None
    plugin.blacklist = []
    plugin._reload_commands_if_changed = MagicMock()
    plugin._save_commands = MagicMock()
    plugin._commands_mtime = 0.0
    plugin.max_count = 3
    return plugin


def _group_message(user_id, raw_message, group_id=719518427):
    return SimpleNamespace(
        group_id=group_id,
        sender=SimpleNamespace(user_id=user_id),
        raw_message=raw_message,
    )


class TestImageSenderCommandMatching:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_third_party_gallery_notice_is_not_admin_command(
        self, image_sender_module
    ):
        """其它机器人发送的“图库【...】新增图片”不应进入图库管理命令。"""
        plugin = _make_plugin(image_sender_module)
        plugin.handle_package_admin = AsyncMock()
        msg = _group_message(
            1264159468,
            "图库【鹭师傅】新增图片：\n鹭师傅_2_unknown.gif",
        )

        await plugin.handle_image(msg)

        plugin.handle_package_admin.assert_not_awaited()
        plugin.api.qq.post_group_msg.assert_not_called()


class TestImageSenderPermissions:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_call_only_user_cannot_write_or_manage(
        self, image_sender_module
    ):
        """1363021751 只能调用图库，不能上传、删除或管理。"""
        plugin = _make_plugin(image_sender_module)
        user = _group_message(1363021751, "图库 添加 测试")
        plugin.handle_package_admin = AsyncMock()
        plugin.handle_delete = AsyncMock()
        plugin.handle_upload = AsyncMock()

        await plugin.handle_image(user)
        await plugin.handle_image(_group_message(1363021751, "删除 测试"))
        await plugin.handle_image(_group_message(1363021751, "上传 测试"))

        plugin.handle_package_admin.assert_not_awaited()
        plugin.handle_delete.assert_not_awaited()
        plugin.handle_upload.assert_not_awaited()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_call_only_user_can_call_whitelisted_gallery(
        self, image_sender_module, tmp_path
    ):
        """仅发图用户仍可调用配置了其它用户白名单的图库。"""
        plugin = _make_plugin(
            image_sender_module,
            commands={
                "私有": {
                    "triggers": ["私有"],
                    "path": str(tmp_path),
                    "allowed_users": ["273421673"],
                    "recall_time": None,
                }
            },
        )
        (tmp_path / "x.jpg").write_bytes(b"x")
        await plugin.handle_image(_group_message(1363021751, "私有"))

        plugin.api.qq.post_group_msg.assert_called_once()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_non_admin_admin_command_is_silent(self, image_sender_module, caplog):
        """普通用户执行图库管理命令时只写后台日志，不向前台报错。"""
        plugin = _make_plugin(image_sender_module)
        msg = _group_message(123, "图库 添加 测试")

        with caplog.at_level(logging.WARNING):
            await plugin.handle_package_admin(msg, "图库 添加 测试")

        plugin.api.qq.post_group_msg.assert_not_called()
        assert "无权限" in caplog.text

    @pytest.mark.asyncio(loop_scope="function")
    async def test_upload_auto_creates_without_gallery_permission(
        self, image_sender_module, tmp_path
    ):
        """上传不存在的图库时自动创建，且不校验原有图库权限。"""
        plugin = _make_plugin(
            image_sender_module,
            config={"upload_prefix": "#存图"},
        )
        plugin.get_images_from_reply = AsyncMock(return_value=[])
        plugin.download_and_save_image = AsyncMock(return_value=(True, "ok"))
        raw = "#存图 鹭师傅[CQ:image,file=x.gif,url=http://example.com/x.gif]"

        with patch.object(image_sender_module, "DEFAULT_IMAGE_ROOT", tmp_path):
            await plugin.handle_upload(_group_message(123, raw), raw)

        assert "鹭师傅" in plugin.commands
        assert plugin.commands["鹭师傅"]["allowed_users"] is None
        plugin.download_and_save_image.assert_awaited_once()
        sent = plugin.api.qq.post_group_msg.call_args.kwargs["text"]
        assert "已自动创建图库 [鹭师傅]" in sent

    @pytest.mark.asyncio(loop_scope="function")
    async def test_upload_to_whitelisted_gallery_denied_silently(
        self, image_sender_module, caplog
    ):
        """已有白名单图库拒绝未授权用户上传，且不向前台报错。"""
        plugin = _make_plugin(
            image_sender_module,
            config={"upload_prefix": "#存图"},
            commands={
                "私有": {
                    "triggers": ["私有"],
                    "path": "data/image/imagesender/私有",
                    "allowed_users": ["273421673"],
                    "recall_time": None,
                }
            },
        )
        plugin.get_images_from_reply = AsyncMock(return_value=[])
        plugin.download_and_save_image = AsyncMock(return_value=(True, "ok"))
        raw = "#存图 私有[CQ:image,file=x.gif,url=http://example.com/x.gif]"

        with caplog.at_level(logging.WARNING):
            await plugin.handle_upload(_group_message(123, raw), raw)

        plugin.download_and_save_image.assert_not_awaited()
        plugin.api.qq.post_group_msg.assert_not_called()
        assert "无权上传" in caplog.text

    @pytest.mark.asyncio(loop_scope="function")
    async def test_upload_to_whitelisted_gallery_allowed(self, image_sender_module):
        """白名单内用户仍可正常上传到已有图库。"""
        plugin = _make_plugin(
            image_sender_module,
            config={"upload_prefix": "#存图"},
            commands={
                "私有": {
                    "triggers": ["私有"],
                    "path": "data/image/imagesender/私有",
                    "allowed_users": ["273421673"],
                    "recall_time": None,
                }
            },
        )
        plugin.get_images_from_reply = AsyncMock(return_value=[])
        plugin.download_and_save_image = AsyncMock(return_value=(True, "ok"))
        raw = "#存图 私有[CQ:image,file=x.gif,url=http://example.com/x.gif]"

        await plugin.handle_upload(_group_message(273421673, raw), raw)

        plugin.download_and_save_image.assert_awaited_once()
        plugin.api.qq.post_group_msg.assert_called_once()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_trigger_whitelist_denied_silently_logs(
        self, image_sender_module, caplog
    ):
        """未授权用户调用已有白名单图库时静默忽略并写后台日志。"""
        plugin = _make_plugin(
            image_sender_module,
            commands={
                "私有": {
                    "triggers": ["私有"],
                    "path": "data/image/imagesender/私有",
                    "allowed_users": ["273421673"],
                    "recall_time": None,
                }
            },
        )
        msg = _group_message(123, "私有")

        with caplog.at_level(logging.WARNING):
            await plugin.handle_image(msg)

        plugin.api.qq.post_group_msg.assert_not_called()
        assert "无权调用图库" in caplog.text
