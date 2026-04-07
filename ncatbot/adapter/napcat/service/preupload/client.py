"""
流式上传客户端

基于 OB11Protocol 的文件流式分片上传。
"""

import base64
import hashlib
import uuid
from pathlib import Path
from typing import List, TYPE_CHECKING

from ncatbot.utils import get_log
from .models import (
    ChunkInfo,
    FileAnalysis,
    UploadResult,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_FILE_RETENTION,
)

if TYPE_CHECKING:
    from ...connection.protocol import OB11Protocol

LOG = get_log("StreamUploadClient")


class StreamUploadClient:
    """流式上传客户端"""

    def __init__(
        self,
        protocol: "OB11Protocol",
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        file_retention: int = DEFAULT_FILE_RETENTION,
    ):
        self._protocol = protocol
        self._chunk_size = chunk_size
        self._file_retention = file_retention

    def analyze_file(self, file_path: str) -> FileAnalysis:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        chunks: List[ChunkInfo] = []
        hasher = hashlib.sha256()
        total_size = 0
        chunk_index = 0

        with open(path, "rb") as f:
            while True:
                chunk = f.read(self._chunk_size)
                if not chunk:
                    break
                hasher.update(chunk)
                total_size += len(chunk)
                base64_data = base64.b64encode(chunk).decode("utf-8")
                chunks.append(
                    ChunkInfo(data=chunk, index=chunk_index, base64_data=base64_data)
                )
                chunk_index += 1

        return FileAnalysis(
            chunks=chunks,
            sha256_hash=hasher.hexdigest(),
            total_size=total_size,
            filename=path.name,
        )

    def analyze_bytes(self, data: bytes, filename: str) -> FileAnalysis:
        chunks: List[ChunkInfo] = []
        hasher = hashlib.sha256()
        hasher.update(data)

        offset = 0
        chunk_index = 0
        while offset < len(data):
            chunk = data[offset : offset + self._chunk_size]
            base64_data = base64.b64encode(chunk).decode("utf-8")
            chunks.append(
                ChunkInfo(data=chunk, index=chunk_index, base64_data=base64_data)
            )
            offset += self._chunk_size
            chunk_index += 1

        return FileAnalysis(
            chunks=chunks,
            sha256_hash=hasher.hexdigest(),
            total_size=len(data),
            filename=filename,
        )

    async def upload_file(self, file_path: str) -> UploadResult:
        try:
            analysis = self.analyze_file(file_path)
            return await self._upload_chunks(analysis)
        except Exception as e:
            LOG.error(f"上传文件失败: {e}")
            return UploadResult(success=False, error=str(e))

    async def upload_bytes(self, data: bytes, filename: str) -> UploadResult:
        try:
            analysis = self.analyze_bytes(data, filename)
            return await self._upload_chunks(analysis)
        except Exception as e:
            LOG.error(f"上传字节数据失败: {e}")
            return UploadResult(success=False, error=str(e))

    async def _upload_chunks(self, analysis: FileAnalysis) -> UploadResult:
        stream_id = str(uuid.uuid4())
        total_chunks = len(analysis.chunks)

        LOG.debug(
            f"开始上传: {analysis.filename}, "
            f"大小: {analysis.total_size}, 分片数: {total_chunks}"
        )

        for chunk in analysis.chunks:
            params = {
                "stream_id": stream_id,
                "chunk_data": chunk.base64_data,
                "chunk_index": chunk.index,
                "total_chunks": total_chunks,
                "file_size": analysis.total_size,
                "expected_sha256": analysis.sha256_hash,
                "filename": analysis.filename,
                "file_retention": self._file_retention,
            }

            response = await self._protocol.call("upload_file_stream", params)

            if response.get("status") != "ok":
                error_msg = response.get("message", "Unknown error")
                LOG.error(f"上传分片 {chunk.index} 失败: {error_msg}")
                return UploadResult(success=False, error=error_msg)

        # 完成信号
        response = await self._protocol.call(
            "upload_file_stream",
            {
                "stream_id": stream_id,
                "is_complete": True,
            },
        )

        if response.get("status") != "ok":
            error_msg = response.get("message", "Unknown error")
            return UploadResult(success=False, error=error_msg)

        result_data = response.get("data", {})

        if result_data.get("status") == "file_complete":
            LOG.info(f"文件上传成功: {result_data.get('file_path')}")
            return UploadResult(
                success=True,
                file_path=result_data.get("file_path"),
                file_size=result_data.get("file_size"),
                sha256=result_data.get("sha256"),
            )

        return UploadResult(
            success=False,
            error=f"意外的响应状态: {result_data.get('status')}",
        )
