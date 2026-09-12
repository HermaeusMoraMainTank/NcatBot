"""字段映射配置 - 顺序决定显示顺序"""

from datetime import datetime
from typing import Any

from .utils import (
    get_blood_type,
    get_career,
    parse_home_town,
    qqLevel_to_icon,
)

# 字段映射表：保持列表顺序即为显示顺序
# source: "info1" = stranger_info, "info2" = member_info, "computed" = 计算字段
FIELD_MAPPING: list[dict[str, Any]] = [
    {"key": "user_id", "label": "QQ号", "source": "info1"},
    {"key": "nickname", "label": "昵称", "source": "info1"},
    {"key": "remark", "label": "备注", "source": "info1"},
    {"key": "card", "label": "群昵称", "source": "info2"},
    {"key": "title", "label": "群头衔", "source": "info2"},
    {
        "key": "sex",
        "label": "性别",
        "source": "info1",
        "transform": lambda v: {"male": "男", "female": "女"}.get(v),
    },
    {"key": "birthday", "label": "生日", "source": "computed"},
    {"key": "constellation", "label": "星座", "source": "computed"},
    {"key": "zodiac", "label": "生肖", "source": "computed"},
    {"key": "age", "label": "年龄", "source": "info1", "suffix": "岁"},
    {
        "key": "kBloodType",
        "label": "血型",
        "source": "info1",
        "transform": lambda v: get_blood_type(int(v)) if v else None,
    },
    {
        "key": "phoneNum",
        "label": "电话",
        "source": "info1",
        "skip_values": ["-", ""],
    },
    {
        "key": "eMail",
        "label": "邮箱",
        "source": "info1",
        "skip_values": ["-", ""],
    },
    {
        "key": "homeTown",
        "label": "家乡",
        "source": "info1",
        "transform": parse_home_town,
        "skip_values": ["0-0-0", ""],
    },
    {"key": "address", "label": "现居", "source": "computed"},
    {
        "key": "makeFriendCareer",
        "label": "职业",
        "source": "info1",
        "transform": lambda v: get_career(int(v)) if v and v != "0" else None,
        "skip_values": ["0", ""],
    },
    {"key": "labels", "label": "个性标签", "source": "info1"},
    {
        "key": "unfriendly",
        "label": "风险账号",
        "source": "info2",
        "transform": lambda v: "有" if v else None,
    },
    {
        "key": "is_robot",
        "label": "机器人账号",
        "source": "info2",
        "transform": lambda v: "是" if v else None,
    },
    {
        "key": "is_vip",
        "label": "QQVIP",
        "source": "info1",
        "transform": lambda v: "已开" if v else None,
    },
    {
        "key": "is_years_vip",
        "label": "年VIP",
        "source": "info1",
        "transform": lambda v: "已开" if v else None,
    },
    {
        "key": "vip_level",
        "label": "VIP等级",
        "source": "info1",
        "transform": lambda v: str(v) if v and int(v) != 0 else None,
    },
    {
        "key": "level",
        "label": "群等级",
        "source": "info2",
        "suffix": "级",
        "transform": lambda v: str(int(v)) if v else None,
    },
    {
        "key": "join_time",
        "label": "加群时间",
        "source": "info2",
        "transform": lambda v: (
            datetime.fromtimestamp(v).strftime("%Y-%m-%d") if v else None
        ),
    },
    {
        "key": "qqLevel",
        "label": "QQ等级",
        "source": "info1",
        "transform": lambda v: qqLevel_to_icon(int(v)) if v else None,
    },
    {
        "key": "reg_time",
        "label": "注册时间",
        "source": "info1",
        "transform": lambda v: (
            datetime.fromtimestamp(v).strftime("%Y年") if v else None
        ),
    },
    {
        "key": "long_nick",
        "label": "签名",
        "source": "info1",
        "multiline": True,
        "wrap_width": 15,
    },
]

# 中文名 -> 英文字段名 映射
LABEL_TO_KEY: dict[str, str] = {f["label"]: f["key"] for f in FIELD_MAPPING}

# 英文字段名 -> 中文名 映射
KEY_TO_LABEL: dict[str, str] = {f["key"]: f["label"] for f in FIELD_MAPPING}

# 所有可用的中文标签
ALL_LABELS: list[str] = [f["label"] for f in FIELD_MAPPING]

# 默认显示选项
DEFAULT_DISPLAY_OPTIONS: list[str] = [
    "QQ号",
    "昵称",
    "备注",
    "群昵称",
    "群头衔",
    "性别",
    "生日",
    "星座",
    "生肖",
    "年龄",
    "血型",
    "电话",
    "邮箱",
    "家乡",
    "现居",
    "职业",
    "个性标签",
    "风险账号",
    "机器人账号",
    "QQVIP",
    "年VIP",
    "VIP等级",
    "群等级",
    "加群时间",
    "QQ等级",
    "注册时间",
    "签名",
]
