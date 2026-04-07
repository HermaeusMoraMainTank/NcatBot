"""
NapCat 响应类型模型规范测试

规范:
  N-01: NapCatModel 自动将 int 类型 *_id 字段转为 str
  N-03: NapCatModel 允许额外字段 (extra="allow")
  N-04: SendMessageResult 将 int message_id 转为 str
"""

from ncatbot.types.napcat import (
    NapCatModel,
    SendMessageResult,
)


# ---- N-01: ID 强转 ----


class _IdModel(NapCatModel):
    user_id: str = ""
    group_id: str = ""


def test_napcat_model_coerces_int_id_to_str():
    """N-01: NapCatModel 将 int 类型 *_id 字段自动转为 str"""
    m = _IdModel.model_validate({"user_id": 12345, "group_id": 67890})
    assert m.user_id == "12345"
    assert m.group_id == "67890"


# ---- N-03: 额外字段 ----


def test_napcat_model_extra_fields():
    """N-03: NapCatModel 允许额外字段"""
    m = SendMessageResult.model_validate({"message_id": "1", "extra_field": "value"})
    assert m.message_id == "1"
    assert m.extra_field == "value"


# ---- N-04: SendMessageResult ----


def test_send_message_result_int_coerce():
    """N-04: SendMessageResult 将 int message_id 转为 str"""
    r = SendMessageResult.model_validate({"message_id": 123})
    assert r.message_id == "123"
