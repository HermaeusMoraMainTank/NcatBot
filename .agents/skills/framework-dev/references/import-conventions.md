# 导入规范

NcatBot 框架按 **一级 layer** 划分模块边界，5.2 多平台架构引入了 **平台子模块** 作为合法的第二级导入层。

## 一级 Layer 列表

`adapter` / `api` / `app` / `cli` / `core` / `event` / `plugin` / `service` / `testing` / `types` / `utils`

## 规则 1：跨 layer — 绝对导入，最多到二级（平台子模块）

不同 layer 之间互相引用时，使用 `from ncatbot.<layer> import ...`。当需要平台特定类型时，允许到 **平台子模块**（`<layer>.<platform>`），但 **禁止** 更深层导入。

```python
# ✅ 一级导入 — 平台无关
from ncatbot.core import registrar, AsyncEventDispatcher
from ncatbot.utils import get_log
from ncatbot.types import MessageArray, PlainText, At, Image

# ✅ 二级导入 — 平台特定（event / types / api 的平台子模块）
from ncatbot.event.qq import GroupMessageEvent, PrivateMessageEvent
from ncatbot.types.qq import ForwardConstructor, Face
from ncatbot.api.qq import QQAPIClient

# ❌ 三级及更深 — 禁止
from ncatbot.core.registry import registrar
from ncatbot.types.common.segment import PlainText
from ncatbot.event.qq.message import GroupMessageEvent
```

**允许二级导入的平台子模块**（白名单）：

`event`、`types`、`api`、`adapter` 四个 layer 允许 `ncatbot.<layer>.<platform>` 形式的二级导入，`<platform>` 为该 layer 下任意已存在的平台子包（有 `__init__.py`）。当前平台子模块：

| Layer | 允许的二级子模块 | 典型导入 |
|-------|-----------------|---------|
| `event` | `event.qq`, `event.bilibili`, `event.github`, `event.common` | `GroupMessageEvent`, `BiliLiveEvent` |
| `types` | `types.qq`, `types.bilibili`, `types.github`, `types.common`, `types.napcat` | `ForwardConstructor`, `GitHubSender` |
| `api` | `api.qq`, `api.bilibili`, `api.github`, `api.ai`, `api.traits` | `QQAPIClient`, `IMessaging` |
| `adapter` | `adapter.napcat`, `adapter.mock`, `adapter.bilibili`, `adapter.github`, `adapter.ai` | `NapCatAdapter`, `MockAdapter` |

其他 layer（`core`、`plugin`、`utils`、`app`、`service`、`cli`、`testing`）**只允许一级导入**。

## 规则 2：同 layer 内部 — 相对导入

同一个一级 layer 内部的模块互相引用时，**必须**使用相对导入。

```python
# ✅ 正确（在 utils/config/manager.py 中）
from ..logger import get_early_logger
from .models import Config

# ❌ 错误 — 同 layer 内用了绝对导入
from ncatbot.utils.logger import get_early_logger
```

## 规则 3：外部示例 — 遵循规则 1

`docs/docs/examples/`、`.agents/skills/` 中的代码与用户代码一样，遵循规则 1：一级 + 白名单内的二级。

```python
# ✅ 用户代码 / 示例代码
from ncatbot.core import registrar, from_event, Hook
from ncatbot.plugin import NcatBotPlugin
from ncatbot.event.qq import GroupMessageEvent, FriendRequestEvent
from ncatbot.types import MessageArray, PlainText, At
from ncatbot.types.qq import ForwardConstructor
from ncatbot.utils import get_log

# ❌ 三级导入 — 禁止
from ncatbot.core.registry import registrar
from ncatbot.event.qq.message import GroupMessageEvent
```

## 规则 4：TYPE_CHECKING 守护

框架内部在类型注解中引用其他 layer 的类时，使用 `TYPE_CHECKING` 块避免循环导入：

```python
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ncatbot.core import AsyncEventDispatcher, Event
    from ncatbot.types.qq import EventType
```

## 唯一例外

访问 layer 内部的 **私有实现** (以 `_` 开头)，优先通过 `__init__.py` 向上层 re-export 再以一级导入使用：

```python
# 推荐：私有符号已通过 __init__.py 向上重导出
from ncatbot.core import _current_plugin_ctx
```

若 re-export 不可行，允许框架内部代码使用深层绝对导入。

## CLI 运行时例外

`cli/` 层作为编排入口，需延迟加载重量级 adapter 子模块以保持启动速度。以下 **函数体内** 的运行时导入为合法例外，不受深度限制：

- `cli/commands/napcat.py` — `adapter.napcat.debug.*`、`adapter.napcat.setup.*`
- `cli/commands/adapter.py` — `adapter.bilibili.auth.*`

```python
# ✅ 允许：CLI 函数体内的运行时延迟导入
def diagnose():
    from ncatbot.adapter.napcat.debug.diagnose import diagnose as run_diagnose
    ...
```

## 新增导出检查

新增公共 API 导出时，必须在对应 layer 的 `__init__.py` 中注册。如果 `__init__.py` 中没有某个符号，说明它不是公共 API。

## 一键检查

```bash
# 架构规范检查（Rule 1 跨层深度 + Rule 2 同层相对导入）
python .agents/scripts/check_imports.py          # 报告所有违规
python .agents/scripts/check_imports.py --stat   # 仅汇总统计
python dev/check_imports.py                      # 等价（thin wrapper）

# 运行时导入检查
python .agents/scripts/check_runtime_imports.py            # 汇总 + 逐文件明细
python .agents/scripts/check_runtime_imports.py --stat     # 仅汇总
python .agents/scripts/check_runtime_imports.py --strict   # 汇总 + 明细；有违规则 exit 1
```

两个脚本目标均为 **0 违规**。

---

## 规则 5：运行时导入（非顶层导入）

所有导入 **默认放在模块顶层**。仅以下场景允许在函数体 / if-guard 内延迟导入：

### 5.1 允许的延迟导入（仅提示，不算违规）

| 分类 | 标签 | 说明 | 示例 |
|------|------|------|------|
| **可选第三方依赖** | `optional_dep` | 用户可能未安装的第三方包，缺失时不应阻止框架启动 | `bilibili_api`、`litellm`、`schedule` |
| **CLI 懒加载** | `cli_lazy` | `cli/` 层 Click 命令函数体内的导入，延迟加载以保持 CLI 启动速度 | 见规则「CLI 运行时例外」 |
| **同层循环防护** | `same_module_lazy` | 同一 layer 内存在真实循环依赖，已确认无法移至顶层 | `types/common/segment/media.py` ↔ `attachment.py` |
| **平台条件导入** | `platform_guard` | `if sys.platform == "win32":` 等平台特定模块 | `ctypes`、`winreg` |

### 5.2 禁止的延迟导入（必须修复）

| 分类 | 标签 | 修复方式 |
|------|------|---------|
| **跨层延迟导入** | `cycle_breaker` | 移至模块顶层。实际不存在循环的跨层导入不应延迟 |
| **标准库延迟导入** | `stdlib_lazy` | 移至模块顶层。标准库永远可用，无需延迟 |
| **其他延迟导入** | `other` | 逐案分析后移至顶层或归入上述允许类别 |
| **try/except 导入** | `try_except` | Python 3.12+ 无需兼容性 try/except |
| **非平台 runtime if** | `runtime_if` | 逐案分析，通常应移至顶层 |

### 5.3 判定流程：新增导入该放哪？

```
新增一个 import
  ├─ 跨 layer？ → 顶层绝对导入（Rule 1）
  ├─ 同 layer？ → 顶层相对导入（Rule 2）
  ├─ 第三方可选包？ → 函数体内 ✅（optional_dep）
  ├─ cli/ 命令函数？ → 函数体内 ✅（cli_lazy）
  ├─ 平台条件？ → if sys.platform guard ✅（platform_guard）
  ├─ 移到顶层会触发循环？
  │     ├─ 同层内部：确认循环后保留 ✅（same_module_lazy）
  │     └─ 跨层：重构消除循环（通常通过 __init__.py re-export 或拆分模块）
  └─ 以上都不是 → 顶层导入
```

### 5.4 已确认的循环依赖（不可移动）

以下延迟导入已确认存在真实循环，保留在函数体内：

| 文件 | 导入 | 循环路径 |
|------|------|---------|
| `types/common/segment/media.py` | `from ..attachment import Attachment` 等 5 处 | `attachment.py` 顶层导入 `media.py` 中的段类型 |
| `types/common/segment/array.py` | `from ..attachment_list import AttachmentList` | `attachment_list` → `attachment` → `segment` |
| `plugin/builtin/system_manager/main.py` | `from .. import BUILTIN_PLUGINS` | `builtin/__init__` → `system_manager` → `BUILTIN_PLUGINS` |
| `plugin/loader/core.py` | `from ...plugin.builtin import BUILTIN_PLUGINS` | `plugin/__init__` → `loader` → `builtin` |

### 5.5 可选第三方依赖白名单

在 `check_runtime_imports.py` 的 `OPTIONAL_DEPS` 中维护：

```python
OPTIONAL_DEPS = {"bilibili_api", "litellm", "schedule"}
```

新增可选依赖时同步更新此白名单。
