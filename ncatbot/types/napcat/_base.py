"""NapCat API 响应模型基类"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator


class NapCatModel(BaseModel):
    """NapCat API 响应数据基类

    - ``extra="allow"`` 避免 NapCat 协议变动导致解析失败
    - ``_coerce_ids`` 将所有 ``*_id`` 字段统一转为 str
    - 支持 dict 式下标访问 (``result["message_id"]``) 以降低迁移成本
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    @model_validator(mode="before")
    @classmethod
    def _coerce_ids(cls, data: Any) -> Any:
        if isinstance(data, dict):
            for key, value in data.items():
                if key.endswith("_id") and isinstance(value, (int, float)):
                    data[key] = str(int(value))
        return data

    # ---- dict 兼容层 ----

    def __getitem__(self, key: str) -> Any:
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key)

    def __contains__(self, key: str) -> bool:
        return hasattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)
