"""
消息预上传处理器

遍历消息结构，对需要上传的文件进行预上传处理。
合并了旧的 processor.py + utils.py。
"""

import copy
import os
import re
import uuid
import base64 as b64
from typing import Any, Dict, List, Optional, TYPE_CHECKING
from dataclasses import dataclass, field

from ncatbot.utils import get_log

if TYPE_CHECKING:
    from .client import StreamUploadClient

LOG = get_log("MessagePreUploadProcessor")

# ==================== 工具函数（原 utils.py）====================

DOWNLOADABLE_TYPES = frozenset({"image", "video", "record", "file"})
BASE64_PREFIX = "base64://"
URL_PATTERN = re.compile(r"^https?://", re.IGNORECASE)
FILE_PREFIX = "file://"


def is_local_file(file_value: str) -> bool:
    if not file_value or not isinstance(file_value, str):
        return False
    if file_value.startswith(FILE_PREFIX):
        return True
    if file_value.startswith(BASE64_PREFIX):
        return False
    if URL_PATTERN.match(file_value):
        return False
    if os.path.isabs(file_value):
        return True
    if os.sep in file_value or "/" in file_value:
        return True
    if "." in file_value and not file_value.startswith("."):
        return True
    return False


def is_base64_data(file_value: str) -> bool:
    if not file_value or not isinstance(file_value, str):
        return False
    return file_value.startswith(BASE64_PREFIX)


def is_remote_url(file_value: str) -> bool:
    if not file_value or not isinstance(file_value, str):
        return False
    return bool(URL_PATTERN.match(file_value))


def needs_upload(file_value: str) -> bool:
    return is_local_file(file_value) or is_base64_data(file_value)


def get_local_path(file_value: str) -> Optional[str]:
    if not is_local_file(file_value):
        return None
    if file_value.startswith(FILE_PREFIX):
        return file_value[len(FILE_PREFIX) :]
    return file_value


def extract_base64_data(file_value: str) -> Optional[bytes]:
    if not is_base64_data(file_value):
        return None
    try:
        return b64.b64decode(file_value[len(BASE64_PREFIX) :])
    except Exception:
        return None


def generate_filename_from_type(msg_type: str) -> str:
    extensions = {"image": ".jpg", "video": ".mp4", "record": ".mp3", "file": ".bin"}
    ext = extensions.get(msg_type, ".bin")
    return f"{uuid.uuid4().hex[:8]}{ext}"


# ==================== 处理结果 ====================


@dataclass
class ProcessResult:
    success: bool
    data: Optional[Dict[str, Any]] = None
    errors: List[str] = field(default_factory=list)
    uploaded_count: int = 0


# ==================== 处理器 ====================


class MessagePreUploadProcessor:
    """消息预上传处理器

    遍历消息字典结构，识别需要上传的文件，执行上传后替换路径。
    """

    def __init__(self, upload_client: "StreamUploadClient"):
        self._client = upload_client

    async def process(self, data: Dict[str, Any]) -> ProcessResult:
        errors: List[str] = []
        uploaded_count = 0
        result_data = copy.deepcopy(data)

        try:
            uploaded_count = await self._process_node(result_data, errors)
            return ProcessResult(
                success=len(errors) == 0,
                data=result_data,
                errors=errors,
                uploaded_count=uploaded_count,
            )
        except Exception as e:
            LOG.error(f"消息预处理失败: {e}")
            errors.append(str(e))
            return ProcessResult(
                success=False,
                data=result_data,
                errors=errors,
                uploaded_count=uploaded_count,
            )

    async def process_message_array(
        self,
        messages: List[Dict[str, Any]],
    ) -> ProcessResult:
        errors: List[str] = []
        total_uploaded = 0
        result = copy.deepcopy(messages)

        for i, msg in enumerate(result):
            try:
                count = await self._process_node(msg, errors)
                total_uploaded += count
            except Exception as e:
                LOG.error(f"处理消息段 {i} 失败: {e}")
                errors.append(f"消息段 {i}: {e}")

        return ProcessResult(
            success=len(errors) == 0,
            data={"message": result},
            errors=errors,
            uploaded_count=total_uploaded,
        )

    async def _process_node(self, node: Any, errors: List[str]) -> int:
        uploaded_count = 0
        if isinstance(node, dict):
            uploaded_count += await self._process_dict(node, errors)
        elif isinstance(node, list):
            for item in node:
                uploaded_count += await self._process_node(item, errors)
        return uploaded_count

    async def _process_dict(self, node: Dict[str, Any], errors: List[str]) -> int:
        uploaded_count = 0
        msg_type = node.get("type")

        if msg_type in DOWNLOADABLE_TYPES:
            uploaded_count += await self._process_downloadable(node, errors)

        if msg_type == "forward":
            content = node.get("data", {}).get("content")
            if content:
                uploaded_count += await self._process_node(content, errors)

        if msg_type == "node":
            data = node.get("data", {})
            content = data.get("content")
            if content:
                uploaded_count += await self._process_node(content, errors)
            message = data.get("message")
            if message:
                uploaded_count += await self._process_node(message, errors)

        for key, value in node.items():
            if key in ("type", "data"):
                continue
            uploaded_count += await self._process_node(value, errors)

        return uploaded_count

    async def _process_downloadable(
        self,
        node: Dict[str, Any],
        errors: List[str],
    ) -> int:
        data = node.get("data", {})
        file_value = data.get("file")

        if not file_value or not needs_upload(file_value):
            return 0

        msg_type = node.get("type", "file")

        try:
            if is_local_file(file_value):
                result = await self._upload_local_file(file_value)
            elif is_base64_data(file_value):
                result = await self._upload_base64(file_value, msg_type)
            else:
                return 0

            if result:
                data["file"] = result
                LOG.debug(f"文件已上传: {file_value[:50]}... -> {result}")
                return 1
            else:
                errors.append(f"上传失败: {file_value[:50]}...")
                return 0

        except Exception as e:
            LOG.error(f"处理文件失败: {e}")
            errors.append(f"文件处理错误: {e}")
            return 0

    async def _upload_local_file(self, file_value: str) -> Optional[str]:
        local_path = get_local_path(file_value)
        if not local_path:
            return None
        result = await self._client.upload_file(local_path)
        return result.file_path if result.success else None

    async def _upload_base64(self, file_value: str, msg_type: str) -> Optional[str]:
        data = extract_base64_data(file_value)
        if not data:
            return None
        filename = generate_filename_from_type(msg_type)
        result = await self._client.upload_bytes(data, filename)
        return result.file_path if result.success else None
