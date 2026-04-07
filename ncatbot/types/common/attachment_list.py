"""AttachmentList — 带类型过滤的附件容器"""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path
from typing import (
    Generic,
    Iterable,
    Iterator,
    List,
    Optional,
    SupportsIndex,
    TypeVar,
    Union,
    overload,
)

from .attachment import (
    Attachment,
    AttachmentKind,
    AudioAttachment,
    FileAttachment,
    ImageAttachment,
    VideoAttachment,
)

__all__ = ["AttachmentList"]

T = TypeVar("T", bound=Attachment)


class AttachmentList(list, Generic[T]):
    """带类型过滤能力的泛型附件列表

    继承 ``list``，完全兼容 ``List[Attachment]`` 的所有用法。

    - 迭代时元素类型为 ``T``
    - 下标访问返回 ``T``（单个）或 ``AttachmentList[T]``（切片）
    - 过滤方法（``images()`` 等）返回精确子类型的 ``AttachmentList``

    典型用法::

        attachments: AttachmentList[Attachment] = event.get_attachments()
        images: AttachmentList[ImageAttachment] = attachments.images()
        first: ImageAttachment | None = images.first()
    """

    # ---- 类型注解覆盖（iteration / index access） ----

    def __iter__(self) -> Iterator[T]:
        return super().__iter__()  # type: ignore[return-value]

    @overload
    def __getitem__(self, index: SupportsIndex) -> T: ...
    @overload
    def __getitem__(self, index: slice) -> AttachmentList[T]: ...

    def __getitem__(self, index: SupportsIndex | slice) -> T | AttachmentList[T]:
        result = super().__getitem__(index)
        if isinstance(index, slice):
            return AttachmentList(result)
        return result  # type: ignore[return-value]

    def append(self, item: T) -> None:  # type: ignore[override]
        super().append(item)

    def insert(self, index: SupportsIndex, item: T) -> None:  # type: ignore[override]
        super().insert(index, item)

    def extend(self, items: Iterable[T]) -> None:  # type: ignore[override]
        super().extend(items)

    def __add__(self, other: Iterable[T]) -> AttachmentList[T]:  # type: ignore[override]
        return AttachmentList(list.__add__(self, list(other)))

    def __iadd__(self, other: Iterable[T]) -> AttachmentList[T]:  # type: ignore[override]
        super().__iadd__(other)
        return self

    # ---- 按类型过滤 ----

    def images(self) -> AttachmentList[ImageAttachment]:
        return AttachmentList(a for a in self if isinstance(a, ImageAttachment))

    def videos(self) -> AttachmentList[VideoAttachment]:
        return AttachmentList(a for a in self if isinstance(a, VideoAttachment))

    def audios(self) -> AttachmentList[AudioAttachment]:
        return AttachmentList(a for a in self if isinstance(a, AudioAttachment))

    def files(self) -> AttachmentList[FileAttachment]:
        return AttachmentList(a for a in self if isinstance(a, FileAttachment))

    def by_kind(self, *kinds: AttachmentKind) -> AttachmentList[Attachment]:
        return AttachmentList(a for a in self if a.kind in kinds)

    def by_content_type(self, pattern: str) -> AttachmentList[Attachment]:
        """按 MIME 类型 glob 过滤，如 ``"image/*"``"""
        return AttachmentList(
            a for a in self if a.content_type and fnmatch(a.content_type, pattern)
        )

    # ---- 选取 ----

    def first(self) -> Optional[T]:
        return self[0] if self else None

    def largest(self) -> Optional[T]:
        sized = [a for a in self if a.size is not None]
        return max(sized, key=lambda a: a.size) if sized else None  # type: ignore[arg-type]

    def smallest(self) -> Optional[T]:
        sized = [a for a in self if a.size is not None]
        return min(sized, key=lambda a: a.size) if sized else None  # type: ignore[arg-type]

    # ---- 批量操作 ----

    async def download_all(self, dest: Union[str, Path]) -> List[Path]:
        """逐个下载所有附件到目标目录"""
        return [await a.download(dest) for a in self]
