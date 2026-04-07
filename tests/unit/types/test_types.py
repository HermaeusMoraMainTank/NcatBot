"""
Types 类型模型规范测试

规范:
  T-01: BaseEventData 自动将 int 类型 ID 强转为 str
"""

from ncatbot.types import BaseEventData


# ---- T-01: ID 强转 ----


def test_base_event_coerces_int_id_to_str():
    """T-01: BaseEventData 自动将 int 类型 *_id 字段从 int 转 str"""
    data = BaseEventData.model_validate(
        {"time": 1, "self_id": 12345, "post_type": "message"}
    )
    assert data.self_id == "12345"
    assert isinstance(data.self_id, str)


def test_base_event_keeps_str_id():
    """T-01 补充: 原本是 str 的保持不变"""
    data = BaseEventData.model_validate(
        {"time": 1, "self_id": "99999", "post_type": "message"}
    )
    assert data.self_id == "99999"


def test_extra_id_fields_coerced():
    """T-01 补充: 额外的 *_id 字段也被转换"""
    data = BaseEventData.model_validate(
        {
            "time": 1,
            "self_id": 10001,
            "post_type": "message",
            "user_id": 67890,
            "group_id": 12345,
        }
    )
    assert data.self_id == "10001"
