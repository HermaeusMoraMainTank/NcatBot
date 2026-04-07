# 多平台变更参考

## 添加新平台

按顺序创建以下目录/文件：

| 目录 | 说明 |
|------|------|
| `ncatbot/types/<platform>/` | 平台专用数据类型 |
| `ncatbot/event/<platform>/` | 平台专用事件实体 + 事件工厂 |
| `ncatbot/api/<platform>/` | 平台 API 接口（实现 `api/traits` 中的 trait） |
| `ncatbot/adapter/<platform>/` | 平台适配器（WebSocket/HTTP 连接层） |

---

## 通用层变更的兼容性检查

修改以下通用层时，**必须检查所有已有平台的兼容性**：

| 通用层 | 检查点 |
|--------|--------|
| `types/common` | 已有平台的类型是否继承/依赖此类型 |
| `event/common` | 已有平台的事件工厂是否产出此事件类型 |
| `api/traits` | 所有平台的 API 实现是否仍满足 trait 接口 |

---

## 导入规范（平台子模块白名单）

允许二级平台子模块导入的白名单（完整规则见 [import-conventions.md](./import-conventions.md)）：

| Layer | 允许的二级子模块 |
|-------|----------------|
| `event` | `event.qq`, `event.bilibili`, `event.github`, `event.lark`, `event.common` |
| `types` | `types.qq`, `types.bilibili`, `types.github`, `types.lark`, `types.common`, `types.napcat` |
| `api` | `api.qq`, `api.bilibili`, `api.github`, `api.traits` |
| `adapter` | `adapter.napcat`, `adapter.mock`, `adapter.bilibili`, `adapter.github`, `adapter.lark` |
