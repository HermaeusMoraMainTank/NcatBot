"""QQ 平台杂项类型"""

from __future__ import annotations

from pydantic import BaseModel

__all__ = [
    "Anonymous",
    "FileInfo",
    "Status",
]


class Anonymous(BaseModel):
    id: int
    name: str
    flag: str


class FileInfo(BaseModel):
    id: str
    name: str
    size: int
    busid: int


class Status(BaseModel):
    online: bool = True
    good: bool = True
