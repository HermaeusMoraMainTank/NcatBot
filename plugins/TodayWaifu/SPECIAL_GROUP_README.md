# 特殊群组功能说明

## 功能概述

在群组 `585479130` 中，实现了特殊的"今日老婆"分配规则：

1. **普通用户**：不会抽到用户 `3860435136` 和 `273421673`
2. **特殊用户**：用户 `3860435136` 和 `273421673` 触发"今日老婆"时，默认分配对方为老婆

## 文件结构

```
TodayWaifu/
├── TodayWaifu.py              # 主插件文件
├── special_group_handler.py   # 特殊群组处理模块
├── test_special_handler.py    # 测试脚本
└── SPECIAL_GROUP_README.md    # 本说明文档
```

## 特殊规则详情

### 群组ID
- 特殊群组：`585479130`

### 特殊用户
- 用户1：`3860435136`
- 用户2：`273421673`

### 分配规则
- `3860435136` 的默认老婆：`273421673`
- `273421673` 的默认老婆：`3860435136`

## 如何删除此功能

如果你想删除这个特殊功能，只需要：

1. **删除特殊处理模块**：
   ```bash
   rm special_group_handler.py
   ```

2. **修改主插件文件**：
   - 删除 `from .special_group_handler import SpecialGroupHandler` 导入语句
   - 删除 `get_random_wife` 方法中的特殊过滤逻辑
   - 删除"今日老婆"处理中的特殊逻辑部分

3. **删除测试文件**（可选）：
   ```bash
   rm test_special_handler.py
   rm SPECIAL_GROUP_README.md
   ```

## 测试

运行测试脚本验证功能：

```bash
cd NcatBot/plugins/TodayWaifu
python test_special_handler.py
```

## 注意事项

- 此功能仅在群组 `585479130` 中生效
- 特殊用户必须同时在群组中才能正常分配
- 如果特殊用户不在群组中，会回退到普通随机分配逻辑 