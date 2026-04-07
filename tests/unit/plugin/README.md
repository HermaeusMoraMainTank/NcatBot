# Plugin Mixin 模块测试

源码模块: `ncatbot.plugin.mixin`

## 验证规范

### ConfigMixin (`test_config_mixin.py`)

| 规范 ID | 说明 | 验证点 |
|---------|------|--------|
| M-01 | `_mixin_load()` | 从 YAML 文件加载配置 |
| M-02 | `set_config()` | 设置配置项后立即持久化 |
| M-03 | `get_config(key, default)` | key 不存在时返回默认值 |
| M-04 | `remove_config()` | 移除配置项，返回 `bool` |
| M-05 | `update_config()` | 批量更新多个配置项 |
| M-06 | 配置文件不存在 | 优雅返回空字典 |

### DataMixin (`test_data_mixin.py`)

| 规范 ID | 说明 | 验证点 |
|---------|------|--------|
| M-10 | `_mixin_load()` | 从 JSON 文件加载数据 |
| M-11 | `_mixin_unload()` | 将数据保存到 JSON |
| M-12 | 数据文件不存在 | 优雅返回空字典 |

### EventMixin (`test_event_mixin.py`)

| 规范 ID | 说明 | 验证点 |
|---------|------|--------|
| M-20 | `events()` | 返回 EventStream 并追踪到 `_active_streams` |
| M-21 | `wait_event(timeout)` | 超时抛出 `TimeoutError` |
| M-22 | `_mixin_unload()` | 关闭所有活跃 stream |

### TimeTaskMixin (`test_time_task_mixin.py`)

| 规范 ID | 说明 | 验证点 |
|---------|------|--------|
| M-30 | `add_scheduled_task` | 服务可用时返回 `True` |
| M-31 | `remove_scheduled_task` | 服务可用时返回 `True` |
| M-32 | 服务不可用 | 优雅返回 `False` / `None` |
| M-33 | `get_task_status` | 服务可用时返回状态信息 |

### RBACMixin (`test_rbac_mixin.py`)

| 规范 ID | 说明 | 验证点 |
|---------|------|--------|
| M-40 | 权限/角色操作 | `check_permission` / `add_permission` / `add_role` 等代理到 RBACService |
| M-41 | 服务不可用 | 所有操作返回 `False` |

### Import Dedup (`test_init_import_dedup.py`)

| 规范 ID | 说明 | 验证点 |
|---------|------|--------|
| ID-01 | `__init__.py` 导入不导致双重 exec | `from .main import X` 不会导致 handler 重复注册 |
| ID-02 | 无 `__init__` 导入时行为不变 | `load_module` 复用已导入模块，pending 数量正确 |

### PluginLoader 生命周期 (`test_plugin_loader.py`)

| 规范 ID | 说明 | 验证点 |
|---------|------|--------|
| LD-01 | `load_plugin` 成功 | handler flush 到 dispatcher |
| LD-02 | `load_plugin` 异常 | handler 清理回滚 |
| LD-03 | `unload_plugin` | handler revoke + on_close |
| LD-04 | `load_all` 拓扑排序 | 按依赖顺序加载 |
| LD-05 | 缺失依赖跳过 | 跳过缺依赖的插件，继续加载其他 |
| LD-06 | `_instantiate` 注入元数据 | name/version/author/description 正确注入 |
| LD-07 | 热重载失败后重试 | reload 失败后再次文件变更仍应尝试加载 |
| LD-08 | 运行时新插件自动索引 | 新放入的插件目录自动索引并加载 |
