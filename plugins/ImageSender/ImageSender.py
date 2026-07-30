from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import base64
import json
import logging
import os
import random
import re
import requests
import html
import hashlib
import shutil
from PIL import Image as PILImage
from PIL import ImageDraw, ImageFont

from ncatbot.event.qq import GroupMessageEvent as GroupMessage
from ncatbot.types import Image, MessageArray as MessageChain, Reply
from ncatbot.plugin import NcatBotPlugin
from ncatbot.core import registrar
import asyncio
from common.constants.HMMT import HMMT
from common.utils.plugin_commands import (
    cfg_str,
    cfg_str_list,
    format_help,
    is_help_message,
)


_log = logging.getLogger(__name__)

PLUGIN_DIR = Path(__file__).resolve().parent
BUNDLED_COMMANDS_PATH = PLUGIN_DIR / "commands.json"
COMMANDS_JSON_PATH = Path("data/json/image_sender_commands.json")
_LEGACY_COMMANDS_PATHS = (
    Path("data/ImageSender/commands.json"),
    BUNDLED_COMMANDS_PATH,
)
DEFAULT_IMAGE_ROOT = Path("data/image/imagesender")
_LEGACY_IMAGE_PREFIX = "data/image/"
LIST_CACHE_DIR = Path("data/json")
LIST_IMAGE_WIDTH = 1160
LIST_MAX_HEIGHT = 3200
LIST_ROW_BASE_HEIGHT = 26
LIST_LINE_HEIGHT = 20
LIST_MAX_TRIG_LINES = 3
_FONT_CANDIDATES = (
    Path(r"C:\Windows\Fonts\msyh.ttc"),
    Path(r"C:\Windows\Fonts\msyhbd.ttc"),
    Path(r"C:\Windows\Fonts\simhei.ttf"),
    Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
)

# 每用户冷却：60 秒内只能调用一次（仅发图，上传无 CD）
USER_CD_SECONDS = 60
user_last_trigger: Dict[str, datetime] = {}
# 无视 CD 的用户 ID（如 hmmt）
CD_EXEMPT_USERS = {HMMT.HMMT_ID}

# ========== 命令与帮助（可在插件 config 中覆盖） ==========
DEFAULT_CONFIG = {
    "admin_prefix": "图库",
    "upload_prefix": "上传",
    "delete_prefix": "删除",
    "admin_help_triggers": ["帮助", "help"],
}


def _format_user_help(
    admin_prefix: str,
    upload_prefix: str,
    delete_prefix: str,
    *,
    image_root: str,
    commands_path: str,
) -> str:
    return format_help(
        "ImageSender 图库插件帮助",
        [
            "发图：发送图库触发词（配置见下方路径）",
            "多张：触发词 数量（如 猫 3）",
            "数量：触发词 count",
            f"{upload_prefix} <图库名> [图片]：上传（图库不存在会自动创建）",
            f"{delete_prefix} <图库名>：管理员删除图片（需回复目标图）",
            f"{admin_prefix} 帮助：图库管理（仅管理员）",
            f"图库目录：{image_root}",
            f"触发词配置：{commands_path}",
        ],
    )


def _format_admin_help(
    admin_prefix: str,
    *,
    image_root: str,
    commands_path: str,
) -> str:
    p = admin_prefix
    return format_help(
        f"{p}管理（目录 {image_root}，配置 {commands_path}）",
        [
            f"{p} 列表",
            f"{p} 添加 <名称> [触发词1 触发词2 ...]",
            f"{p} 删除 <名称>",
            f"{p} 查看 <名称>",
            f"{p} 触发 <名称> <触发词1> [触发词2 ...]",
            f"{p} 触发 添加 <名称> <触发词>",
            f"{p} 触发 删除 <名称> <触发词>",
            f"{p} 路径 <名称> [新路径]",
            f"{p} 权限 开放 <名称>",
            f"{p} 权限 添加 <名称> <QQ号>",
            f"{p} 权限 移除 <名称> <QQ号>",
            f"{p} 撤回 <名称> [秒数，0=不撤回]",
            f"{p} 重载",
        ],
    )


def _try_acquire_user_cd(user_id: str) -> bool:
    """原子地检查并占用 CD。True 表示可调用且已记录触发时间，False 表示冷却中。"""
    if user_id in CD_EXEMPT_USERS:
        return True
    now = datetime.now()
    last = user_last_trigger.get(user_id)
    if last and (now - last).total_seconds() < USER_CD_SECONDS:
        return False
    user_last_trigger[user_id] = now
    return True


class ImageSender(NcatBotPlugin):
    name = "ImageSender"  # 插件名称
    version = "1.2"  # 插件版本

    commands: Dict[str, dict] = {}
    _commands_mtime: float = 0.0

    async def on_load(self):
        """异步加载插件"""
        self.init_defaults(DEFAULT_CONFIG)
        _log.info(f"开始加载 {self.name} 插件 v{self.version}")
        self.commands = self._load_commands()
        self.commands = self._migrate_package_paths(self.commands)
        for name, cmd in self.commands.items():
            full_path = self._resolve_path(cmd["path"])
            if not os.path.exists(full_path):
                _log.warning(f"图库 [{name}] 目录不存在: {full_path}")
        _log.info(
            f"{self.name} 插件加载完成，共 {len(self.commands)} 个图库；"
            f"图库目录: {DEFAULT_IMAGE_ROOT} | "
            f"配置: {COMMANDS_JSON_PATH.resolve()}（修改后自动生效）"
        )

    max_count = 3  # 最大发送数量
    allowed_users = None  # 全局允许的用户ID列表，None表示所有用户都可以使用
    blacklist = []  # 黑名单用户ID列表，黑名单用户无法使用任何功能

    @staticmethod
    def _is_admin(user_id: str) -> bool:
        return user_id == HMMT.HMMT_ID

    def _cmd(self, key: str) -> str:
        return cfg_str(self, key, DEFAULT_CONFIG[key], DEFAULT_CONFIG)

    def _admin_help_triggers(self) -> list[str]:
        return cfg_str_list(
            self,
            "admin_help_triggers",
            DEFAULT_CONFIG["admin_help_triggers"],
            DEFAULT_CONFIG,
        )

    def _is_auxiliary_help(self, clean_message: str) -> bool:
        """上传/删除 等辅助命令的帮助（图库 帮助 走管理入口）。"""
        return is_help_message(
            clean_message,
            command_names=(
                self._cmd("upload_prefix"),
                self._cmd("delete_prefix"),
            ),
        )

    def _user_help_text(self) -> str:
        return _format_user_help(
            self._cmd("admin_prefix"),
            self._cmd("upload_prefix"),
            self._cmd("delete_prefix"),
            image_root=str(DEFAULT_IMAGE_ROOT),
            commands_path=str(COMMANDS_JSON_PATH),
        )

    def _admin_help_text(self) -> str:
        return _format_admin_help(
            self._cmd("admin_prefix"),
            image_root=str(DEFAULT_IMAGE_ROOT),
            commands_path=str(COMMANDS_JSON_PATH),
        )

    def _ensure_commands_file(self) -> None:
        """确保 data/json 下存在配置文件，必要时从旧路径或插件内置模板迁移。"""
        COMMANDS_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
        if COMMANDS_JSON_PATH.exists():
            return
        for legacy in _LEGACY_COMMANDS_PATHS:
            if legacy.is_file():
                shutil.copy2(legacy, COMMANDS_JSON_PATH)
                _log.info(f"已迁移图库配置: {legacy} -> {COMMANDS_JSON_PATH}")
                return
        COMMANDS_JSON_PATH.write_text("{}", encoding="utf-8")
        _log.info(f"已创建空图库配置: {COMMANDS_JSON_PATH}")

    def _sync_commands_mtime(self) -> None:
        if COMMANDS_JSON_PATH.is_file():
            self._commands_mtime = COMMANDS_JSON_PATH.stat().st_mtime
        else:
            self._commands_mtime = 0.0

    def _read_commands_file(self) -> Optional[Dict[str, dict]]:
        try:
            raw = json.loads(COMMANDS_JSON_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            _log.error(f"{COMMANDS_JSON_PATH} 解析失败: {e}")
            return None
        if not isinstance(raw, dict):
            _log.error(f"{COMMANDS_JSON_PATH} 根节点必须是对象")
            return None
        return self._normalize_commands(raw)

    def _load_commands(self) -> Dict[str, dict]:
        self._ensure_commands_file()
        commands = self._read_commands_file()
        self._sync_commands_mtime()
        return commands if commands is not None else {}

    @staticmethod
    def _path_str(path: Path | str) -> str:
        return str(path).replace("\\", "/")

    def _default_package_path(self, name: str) -> str:
        return self._path_str(DEFAULT_IMAGE_ROOT / name)

    @staticmethod
    def _is_under_imagesender(path: str) -> bool:
        p = path.replace("\\", "/")
        return p.startswith("data/image/imagesender/") or p == "data/image/imagesender"

    def _resolve_imagesender_path(self, path: str, name: str) -> Optional[str]:
        """将旧版 data/image/xxx 路径映射到 data/image/imagesender/xxx。"""
        if os.path.isabs(path):
            return None
        p = path.replace("\\", "/").strip()
        if self._is_under_imagesender(p):
            return p
        if p.startswith(_LEGACY_IMAGE_PREFIX):
            suffix = p[len(_LEGACY_IMAGE_PREFIX) :].lstrip("/")
            if suffix:
                return f"data/image/imagesender/{suffix}"
        return self._default_package_path(name)

    @staticmethod
    def _merge_dir_contents(src: str, dst: str) -> None:
        os.makedirs(dst, exist_ok=True)
        if not os.path.isdir(src):
            return
        for entry in os.listdir(src):
            src_item = os.path.join(src, entry)
            dst_item = os.path.join(dst, entry)
            if os.path.exists(dst_item):
                continue
            shutil.move(src_item, dst_item)
        try:
            os.rmdir(src)
        except OSError:
            pass

    def _migrate_package_paths(self, commands: Dict[str, dict]) -> Dict[str, dict]:
        """把图库目录收拢到 data/image/imagesender/，并迁移磁盘上的旧文件夹。"""
        changed = False
        DEFAULT_IMAGE_ROOT.mkdir(parents=True, exist_ok=True)

        for name, cfg in commands.items():
            old_path = str(cfg.get("path") or self._default_package_path(name))
            new_path = self._resolve_imagesender_path(old_path, name)
            if not new_path or new_path == old_path.replace("\\", "/"):
                continue

            old_abs = self._resolve_path(old_path)
            new_abs = self._resolve_path(new_path)

            if os.path.isdir(old_abs):
                if not os.path.exists(new_abs):
                    new_abs_parent = os.path.dirname(new_abs)
                    os.makedirs(new_abs_parent, exist_ok=True)
                    shutil.move(old_abs, new_abs)
                    _log.info(f"图库 [{name}] 目录已迁移: {old_path} -> {new_path}")
                elif os.path.abspath(old_abs) != os.path.abspath(new_abs):
                    self._merge_dir_contents(old_abs, new_abs)
                    _log.info(f"图库 [{name}] 目录已合并: {old_path} -> {new_path}")
            elif not os.path.isdir(new_abs):
                os.makedirs(new_abs, exist_ok=True)

            cfg["path"] = new_path
            changed = True

        for name, cfg in commands.items():
            path = str(cfg.get("path", "")).replace("\\", "/")
            if not self._is_under_imagesender(path):
                continue
            suffix = path.split("imagesender/", 1)[-1]
            if not suffix:
                continue
            legacy_abs = self._resolve_path(f"data/image/{suffix}")
            new_abs = self._resolve_path(path)
            if os.path.isdir(legacy_abs) and os.path.abspath(
                legacy_abs
            ) != os.path.abspath(new_abs):
                if not os.path.isdir(new_abs):
                    os.makedirs(os.path.dirname(new_abs), exist_ok=True)
                    shutil.move(legacy_abs, new_abs)
                    _log.info(f"图库 [{name}] 遗留目录已迁入: data/image/{suffix}")
                else:
                    self._merge_dir_contents(legacy_abs, new_abs)
                    _log.info(f"图库 [{name}] 遗留目录已合并: data/image/{suffix}")
                changed = True

        if changed:
            self._save_commands()
            _log.info("图库路径已收拢至 data/image/imagesender/ 并写回配置")
        return commands

    def _reload_commands_if_changed(self) -> None:
        """检测 JSON 文件变更并热更新内存配置（无需重启或「图库 重载」）。"""
        self._ensure_commands_file()
        try:
            mtime = COMMANDS_JSON_PATH.stat().st_mtime
        except OSError:
            return
        if mtime == self._commands_mtime:
            return
        prev = len(self.commands)
        commands = self._read_commands_file()
        if commands is None:
            _log.error("图库配置热更新失败，已保留当前内存配置")
            self._commands_mtime = mtime
            return
        self.commands = commands
        self.commands = self._migrate_package_paths(self.commands)
        self._commands_mtime = mtime
        _log.info(
            f"图库配置已热更新: {len(self.commands)} 个图库（此前 {prev} 个）<- {COMMANDS_JSON_PATH}"
        )

    def _save_commands(self) -> None:
        COMMANDS_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
        COMMANDS_JSON_PATH.write_text(
            json.dumps(self.commands, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._sync_commands_mtime()

    @staticmethod
    def _normalize_commands(raw: dict) -> Dict[str, dict]:
        normalized: Dict[str, dict] = {}
        for name, cfg in raw.items():
            if name.startswith("_") or not isinstance(cfg, dict):
                continue
            triggers = cfg.get("triggers") or [name]
            if isinstance(triggers, str):
                triggers = [triggers]
            allowed = cfg.get("allowed_users")
            if allowed is not None:
                allowed = [str(u) for u in allowed]
            recall = cfg.get("recall_time")
            if recall is not None:
                recall = int(recall)
            normalized[name] = {
                "triggers": list(triggers),
                "path": str(cfg.get("path") or (DEFAULT_IMAGE_ROOT / name)).replace(
                    "\\", "/"
                ),
                "allowed_users": allowed,
                "recall_time": recall,
            }
        return normalized

    @staticmethod
    def _resolve_path(path: str) -> str:
        if os.path.isabs(path):
            return path
        return os.path.join(os.getcwd(), path)

    def _load_font(self, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        for path in _FONT_CANDIDATES:
            if path.is_file():
                try:
                    return ImageFont.truetype(str(path), size)
                except OSError:
                    continue
        return ImageFont.load_default()

    @staticmethod
    def _text_width(draw: ImageDraw.ImageDraw, text: str, font) -> int:
        if not text:
            return 0
        bbox = draw.textbbox((0, 0), text, font=font)
        return int(bbox[2] - bbox[0])

    @staticmethod
    def _truncate_text(
        draw: ImageDraw.ImageDraw, text: str, font, max_width: int
    ) -> str:
        if ImageSender._text_width(draw, text, font) <= max_width:
            return text
        ellipsis = "…"
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if ImageSender._text_width(draw, text[:mid] + ellipsis, font) <= max_width:
                lo = mid
            else:
                hi = mid - 1
        return text[:lo] + ellipsis if lo > 0 else ellipsis

    def _wrap_text(
        self, draw: ImageDraw.ImageDraw, text: str, font, max_width: int
    ) -> List[str]:
        lines: List[str] = []
        for paragraph in text.replace("\r\n", "\n").split("\n"):
            if not paragraph:
                lines.append("")
                continue
            line = ""
            for ch in paragraph:
                test = line + ch
                if self._text_width(draw, test, font) <= max_width:
                    line = test
                else:
                    if line:
                        lines.append(line)
                    line = ch
            if line:
                lines.append(line)
        return lines or [""]

    @staticmethod
    def _format_list_recall(recall_time: Optional[int]) -> str:
        return f"{recall_time}秒" if recall_time else "不撤回"

    @staticmethod
    def _format_list_users_full(allowed_users: Optional[List[str]]) -> str:
        if not allowed_users:
            return "全员"
        return "、".join(allowed_users)

    def _estimate_row_height(
        self, draw: ImageDraw.ImageDraw, row: dict, font_sub
    ) -> int:
        inner_w = LIST_IMAGE_WIDTH - 48
        h = LIST_ROW_BASE_HEIGHT
        trig_lines = self._wrap_text(
            draw, "触发: " + row["triggers"], font_sub, inner_w
        )
        h += min(len(trig_lines), LIST_MAX_TRIG_LINES) * LIST_LINE_HEIGHT
        h += LIST_LINE_HEIGHT
        user_lines = self._wrap_text(
            draw, "用户: " + row["users_full"], font_sub, inner_w
        )
        h += len(user_lines) * LIST_LINE_HEIGHT
        return h + 10

    def _build_package_list_rows(self) -> List[dict]:
        rows: List[dict] = []
        for name in sorted(self.commands.keys()):
            cfg = self.commands[name]
            allowed = cfg.get("allowed_users")
            rows.append(
                {
                    "name": name,
                    "count": len(self.get_image_files(cfg["path"])),
                    "recall": self._format_list_recall(cfg.get("recall_time")),
                    "users_full": self._format_list_users_full(allowed),
                    "users_restricted": bool(allowed),
                    "triggers": "、".join(cfg.get("triggers") or [name]),
                    "path": str(cfg.get("path") or "").replace("\\", "/"),
                }
            )
        return rows

    def _paginate_package_rows(self, rows: List[dict]) -> List[List[dict]]:
        probe = PILImage.new("RGB", (LIST_IMAGE_WIDTH, 10))
        probe_draw = ImageDraw.Draw(probe)
        font_sub = self._load_font(14)
        overhead = 24 * 2 + 88 + 52 + 36
        pages: List[List[dict]] = []
        current: List[dict] = []
        current_h = overhead

        for row in rows:
            row_h = self._estimate_row_height(probe_draw, row, font_sub)
            if current and current_h + row_h > LIST_MAX_HEIGHT:
                pages.append(current)
                current = [row]
                current_h = overhead + row_h
            else:
                current.append(row)
                current_h += row_h

        if current:
            pages.append(current)
        return pages

    def _render_package_list_page(
        self,
        rows: List[dict],
        *,
        page_index: int,
        page_total: int,
        total_count: int,
    ) -> str:
        padding = 24
        header_h = 88
        table_header_h = 52
        inner_w = LIST_IMAGE_WIDTH - padding * 2 - 24

        probe = PILImage.new("RGB", (LIST_IMAGE_WIDTH, 10))
        probe_draw = ImageDraw.Draw(probe)
        font_sub = self._load_font(14)
        body_h = sum(
            self._estimate_row_height(probe_draw, row, font_sub) for row in rows
        )
        height = min(
            max(padding * 2 + header_h + table_header_h + body_h + 36, 200),
            LIST_MAX_HEIGHT,
        )

        img = PILImage.new("RGB", (LIST_IMAGE_WIDTH, height), (248, 250, 255))
        draw = ImageDraw.Draw(img)

        font_title = self._load_font(26)
        font_header = self._load_font(16)
        font_row = self._load_font(16)
        font_row_sub = self._load_font(14)
        font_meta = self._load_font(15)

        draw.rounded_rectangle(
            (padding, padding, LIST_IMAGE_WIDTH - padding, padding + header_h - 8),
            radius=14,
            fill=(255, 255, 255),
            outline=(225, 230, 240),
            width=2,
        )
        title = "图库列表"
        if page_total > 1:
            title += f"（{page_index}/{page_total}）"
        draw.text(
            (padding + 16, padding + 14),
            title,
            font=font_title,
            fill=(45, 52, 65),
        )
        draw.text(
            (padding + 16, padding + 48),
            f"共 {total_count} 个图库 · 更新 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            font=font_meta,
            fill=(120, 130, 150),
        )

        y = padding + header_h
        col_name_x = padding + 12
        col_count_x = 168
        col_recall_x = 228

        draw.rectangle(
            (padding, y, LIST_IMAGE_WIDTH - padding, y + table_header_h),
            fill=(237, 242, 252),
        )
        draw.text((col_name_x, y + 12), "名称", font=font_header, fill=(58, 66, 84))
        draw.text((col_count_x, y + 12), "张数", font=font_header, fill=(58, 66, 84))
        draw.text((col_recall_x, y + 12), "撤回", font=font_header, fill=(58, 66, 84))
        draw.text(
            (padding + 12, y + 30),
            "（下方：触发词 / 路径 / 可用用户，用户列表自动换行）",
            font=font_meta,
            fill=(140, 148, 162),
        )
        y += table_header_h

        for i, row in enumerate(rows):
            row_h = self._estimate_row_height(draw, row, font_row_sub)
            row_top = y
            row_bottom = y + row_h
            if i % 2 == 0:
                draw.rectangle(
                    (padding, row_top, LIST_IMAGE_WIDTH - padding, row_bottom),
                    fill=(255, 255, 255),
                )

            name_show = self._truncate_text(
                draw, row["name"], font_row, col_count_x - col_name_x - 8
            )
            draw.text(
                (col_name_x, row_top + 6), name_show, font=font_row, fill=(45, 52, 65)
            )

            count_text = str(row["count"])
            count_w = self._text_width(draw, count_text, font_row)
            draw.text(
                (col_count_x + 40 - count_w, row_top + 6),
                count_text,
                font=font_row,
                fill=(76, 110, 168),
            )

            recall_color = (
                (200, 120, 60) if row["recall"] != "不撤回" else (130, 138, 152)
            )
            draw.text(
                (col_recall_x, row_top + 6),
                row["recall"],
                font=font_row,
                fill=recall_color,
            )

            line_y = row_top + LIST_ROW_BASE_HEIGHT
            trig_lines = self._wrap_text(
                draw, "触发: " + row["triggers"], font_row_sub, inner_w
            )
            if len(trig_lines) > LIST_MAX_TRIG_LINES:
                trig_lines = trig_lines[:LIST_MAX_TRIG_LINES]
                trig_lines[-1] = self._truncate_text(
                    draw, trig_lines[-1], font_row_sub, inner_w
                )
            for ln in trig_lines:
                draw.text(
                    (padding + 12, line_y), ln, font=font_row_sub, fill=(90, 98, 112)
                )
                line_y += LIST_LINE_HEIGHT

            path_line = self._truncate_text(
                draw, "路径: " + row["path"], font_row_sub, inner_w
            )
            draw.text(
                (padding + 12, line_y),
                path_line,
                font=font_row_sub,
                fill=(110, 118, 132),
            )
            line_y += LIST_LINE_HEIGHT

            users_color = (
                (130, 138, 152) if not row["users_restricted"] else (56, 120, 88)
            )
            for ln in self._wrap_text(
                draw, "用户: " + row["users_full"], font_row_sub, inner_w
            ):
                draw.text(
                    (padding + 12, line_y), ln, font=font_row_sub, fill=users_color
                )
                line_y += LIST_LINE_HEIGHT

            draw.line(
                [(padding, row_bottom), (LIST_IMAGE_WIDTH - padding, row_bottom)],
                fill=(236, 239, 245),
                width=1,
            )
            y = row_bottom

        buf = BytesIO()
        img.save(buf, format="PNG")
        return f"base64://{base64.b64encode(buf.getvalue()).decode()}"

    def _render_package_list_images(self) -> List[str]:
        rows = self._build_package_list_rows()
        if not rows:
            return []

        pages = self._paginate_package_rows(rows)
        total = len(rows)
        return [
            self._render_package_list_page(
                chunk,
                page_index=idx + 1,
                page_total=len(pages),
                total_count=total,
            )
            for idx, chunk in enumerate(pages)
        ]

    async def _send_package_list_images(self, group_id: int) -> None:
        images = self._render_package_list_images()
        if not images:
            await self.api.qq.post_group_msg(group_id=group_id, text="暂无图库")
            return
        LIST_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        for img_b64 in images:
            await self.api.qq.post_group_msg(
                group_id=group_id,
                rtf=MessageChain([Image(file=img_b64)]),
            )

    def _find_command(self, keyword: str) -> Optional[Tuple[str, dict]]:
        """按图库名或任意触发词查找配置。"""
        if keyword in self.commands:
            return keyword, self.commands[keyword]
        for name, cfg in self.commands.items():
            if keyword in cfg.get("triggers", []):
                return name, cfg
        return None

    def _create_command(self, name: str, triggers: list[str] | None = None) -> dict:
        """创建新图库、建目录并写回配置。"""
        triggers = triggers or [name]
        rel_path = DEFAULT_IMAGE_ROOT / name
        rel_path.mkdir(parents=True, exist_ok=True)
        path_str = self._path_str(rel_path)
        cfg = {
            "triggers": triggers,
            "path": path_str,
            "allowed_users": None,
            "recall_time": None,
        }
        self.commands[name] = cfg
        self._save_commands()
        return cfg

    async def handle_package_admin(
        self, input: GroupMessage, clean_message: str
    ) -> None:
        """图库管理命令（仅管理员）"""
        self._reload_commands_if_changed()
        user_id = str(input.sender.user_id)
        admin_prefix = self._cmd("admin_prefix")
        upload_prefix = self._cmd("upload_prefix")

        body = clean_message[len(admin_prefix) :].strip()
        if not body or body in self._admin_help_triggers():
            help_text = (
                self._admin_help_text()
                if self._is_admin(user_id)
                else self._user_help_text()
            )
            await self.api.qq.post_group_msg(group_id=input.group_id, text=help_text)
            return

        if not self._is_admin(user_id):
            await self.api.qq.post_group_msg(
                group_id=input.group_id, text="您没有图库管理权限！"
            )
            return

        if body == "重载":
            self.commands = self._load_commands()
            self._sync_commands_mtime()
            await self.api.qq.post_group_msg(
                group_id=input.group_id,
                text=f"已重载，当前 {len(self.commands)} 个图库",
            )
            return

        if body == "列表":
            await self._send_package_list_images(input.group_id)
            return

        parts = body.split()
        action = parts[0]

        if action == "添加":
            if len(parts) < 2:
                await self.api.qq.post_group_msg(
                    group_id=input.group_id,
                    text=f"用法：{admin_prefix} 添加 <名称> [触发词...]",
                )
                return
            name = parts[1]
            if name in self.commands:
                await self.api.qq.post_group_msg(
                    group_id=input.group_id, text=f"图库已存在：{name}"
                )
                return
            triggers = parts[2:] if len(parts) > 2 else [name]
            cfg = self._create_command(name, triggers)
            await self.api.qq.post_group_msg(
                group_id=input.group_id,
                text=(
                    f"已添加图库 [{name}]\n"
                    f"路径: {cfg['path']}\n"
                    f"触发: {', '.join(triggers)}\n"
                    f"上传请用：{upload_prefix} {name}"
                ),
            )
            return

        if action == "删除" and len(parts) >= 2:
            name = parts[1]
            if name not in self.commands:
                await self.api.qq.post_group_msg(
                    group_id=input.group_id, text=f"未知图库：{name}"
                )
                return
            del self.commands[name]
            self._save_commands()
            await self.api.qq.post_group_msg(
                group_id=input.group_id,
                text=f"已从配置移除图库 [{name}]（磁盘图片目录未删除）",
            )
            return

        if action == "查看" and len(parts) >= 2:
            found = self._find_command(parts[1])
            if not found:
                await self.api.qq.post_group_msg(
                    group_id=input.group_id, text=f"未知图库：{parts[1]}"
                )
                return
            name, cfg = found
            count = len(self.get_image_files(cfg["path"]))
            allowed = (
                "所有人"
                if not cfg["allowed_users"]
                else ", ".join(cfg["allowed_users"])
            )
            recall = f"{cfg['recall_time']}秒" if cfg.get("recall_time") else "否"
            await self.api.qq.post_group_msg(
                group_id=input.group_id,
                text=(
                    f"图库 [{name}]\n"
                    f"路径: {cfg['path']}\n"
                    f"触发: {', '.join(cfg['triggers'])}\n"
                    f"图片: {count} 张\n"
                    f"允许用户: {allowed}\n"
                    f"撤回: {recall}"
                ),
            )
            return

        if action == "触发":
            if len(parts) >= 3 and parts[1] == "添加":
                name, trigger = parts[2], parts[3] if len(parts) > 3 else None
                if not trigger or name not in self.commands:
                    await self.api.qq.post_group_msg(
                        group_id=input.group_id,
                        text=f"用法：{admin_prefix} 触发 添加 <名称> <触发词>",
                    )
                    return
                triggers = self.commands[name]["triggers"]
                if trigger not in triggers:
                    triggers.append(trigger)
                    self._save_commands()
                await self.api.qq.post_group_msg(
                    group_id=input.group_id,
                    text=f"[{name}] 触发词: {', '.join(triggers)}",
                )
                return
            if len(parts) >= 3 and parts[1] == "删除":
                name, trigger = parts[2], parts[3] if len(parts) > 3 else None
                if not trigger or name not in self.commands:
                    await self.api.qq.post_group_msg(
                        group_id=input.group_id,
                        text=f"用法：{admin_prefix} 触发 删除 <名称> <触发词>",
                    )
                    return
                triggers = self.commands[name]["triggers"]
                if trigger in triggers:
                    if len(triggers) <= 1:
                        await self.api.qq.post_group_msg(
                            group_id=input.group_id,
                            text="至少保留一个触发词",
                        )
                        return
                    triggers.remove(trigger)
                    self._save_commands()
                await self.api.qq.post_group_msg(
                    group_id=input.group_id,
                    text=f"[{name}] 触发词: {', '.join(triggers)}",
                )
                return
            if len(parts) >= 3:
                name = parts[1]
                if name not in self.commands:
                    await self.api.qq.post_group_msg(
                        group_id=input.group_id, text=f"未知图库：{name}"
                    )
                    return
                new_triggers = parts[2:]
                self.commands[name]["triggers"] = new_triggers
                self._save_commands()
                await self.api.qq.post_group_msg(
                    group_id=input.group_id,
                    text=f"[{name}] 触发词已设为: {', '.join(new_triggers)}",
                )
                return

        if action == "路径" and len(parts) >= 2:
            name = parts[1]
            if name not in self.commands:
                await self.api.qq.post_group_msg(
                    group_id=input.group_id, text=f"未知图库：{name}"
                )
                return
            if len(parts) == 2:
                await self.api.qq.post_group_msg(
                    group_id=input.group_id,
                    text=f"[{name}] 当前路径: {self.commands[name]['path']}",
                )
                return
            new_path = " ".join(parts[2:])
            os.makedirs(self._resolve_path(new_path), exist_ok=True)
            self.commands[name]["path"] = new_path
            self._save_commands()
            await self.api.qq.post_group_msg(
                group_id=input.group_id,
                text=f"[{name}] 路径已更新: {new_path}",
            )
            return

        if action == "权限":
            if len(parts) < 3:
                await self.api.qq.post_group_msg(
                    group_id=input.group_id,
                    text=f"用法：{admin_prefix} 权限 开放|添加|移除 <名称> [QQ号]",
                )
                return
            sub, name = parts[1], parts[2]
            if name not in self.commands:
                await self.api.qq.post_group_msg(
                    group_id=input.group_id, text=f"未知图库：{name}"
                )
                return
            cfg = self.commands[name]
            if sub == "开放":
                cfg["allowed_users"] = None
                self._save_commands()
                await self.api.qq.post_group_msg(
                    group_id=input.group_id,
                    text=f"[{name}] 已开放给所有用户",
                )
                return
            if sub == "添加" and len(parts) >= 4:
                uid = parts[3]
                users = list(cfg["allowed_users"] or [])
                if uid not in users:
                    users.append(uid)
                cfg["allowed_users"] = users
                self._save_commands()
                await self.api.qq.post_group_msg(
                    group_id=input.group_id,
                    text=f"[{name}] 允许用户: {', '.join(users)}",
                )
                return
            if sub == "移除" and len(parts) >= 4:
                uid = parts[3]
                users = list(cfg["allowed_users"] or [])
                if uid in users:
                    users.remove(uid)
                cfg["allowed_users"] = users if users else None
                self._save_commands()
                await self.api.qq.post_group_msg(
                    group_id=input.group_id,
                    text=(
                        f"[{name}] 允许用户: {', '.join(users) if users else '所有人'}"
                    ),
                )
                return

        if action == "撤回" and len(parts) >= 2:
            name = parts[1]
            if name not in self.commands:
                await self.api.qq.post_group_msg(
                    group_id=input.group_id, text=f"未知图库：{name}"
                )
                return
            if len(parts) == 2:
                rt = self.commands[name].get("recall_time")
                await self.api.qq.post_group_msg(
                    group_id=input.group_id,
                    text=f"[{name}] 撤回: {rt if rt else '不撤回'}",
                )
                return
            try:
                seconds = int(parts[2])
            except ValueError:
                await self.api.qq.post_group_msg(
                    group_id=input.group_id,
                    text="秒数必须是整数",
                )
                return
            self.commands[name]["recall_time"] = seconds if seconds > 0 else None
            self._save_commands()
            msg = (
                f"[{name}] 撤回已设为 {seconds}秒"
                if seconds > 0
                else f"[{name}] 已关闭撤回"
            )
            await self.api.qq.post_group_msg(
                group_id=input.group_id,
                text=msg,
            )
            return

        await self.api.qq.post_group_msg(
            group_id=input.group_id,
            text=f"未知子命令，发送「{admin_prefix} 帮助」查看用法",
        )

    @registrar.qq.on_group_message()
    async def handle_image(self, input: GroupMessage):
        self._reload_commands_if_changed()
        message = input.raw_message.strip()
        # 移除 CQ 码后的纯命令文本
        clean_message = re.sub(r"\[CQ:[^\]]+\]", "", message).strip()

        if self._is_auxiliary_help(clean_message):
            await self.api.qq.post_group_msg(
                group_id=input.group_id, text=self._user_help_text()
            )
            return

        admin_prefix = self._cmd("admin_prefix")
        upload_prefix = self._cmd("upload_prefix")
        delete_prefix = self._cmd("delete_prefix")

        # 图库配置管理
        if clean_message.startswith(admin_prefix):
            await self.handle_package_admin(input, clean_message)
            return

        # 处理删除功能（仅限指定管理员使用）
        if clean_message.startswith(f"{delete_prefix} "):
            await self.handle_delete(input, message, clean_message)
            return

        # 检查黑名单
        if input.sender.user_id in self.blacklist:
            return  # 黑名单用户直接忽略

        # 处理上传功能（支持直接发图片或回复图片）
        if (
            clean_message.startswith(f"{upload_prefix} ")
            or clean_message == upload_prefix
        ):
            await self.handle_upload(input, message)
            return

        # 检查消息是否以任何命令开头
        for command, config in self.commands.items():
            for trigger in config["triggers"]:
                if message.startswith(trigger):
                    # 检查全局权限
                    if (
                        self.allowed_users
                        and input.sender.user_id not in self.allowed_users
                    ):
                        return

                    # 检查命令特定权限
                    if (
                        config["allowed_users"]
                        and str(input.sender.user_id) not in config["allowed_users"]
                    ):
                        return

                    # 处理 count 查询
                    if message.startswith(trigger + " count"):
                        await self.handle_count_query(input, command, config)
                        return

                    # 处理带数量的情况
                    if message.startswith(trigger + " "):
                        trimmed_message = message[len(trigger) + 1 :].strip()
                        if not trimmed_message.isdigit():
                            return

                        count = int(trimmed_message)
                        image_files = self.get_image_files(config["path"])

                        if count <= self.max_count:
                            # 检查图片数量是否足够
                            if len(image_files) < count:
                                await self.api.qq.post_group_msg(
                                    group_id=input.group_id,
                                    text=f"图片数量不足，当前只有 {len(image_files)} 张图片",
                                )
                                return

                            # 每用户 60 秒 CD（检查与占用须在同一同步段内，避免并发消息绕过）
                            uid = str(input.sender.user_id)
                            if not _try_acquire_user_cd(uid):
                                return

                            # 使用 random.sample 不重复地选择指定数量的图片
                            selected_files = random.sample(image_files, count)
                            for file in selected_files:
                                _log.info(
                                    f"Time:{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {file}"
                                )

                            # 一次性发送所有图片
                            if selected_files:
                                response = await self.api.qq.post_group_msg(
                                    group_id=input.group_id,
                                    rtf=MessageChain(
                                        [
                                            Image(file=self._to_napcat_image(file))
                                            for file in selected_files
                                        ]
                                    ),
                                )
                                # 响应直接就是消息ID
                                last_message_id = response
                                _log.info(f"发送消息 ID: {last_message_id}")  # 添加日志
                                # 撤回消息
                                if config["recall_time"] and last_message_id:
                                    _log.info(
                                        f"将在 {config['recall_time']} 秒后撤回消息 ID: {last_message_id}"
                                    )  # 添加日志
                                    await self.recall_message(
                                        last_message_id, config["recall_time"]
                                    )
                        else:
                            await self.api.qq.post_group_msg(
                                group_id=input.group_id, text="别太贪心"
                            )
                    # 处理单个图片的情况
                    elif message == trigger:
                        image_files = self.get_image_files(config["path"])

                        if not image_files:
                            await self.api.qq.post_group_msg(
                                group_id=input.group_id,
                                text=f"路径 {config['path']} 中没有找到图片文件！",
                            )
                            return

                        # 每用户 60 秒 CD（检查与占用须在同一同步段内，避免并发消息绕过）
                        uid = str(input.sender.user_id)
                        if not _try_acquire_user_cd(uid):
                            return

                        file = random.choice(image_files)
                        _log.info(
                            f"Time:{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {file}"
                        )
                        response = await self.api.qq.post_group_msg(
                            group_id=input.group_id,
                            rtf=MessageChain([Image(file=self._to_napcat_image(file))]),
                        )
                        # 响应直接就是消息ID
                        last_message_id = response
                        _log.info(f"发送消息 ID: {last_message_id}")  # 添加日志
                        # 撤回消息
                        if config["recall_time"] and last_message_id:
                            _log.info(
                                f"将在 {config['recall_time']} 秒后撤回消息 ID: {last_message_id}"
                            )  # 添加日志
                            await self.recall_message(
                                last_message_id, config["recall_time"]
                            )
                    return

    @staticmethod
    def _to_napcat_image(file_or_uri: str) -> str:
        """把本地文件统一转成 NapCat 可识别格式，避免 Windows 路径被当作非法 URI。"""
        if not file_or_uri:
            return file_or_uri

        if file_or_uri.startswith(("http://", "https://", "base64://", "data:")):
            return file_or_uri

        if os.path.exists(file_or_uri):
            with open(file_or_uri, "rb") as f:
                encoded = base64.b64encode(f.read()).decode("utf-8")
            return f"base64://{encoded}"

        # 文件不存在时兜底交给框架后续处理
        return file_or_uri

    async def handle_count_query(self, input: GroupMessage, command: str, config: dict):
        """处理 count 查询请求"""
        image_files = self.get_image_files(config["path"])
        image_count = len(image_files)

        # 构建权限信息
        if config["allowed_users"]:
            # 特殊处理：如果是色图zmd命令，在允许用户列表中添加506531786（仅显示，不影响实际权限）
            if command == "色图zmd":
                display_users = config["allowed_users"] + ["10123121"]
                allowed_users_text = f"允许用户: {', '.join(map(str, display_users))}"
            else:
                allowed_users_text = (
                    f"允许用户: {', '.join(map(str, config['allowed_users']))}"
                )
            upload_permission_text = "上传权限: 仅限允许用户"
        else:
            allowed_users_text = "允许用户: 所有用户"
            upload_permission_text = "上传权限: 所有用户"

        # 构建响应消息
        response = f"关键词: {command}\n"
        response += f"图片数量: {image_count}\n"
        response += f"{allowed_users_text}\n"
        response += f"{upload_permission_text}\n"
        response += f"最大发送数量: {self.max_count}"
        response += f"\n是否撤回: {'是' if config['recall_time'] else '否'}"
        response += (
            f"\n撤回时长: {config['recall_time']} 秒" if config["recall_time"] else ""
        )

        await self.api.qq.post_group_msg(group_id=input.group_id, text=response)

    async def get_images_from_reply(self, input: GroupMessage) -> list:
        """从回复消息中获取图片信息列表，返回 [(filename, url), ...]"""
        image_list = []
        reply_list = input.message.filter(Reply)

        # 优先从已经解析好的 Reply 段中取
        reply_id = None
        if reply_list:
            reply_id = reply_list[0].id
        else:
            # 兼容某些情况下 Reply 段未被正常解析，只存在于 raw_message 中的情况
            # 例如: [CQ:reply,id=1960706076]
            raw = input.raw_message
            match = re.search(r"\[CQ:reply,id=(\d+)\]", raw)
            if match:
                try:
                    reply_id = int(match.group(1))
                except ValueError:
                    reply_id = None

        if reply_id:
            # get_msg 返回的是 GroupMessageEvent 对象
            reply_msg = await self.api.qq.query.get_msg(reply_id)
            # 从回复消息中获取图片
            segments = getattr(reply_msg, "message", [])

            if hasattr(segments, "filter"):
                reply_images = segments.filter(Image)
                for i, img in enumerate(reply_images):
                    if hasattr(img, "url") and img.url:
                        filename = f"reply_image_{i}.jpg"
                        if hasattr(img, "file") and img.file:
                            filename = img.file
                        image_list.append((filename, img.url))
            elif isinstance(segments, list):
                # 兼容旧/异常情况下 message 为 list 的形态（可能是 Image 段对象或原始 dict 段）
                for i, seg in enumerate(segments):
                    if isinstance(seg, Image):
                        if getattr(seg, "url", None):
                            filename = (
                                getattr(seg, "file", None) or f"reply_image_{i}.jpg"
                            )
                            image_list.append((filename, seg.url))
                        continue

                    if isinstance(seg, dict) and seg.get("type") == "image":
                        data = seg.get("data", {}) or {}
                        url = data.get("url")
                        if url:
                            filename = data.get("file") or f"reply_image_{i}.jpg"
                            image_list.append((filename, url))
        return image_list

    async def handle_upload(self, input: GroupMessage, message: str):
        """处理图片上传请求"""
        upload_prefix = self._cmd("upload_prefix")
        # 移除 CQ 码后获取命令
        clean_message = re.sub(r"\[CQ:[^\]]+\]", "", message).strip()

        # 解析上传命令格式：上传 关键词[CQ:image,...] 或回复图片
        keyword_match = re.match(rf"{re.escape(upload_prefix)}\s*(\S+)", clean_message)
        if not keyword_match:
            await self.api.qq.post_group_msg(
                group_id=input.group_id,
                text=(
                    f"{upload_prefix}格式错误！请使用：{upload_prefix} 关键词[图片] "
                    f"或回复图片并发送：{upload_prefix} 关键词"
                ),
            )
            return

        keyword = keyword_match.group(1)

        # 首先尝试从回复消息中获取图片
        image_matches = await self.get_images_from_reply(input)

        # 如果回复中没有图片，则从当前消息提取图片标签
        if not image_matches:
            image_pattern = r"\[CQ:image,.*?file=([^,]+),.*?url=([^\]]+)\]"
            image_matches = re.findall(image_pattern, message)

        if not image_matches:
            await self.api.qq.post_group_msg(
                group_id=input.group_id,
                text=(
                    f"未找到图片信息！请使用：{upload_prefix} 关键词[图片] "
                    f"或回复图片并发送：{upload_prefix} 关键词"
                ),
            )
            return

        # 处理每个匹配的图片
        success_count = 0
        failed_count = 0
        duplicate_count = 0
        user_id = input.sender.user_id

        found = self._find_command(keyword)
        auto_created = False
        if not found:
            command_config = self._create_command(keyword)
            auto_created = True
            _log.info(f"上传时自动创建图库 [{keyword}]")
        else:
            keyword, command_config = found

        # 检查该关键词的上传权限（基于allowed_users）
        if (
            command_config["allowed_users"]
            and str(user_id) not in command_config["allowed_users"]
        ):
            await self.api.qq.post_group_msg(
                group_id=input.group_id, text="您没有上传权限！"
            )
            return

        for filename, url in image_matches:
            # 下载并保存图片
            result, status = await self.download_and_save_image(
                url, filename, command_config["path"], user_id
            )

            if result:
                success_count += 1
                _log.info(f"用户 {user_id} 成功上传图片到 {keyword}: {filename}")
            elif status == "duplicate":
                duplicate_count += 1
                _log.info(f"用户 {user_id} 上传的图片已存在于 {keyword}: {filename}")
            else:
                failed_count += 1

        # 发送上传结果
        result_message = f"上传完成！成功: {success_count} 张，重复: {duplicate_count} 张，失败: {failed_count} 张"
        if auto_created:
            result_message = f"已自动创建图库 [{keyword}]\n{result_message}"
        await self.api.qq.post_group_msg(group_id=input.group_id, text=result_message)

    async def handle_delete(
        self, input: GroupMessage, message: str, clean_message: str
    ):
        """处理图片删除请求（仅限特定用户）"""
        user_id_str = str(input.sender.user_id)

        if not self._is_admin(user_id_str):
            await self.api.qq.post_group_msg(
                group_id=input.group_id, text="您没有删除权限！"
            )
            return

        delete_prefix = self._cmd("delete_prefix")
        # 解析删除命令格式：删除 关键词
        keyword_match = re.match(rf"{re.escape(delete_prefix)}\s*(\S+)", clean_message)
        if not keyword_match:
            await self.api.qq.post_group_msg(
                group_id=input.group_id,
                text=(
                    f"{delete_prefix}格式错误！请使用：{delete_prefix} 关键词，"
                    f"或回复图片并发送：{delete_prefix} 关键词"
                ),
            )
            return

        keyword = keyword_match.group(1)

        found = self._find_command(keyword)
        if not found:
            await self.api.qq.post_group_msg(
                group_id=input.group_id, text=f"未知的关键词：{keyword}"
            )
            return
        _, command_config = found

        # 优先从回复消息中获取要删除的图片
        image_matches = await self.get_images_from_reply(input)

        # 如果回复中没有图片，则从当前消息提取图片标签
        if not image_matches:
            image_pattern = r"\[CQ:image,.*?file=([^,]+),.*?url=([^\]]+)\]"
            image_matches = re.findall(image_pattern, message)

        if not image_matches:
            await self.api.qq.post_group_msg(
                group_id=input.group_id,
                text="未找到要删除的图片！请回复要删除的图片，或在同一条消息中附带图片。",
            )
            return

        success_count = 0
        not_found_count = 0
        failed_count = 0

        target_path = command_config["path"]

        for _, url in image_matches:
            result, status = await self.delete_image_by_url(url, target_path)
            if result:
                success_count += 1
            elif status == "not_found":
                not_found_count += 1
            else:
                failed_count += 1

        # 构建删除结果消息
        result_message = f"删除完成！成功: {success_count} 张，未找到: {not_found_count} 张，失败: {failed_count} 张"
        await self.api.qq.post_group_msg(group_id=input.group_id, text=result_message)

    async def download_and_save_image(
        self, url: str, filename: str, target_path: str, user_id: int
    ) -> tuple[bool, str]:
        """下载并保存图片到指定路径，返回(是否成功, 状态信息)"""
        return await asyncio.to_thread(
            self._download_and_save_image_sync, url, filename, target_path, user_id
        )

    def _download_and_save_image_sync(
        self, url: str, filename: str, target_path: str, user_id: int
    ) -> tuple[bool, str]:
        """同步下载并保存图片（供 to_thread 调用）"""
        try:
            # 如果路径不是绝对路径，则转换为绝对路径
            if not os.path.isabs(target_path):
                current_dir = os.getcwd()
                target_path = os.path.join(current_dir, target_path)

            # 确保目标目录存在
            os.makedirs(target_path, exist_ok=True)

            # 解码HTML实体（如 &amp; -> &）
            decoded_url = html.unescape(url)

            # 设置请求头，模拟浏览器
            headers = {
                "User-Agent": HMMT.USER_AGENT,
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Referer": "https://multimedia.nt.qq.com.cn/",
            }

            # 下载图片
            response = requests.get(decoded_url, headers=headers, timeout=30)
            response.raise_for_status()

            # 计算图片内容的MD5哈希值
            image_content = response.content
            image_hash = hashlib.md5(image_content).hexdigest()

            # 检查是否已存在相同内容的图片（复用删除逻辑的 MD5 匹配方法）
            matched_files = self._find_matching_files(target_path, image_hash)
            if matched_files:
                _log.info(f"图片已存在，跳过上传: {matched_files[0]}")
                return False, "duplicate"  # 返回重复状态

            # 生成带时间戳和用户ID的文件名
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            name, ext = os.path.splitext(filename)
            new_filename = f"{user_id}_{timestamp}_{name}{ext}"

            # 构建完整的文件路径
            file_path = os.path.join(target_path, new_filename)

            # 保存图片
            with open(file_path, "wb") as f:
                f.write(image_content)

            _log.info(f"图片已保存到: {file_path}")
            return True, "success"

        except Exception as e:
            _log.error(f"下载图片失败 {url}: {str(e)}")
            return False, "failed"

    async def delete_image_by_url(self, url: str, target_path: str) -> tuple[bool, str]:
        """根据图片 URL 在指定路径中查找并删除对应图片，返回(是否成功, 状态信息)"""
        return await asyncio.to_thread(self._delete_image_by_url_sync, url, target_path)

    def _delete_image_by_url_sync(self, url: str, target_path: str) -> tuple[bool, str]:
        try:
            # 如果路径不是绝对路径，则转换为绝对路径
            if not os.path.isabs(target_path):
                current_dir = os.getcwd()
                target_path = os.path.join(current_dir, target_path)

            # 解码HTML实体（如 &amp; -> &）
            decoded_url = html.unescape(url)

            # 设置请求头，模拟浏览器
            headers = {
                "User-Agent": HMMT.USER_AGENT,
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Referer": "https://multimedia.nt.qq.com.cn/",
            }

            # 下载图片内容
            response = requests.get(decoded_url, headers=headers, timeout=30)
            response.raise_for_status()
            image_content = response.content
            image_hash = hashlib.md5(image_content).hexdigest()

            matched_files = self._find_matching_files(target_path, image_hash)

            if not matched_files:
                _log.info(
                    f"未在路径 {target_path} 中找到与给定图片内容匹配的文件，跳过删除"
                )
                return False, "not_found"

            deleted_any = False
            for file_path in matched_files:
                try:
                    os.remove(file_path)
                    _log.info(f"已删除图片文件: {file_path}")
                    deleted_any = True
                except FileNotFoundError:
                    continue
                except Exception as e:
                    _log.warning(f"删除文件失败 {file_path}: {e}")

            return (True, "success") if deleted_any else (False, "failed")

        except Exception as e:
            _log.error(f"根据 URL 删除图片失败 {url}: {str(e)}")
            return False, "failed"

    def _find_matching_files(self, target_path: str, image_hash: str) -> list[str]:
        """
        在目录中查找与目标图片匹配的文件：
        仅按 MD5 完全一致进行匹配（与早期重复判定逻辑保持一致）
        返回匹配文件路径列表（可能包含多张相同内容的副本）
        """
        matching_files = []
        existing_files = self.get_image_files(target_path)

        # 先做 MD5 精确匹配
        for existing_file in existing_files:
            try:
                with open(existing_file, "rb") as f:
                    existing_content = f.read()
                    existing_hash = hashlib.md5(existing_content).hexdigest()
                    if existing_hash == image_hash:
                        matching_files.append(existing_file)
            except Exception as e:
                _log.warning(f"读取文件失败 {existing_file}: {e}")

        if matching_files:
            return matching_files
        return matching_files

    @staticmethod
    def get_image_files(folder_path):
        # 如果路径不是绝对路径，则转换为绝对路径
        if not os.path.isabs(folder_path):
            current_dir = os.getcwd()
            folder_path = os.path.join(current_dir, folder_path)

        if os.path.isdir(folder_path):
            return [
                os.path.join(folder_path, f)
                for f in os.listdir(folder_path)
                if f.lower().endswith((".jpg", ".png", ".jpeg", ".gif"))
            ]
        return []

    async def recall_message(self, message_id: int, recall_time: int):
        """
        撤回消息
        """
        await asyncio.sleep(recall_time)
        _log.info(f"正在撤回消息 ID: {message_id}")  # 添加日志
        # 撤回指定的消息
        await self.api.qq.manage.delete_msg(message_id)
