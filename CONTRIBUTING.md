# 贡献指南

本文件面向**框架核心开发者**——修改 `ncatbot/` 源码、编写测试、维护文档与 Agent Skills。

## 贡献步骤

1. Fork 并 clone 本仓库
2. 准备开发环境
3. 新建分支开发
4. 测试通过
5. 提交并开 Pull Request

## 环境准备

```bash
git clone --recurse-submodules https://github.com/<你的用户名>/ncatbot.git
cd ncatbot
```

> `--recurse-submodules` 是必须的，`docs/` 是 Git submodule，测试和文档构建依赖它。
> 若已 clone 但未拉取子模块，补执行 `git submodule update --init`。

推荐使用 [uv](https://docs.astral.sh/uv/) 管理环境：

```bash
uv sync --extra dev                  # 安装依赖
uv run pre-commit install            # 启用 pre-commit（仅需一次）
```

激活虚拟环境：

```bash
source .venv/bin/activate            # Linux/macOS
.venv\Scripts\activate.ps1           # Windows PowerShell
```

> pre-commit 会在 `git commit` 时自动检查格式。若自动修复了文件，请 `git add` 重新暂存后再提交。

## AI Agent 辅助开发

如果你使用 VS Code + GitHub Copilot，工作区内置的 **领域技能（Skills）** 可极大提升效率：

| 场景 | Agent 技能 |
|------|-----------|
| 定位代码、追踪调用链 | `codebase-nav` / `code-nav` |
| 修 Bug、写新功能、重构 | `framework-dev` |
| 写测试、调试测试 | `testing-framework` / `testing-design` |
| 写文档、修文档 | `docs-maintenance` |

直接在 Copilot Chat 中用自然语言描述需求，Agent 会自动加载对应技能。

## 开发

新建分支：

```bash
git checkout -b feat/描述性短名       # feat/ fix/ refactor/ docs/
```

### 四位一体原则

每次变更应同步考虑这四项，保持项目一致性：

| 产物 | 位置 |
|------|------|
| **Code** | `ncatbot/` |
| **Test** | `tests/` |
| **Docs** | `docs/docs/notes/` |
| **Skill** | `.agents/skills/` |

> 并非每个 PR 都必须四项齐全——小修复可能只涉及 Code + Test。但请在提交前有意识地检查是否需要同步其他产物。

### 导入规范

- **跨 layer**：使用绝对导入，最多到二级平台子模块（如 `from ncatbot.event.qq import GroupMessageEvent`）
- **同 layer 内部**：使用相对导入（如 `from ..logger import get_early_logger`）

> 详见 `.agents/skills/framework-dev/references/import-conventions.md`

### Commit 格式

使用 [Conventional Commits](https://www.conventionalcommits.org/zh-hans/v1.0.0/)：

```bash
git commit -m "feat(plugin): 新增热重载事件通知"
git commit -m "fix(api): 修复消息队列溢出问题 #123"
git commit -m "docs: 更新适配器指南"
```

## 测试

```bash
uv run pytest                                           # 全部测试
uv run pytest --cov=ncatbot --cov-report=term-missing    # 带覆盖率
uv run pytest tests/unit/ -v                             # 仅单元测试
```

本项目采用**规范驱动测试**——每个测试函数的 docstring 以 spec-id 开头（如 `R-10`、`PL-01`），测试分为 unit / integration / e2e 三层。

> 详见 [tests/README.md](tests/README.md) 了解完整的 spec-id 索引与测试规范。

### 多版本测试（可选）

```bash
uv run tox                # Python 3.12 + 3.13
uv run tox -e py312       # 仅 3.12
```

> CI 会自动在多版本上运行，本地非必需。

## 项目结构

```
ncatbot/
├── adapter/     # 平台适配器（NapCat、Bilibili、GitHub、Mock）
├── api/         # Bot API 客户端
├── app/         # 应用入口（BotClient）
├── cli/         # 命令行工具
├── core/        # 核心分发器、注册器、Hook 系统
├── event/       # 事件定义
├── plugin/      # 插件系统（加载器、Mixin）
├── service/     # 框架服务（RBAC、定时任务、文件监听）
├── testing/     # 测试框架（TestHarness、PluginTestHarness）
├── types/       # 消息类型、数据模型
└── utils/       # 工具模块（配置、日志）
```

## 依赖管理

- **pyproject.toml**：定义顶层依赖
- **uv.lock**：锁定版本（已纳入版本控制）

```bash
uv lock                    # 修改 pyproject.toml 后重新锁定
uv lock --upgrade          # 升级所有依赖
```

将 `pyproject.toml` 和 `uv.lock` 一起提交。

## 提交 PR

```bash
git push origin feat/描述性短名
```

在 GitHub 上向上游仓库创建 Pull Request，说明变更内容并关联 Issue（如有）。

## 代码规范

- 遵循 PEP8，**尽量添加类型注解**
- 必要时添加清晰的 docstring：

```python
def handle_event(event: Event):
    """处理机器人事件。

    Args:
        event: 继承自 BaseEvent 的事件对象。
    """
```
