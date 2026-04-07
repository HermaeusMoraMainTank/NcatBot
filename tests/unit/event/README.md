# Event 模块测试

源码模块: `ncatbot.event`

## 验证规范

### 事件实体工厂 (`test_event_factory.py`)

| 规范 ID | 说明 | 验证点 |
|---------|------|--------|
| E-01 | GroupMessageEventData 映射 | `create_entity()` 返回 `GroupMessageEvent` |
| E-02 | PrivateMessageEventData 映射 | `create_entity()` 返回 `PrivateMessageEvent` |
| E-03 | 未知 post_type 降级 | 未知类型降级到 `BaseEvent` |
| E-04 | `__getattr__` 属性代理 | EventEntity 代理底层 EventData 字段 |

### GitHub 事件实体 (`test_github_events.py`)

| 规范 ID | 说明 | 验证点 |
|---------|------|--------|
| GHE-01 | GitHubBaseEvent 公共属性 | `api` / `user_id` / `sender` / `repo` / `action` |
| GHE-02 | 所有事件继承公共属性 | 工厂创建正确类型 + 属性代理 |
| GHE-03 | `GitHubIssueEvent.reply()` | 调用 `create_issue_comment` API |
| GHE-04 | `get_attachments()` 转换 | 将 `GitHubReleaseAsset` 转为 `Attachment` |

### QQ 消息附件 (`test_qq_message_attachments.py`)

| 规范 ID | 说明 | 验证点 |
|---------|------|--------|
| QMA-01 | QQ MessageEvent isinstance HasAttachments | 类型检查通过 |
| QMA-02 | `get_attachments()` 从消息段提取附件 | 图片/视频段转 Attachment |
| QMA-03 | 纯文本消息返回空 | `get_attachments()` 返回空 `AttachmentList` |

### 关键实现细节

- `MockBotAPI._record()` 将所有参数以命名形式存储在 `APICall.params` 字典中
- 测试中通过 `call.params["key"]` 来验证参数，或使用 `extract_text(call)` 提取文本内容
