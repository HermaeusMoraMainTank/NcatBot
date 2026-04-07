"""
预上传数据模型和常量

合并旧的 constants.py + result.py。
"""

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional


# ==================== 常量 ====================

DEFAULT_CHUNK_SIZE = 500 * 1024  # 500KB
DEFAULT_FILE_RETENTION = 600 * 1000  # 600 秒（毫秒）


class StreamStatus(str, Enum):
    NORMAL_ACTION = "normal-action"
    STREAM_ACTION = "stream-action"


class StreamResponseType(str, Enum):
    STREAM = "stream"
    RESPONSE = "response"
    ERROR = "error"


# ==================== 数据类 ====================


@dataclass
class ChunkInfo:
    """分片信息"""

    data: bytes
    index: int
    base64_data: str


@dataclass
class FileAnalysis:
    """文件分析结果"""

    chunks: List[ChunkInfo]
    sha256_hash: str
    total_size: int
    filename: str


@dataclass
class UploadResult:
    """上传结果"""

    success: bool
    file_path: Optional[str] = None
    file_size: Optional[int] = None
    sha256: Optional[str] = None
    error: Optional[str] = None


@dataclass
class PreUploadResult:
    """预上传结果"""

    success: bool
    file_path: Optional[str] = None
    original_path: Optional[str] = None
    error: Optional[str] = None

    @property
    def uploaded(self) -> bool:
        return self.success and self.file_path != self.original_path
