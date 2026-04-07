"""
预上传服务 — 统一入口
"""

from typing import Any, Dict, List, TYPE_CHECKING

from ncatbot.utils import get_log
from .client import StreamUploadClient
from .models import (
    DEFAULT_CHUNK_SIZE,
    DEFAULT_FILE_RETENTION,
    PreUploadResult,
    UploadResult,
)
from .processor import (
    MessagePreUploadProcessor,
    ProcessResult,
    is_local_file,
    is_base64_data,
    is_remote_url,
    get_local_path,
    extract_base64_data,
    generate_filename_from_type,
)

if TYPE_CHECKING:
    from ...connection.protocol import OB11Protocol

LOG = get_log("PreUploadService")


class PreUploadService:
    """预上传服务"""

    def __init__(
        self,
        protocol: "OB11Protocol",
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        file_retention: int = DEFAULT_FILE_RETENTION,
    ):
        self._client = StreamUploadClient(protocol, chunk_size, file_retention)
        self._processor = MessagePreUploadProcessor(self._client)

    # ---- 底层上传 ----

    async def upload_file(self, file_path: str) -> UploadResult:
        return await self._client.upload_file(file_path)

    async def upload_bytes(self, data: bytes, filename: str) -> UploadResult:
        return await self._client.upload_bytes(data, filename)

    # ---- 消息预上传 ----

    async def process_message(self, data: Dict[str, Any]) -> ProcessResult:
        return await self._processor.process(data)

    async def process_message_array(
        self,
        messages: List[Dict[str, Any]],
    ) -> ProcessResult:
        return await self._processor.process_message_array(messages)

    # ---- 文件预上传 ----

    async def preupload_file(
        self,
        file_value: str,
        file_type: str = "file",
    ) -> PreUploadResult:
        if not file_value:
            return PreUploadResult(success=False, error="文件路径为空")

        if is_remote_url(file_value):
            return PreUploadResult(
                success=True, file_path=file_value, original_path=file_value
            )

        if is_local_file(file_value):
            return await self._upload_local(file_value)

        if is_base64_data(file_value):
            return await self._upload_base64(file_value, file_type)

        return PreUploadResult(
            success=True, file_path=file_value, original_path=file_value
        )

    async def preupload_file_if_needed(
        self,
        file_value: str,
        file_type: str = "file",
    ) -> str:
        result = await self.preupload_file(file_value, file_type)
        if not result.success or result.file_path is None:
            raise RuntimeError(f"文件预上传失败: {result.error}")
        return result.file_path

    async def _upload_local(self, file_value: str) -> PreUploadResult:
        local_path = get_local_path(file_value)
        if not local_path:
            return PreUploadResult(
                success=False, original_path=file_value, error="无法解析本地文件路径"
            )

        result = await self._client.upload_file(local_path)
        if result.success:
            LOG.debug(f"文件预上传成功: {local_path} -> {result.file_path}")
            return PreUploadResult(
                success=True, file_path=result.file_path, original_path=file_value
            )
        return PreUploadResult(
            success=False, original_path=file_value, error=result.error
        )

    async def _upload_base64(self, file_value: str, file_type: str) -> PreUploadResult:
        data = extract_base64_data(file_value)
        if not data:
            return PreUploadResult(
                success=False, original_path=file_value, error="Base64 解码失败"
            )

        filename = generate_filename_from_type(file_type)
        result = await self._client.upload_bytes(data, filename)
        if result.success:
            LOG.debug(f"Base64 预上传成功: {file_value[:30]}... -> {result.file_path}")
            return PreUploadResult(
                success=True, file_path=result.file_path, original_path=file_value
            )
        return PreUploadResult(
            success=False, original_path=file_value, error=result.error
        )
