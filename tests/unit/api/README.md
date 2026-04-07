# API 模块测试

源码模块: `ncatbot.api`

## 验证规范

### BotAPIClient (`test_api_client.py`)

| 规范 ID | 说明 | 验证点 |
|---------|------|--------|
| A-01 | `query` 命名空间 | 包含 `get_group_info` 等查询操作 |
| A-02 | `__getattr__` 兜底 | 未定义方法透传到底层 API |

### API 错误层级 (`test_api_errors.py`)

| 规范 ID | 说明 | 验证点 |
|---------|------|--------|
| AE-01 | `APIError` 构造 | 可用 `retcode` / `message` / `wording` 构造 |
| AE-02 | `retcode=0` 不抛异常 | `raise_for_retcode` 正常返回 |
| AE-03 | `retcode=1400` | 抛 `APIRequestError` |
| AE-04 | `retcode=1401` | 抛 `APIPermissionError` |
| AE-05 | `retcode=1404` | 抛 `APINotFoundError` |
| AE-06 | 未知 retcode | 抛基类 `APIError` |
| AE-07 | 继承关系 | 所有子类都是 `APIError` 实例 |

### QQ Sugar 便捷方法 (`test_qq_sugar.py`)

| 规范 ID | 说明 | 验证点 |
|---------|------|--------|
| SG-01 | `post_group_msg` 组装消息 | text+at+reply+image 组装后调用 `send_group_msg` |
| SG-02 | `post_private_msg` 基本消息 | 基本消息发送 |
| SG-03 | 群快捷方法 | `send_group_text` / `send_group_image` |
| SG-04 | 私聊快捷方法 | `send_private_text` / `send_private_image` |
| SG-05 | `_build_message_array` 组装 | 消息段按正确顺序组装 |
| SG-06 | Segment 对象直接传入 | `image=Image(...)` 等 Segment 对象 |

### QQ 文件操作 (`test_qq_file.py`)

| 规范 ID | 说明 | 验证点 |
|---------|------|--------|
| FL-01 | 根目录已存在文件夹 | 直接返回 `folder_id` |
| FL-02 | 根目录不存在文件夹 | 创建并返回 `folder_id` |
| FL-03 | 子文件夹查找 | 使用 `parent_id` 查找子目录 |
| FL-04 | 子文件夹创建 | 子目录不存在时创建 |
| FL-05 | 空文件夹列表 | 无文件夹时正确创建 |
| FL-06 | 名称精确匹配 | 不误匹配相似名称 |
