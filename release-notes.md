## ✨ 新功能
- **event**: 补齐所有 QQ notice 事件实体 — GroupUploadEvent / GroupAdminEvent / GroupDecreaseEvent / GroupBanEvent / FriendAddEvent / GroupRecallEvent / FriendRecallEvent / NotifyEvent / PokeNotifyEvent / LuckyKingNotifyEvent / HonorNotifyEvent，有 sub_type 的实体代理 sub_type 属性，工厂精确映射从 6 条扩展到 17 条 (49980d09)
- **cli**: 新增 `napcat stop` 命令停止本机 NapCat 进程（仅 Linux），BotClient 关闭时自动停止配置了 `stop_napcat` 的适配器 (51569f8e)
