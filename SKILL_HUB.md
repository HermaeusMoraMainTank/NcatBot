# Skill Hub：插件能力化与通用管理后台

这套改造把「插件」变成「模型可发现、可调用、可审计的工具」，同时保留原有前缀命令匹配。
目标是把 `loop流程工作台` 里除 Docker 之外的链路，落到现有 Python 机器人上。

## 一、消息怎么走

```
QQ 消息
  ├─ 前缀 / 固定触发词 ──→ 原有 HandlerDispatcher → 插件 handler（完全不变）
  └─ @机器人 / 自由任务 ──→ FakeAi agent
                               ├─ list_skills      （只看名字和一行说明）
                               ├─ describe_skills  （展开参数 schema）
                               └─ invoke_skill     （受控执行）
```

- 前缀命令永远优先，模型不会截胡。
- 模型只先看到 4 个元工具（目录、展开、调用、结束），插件再多也不会把 system prompt 撑大。
- 每次调用都过 `SkillRegistry`：参数校验、角色/能力检查、超时、并发锁、审计。

## 二、三个新模块

| 模块 | 作用 |
| --- | --- |
| `ncatbot/skill` | Skill 描述、注册表、渐进展示、调用网关、FakeAi 桥接 |
| `ncatbot/agent` | 工具循环、运行记录、子智能体、模型接入 |
| `ncatbot/admin` | 通用后台：插件配置、技能目录、运行记录、审计、MCP |

插件在 `on_load` 之后自动扫描 `@skill` 方法；框架还会为每个有消息 handler 的已加载插件生成 `plugin.<Name>.run`。热重载会同步注销/重注册。

## 三、无 Docker 的权限模型

Docker 解决的是「不可信代码能碰什么」。这里换成四道更轻的闸门：

1. **能力声明而不是环境隔离**
   每个 skill 声明 `requires`（`network` / `fs.read` / `fs.write` / `subprocess` / `media`）和 `scopes`（`user` / `group` / `admin` / `owner`）。
   子智能体的能力集合是显式裁剪的，默认拿不到 `subprocess`、发消息、继续派活。

2. **工作区牢笼**
   所有文件 skill 只能访问 `data/sandbox/<session>-<run>/`。
   `..`、绝对路径、指向外部的符号链接都会被拒绝；还有文件数和体积配额。

3. **默认没有任意代码执行**
   不提供 shell（永远 `create_subprocess_exec`，argv 是列表），不把密钥传进子进程。
   只有 `sandbox.allow_subprocess=true` 且调用者是 `owner` 时，`sandbox.run` 才可用。
   Linux 上如果装了 `bwrap`，会自动加只读系统目录、独立 `/tmp`、断网命名空间；
   Windows 上退化为「新进程组 + 超时强杀 + 环境变量白名单」。

4. **密钥不进入模型/子进程**
   模型只看得到脱敏后的配置；子进程环境变量只保留白名单；审计与工具返回都会做密钥打码。

> 结论：不追求「虚拟化级」隔离，而是把危险动作收敛成少量显式 skill。
> 需要跑真正不可信的代码时，再把 `bwrap`（Linux）或 Docker 作为可选后端接上即可。

## 四、配置

`config.yaml` 新增：

```yaml
admin:
  enabled: true
  host: 127.0.0.1     # 非回环地址会被强制改回
  port: 8770
  allow_remote: false
  token: ""           # 留空自动生成到 data/admin/token.txt

skills:
  enabled: true
  default_timeout: 60
  auto_expose_plugins: true   # 每个已加载插件自动生成 plugin.<Name>.run
  plugin_allowlist: []        # 非空时只暴露这些插件
  plugin_denylist: ["_system_manager", "Administrator_plugin", "FakeAi", "SkillHub"]

agent:
  max_rounds: 8
  max_tool_calls: 20
  run_timeout: 180
  max_depth: 1
  max_tokens_per_run: 60000   # 单次运行 token 上限，0=不限
  keep_runs: 200

sandbox:
  root: data/sandbox
  max_files: 200
  max_bytes: 52428800
  allow_subprocess: false
  backend: auto       # auto / process / bwrap
  network: false

ai:
  base_url: https://api.deepseek.com
  api_key: ""         # 留空读 DEEPSEEK_API_KEY；SkillHub 会用 common 的按能力 Key 覆盖
  model: deepseek-flash
```

FakeAi 新增开关：

```yaml
skills_enabled: true      # 是否把 skill 目录接进蓝晴的 agent 工具循环
agent_max_tokens: 60000   # 单轮对话的工具循环 token 上限
```

SkillHub 新增开关（插件自身 config.yaml / 后台里编辑）：

```yaml
exposed_commands: []   # 允许模型代发的前缀命令，例：["今日老婆", "塔塔露"]
```

## 五、后台怎么用

启动后日志会打印：

```
管理后台已启动：http://127.0.0.1:8770/?token=xxxx
```

- 插件：查看所有插件、编辑配置（密钥字段显示 `***`，不会回写）、一键重载。
- 技能：搜索/筛选、展开 schema、在线试调用、启停。
- Agent：手动跑一个任务，查看每一步工具调用与完整对话。
- 审计：技能调用、后台操作、耗时与错误。
- `POST /mcp`：MCP JSON-RPC（`initialize` / `tools/list` / `tools/call`），给外部 MCP 客户端用。

## 六、写一个 skill

```python
from ncatbot.plugin import NcatBotPlugin
from ncatbot.skill import SkillContext, SkillResult, skill


class MyPlugin(NcatBotPlugin):
    name = "MyPlugin"
    version = "1.0.0"

    @skill(
        "weather.now",
        summary="查某个城市当前天气",
        params=[{"name": "city", "type": "string", "required": True}],
        tags=["web"],
        side_effects="external",
        requires=("network",),
        timeout=30,
    )
    async def weather_now(self, ctx: SkillContext, city: str) -> SkillResult:
        ...
        return SkillResult.text(f"{city}：晴 25℃")
```

`summary` 进目录；`description` 和 `parameters` 只在 `describe_skills` 时展开。

## 七、已经接进来的能力

`plugins/SkillHub` 目前提供：

- `time.now`
- `file.write_text` / `file.read_text` / `file.list` / `file.delete`
- `web.search` / `web.fetch`
- `message.send` / `message.send_image` / `message.send_file`
- `tts.speak`（MiMo TTS；Key 不可用时自动回退 Windows SAPI / espeak / macOS say）
- `image.text`（Pillow 文字图）
- `image.package.list` / `image.package.send`（ImageSender 图库）
- `agent.spawn`（受限子智能体）
- `plugin.list` / `plugin.commands.list`（只读发现现有插件与前缀命令）
- `plugin.command.run`（只有 `exposed_commands` 里显式开放的命令，模型才能代发；默认空）
- `sandbox.status` / `sandbox.run`（默认关闭）

除此之外，框架会自动为**每个有消息 handler 的已加载插件**注册一个通用入口：

```
plugin.<插件名>.run(text="要发的消息", at_bot=false)
```

它会构造一条合成消息，只分发给该插件的 handler，由插件按自己的触发词、权限和冷却处理。
触发词从 `CommandHook` / `StartsWithHook` / `KeywordHook`、`commands` 图库、
`HELP_TEXT`、`COMMAND_*` 常量里自动提取；提取不到时才保守扫描源码。
`FakeAi`、`SkillHub`、内置 `_system_manager` 默认在 denylist 里，避免递归和管理命令被代发。

这些自动入口在后台的“技能”页会显示为 `plugin.<插件名>.run`，可以单独启停。

示例链路：

> @机器人 先发语音说「哈哈，我是咕咕白」，然后生成一张写着 2236 的图，最后让子智能体写个 txt 发群里。

模型会依次：`tts.speak` → `image.text` → `message.send_image` → `agent.spawn` → `message.send_file`。

## 八、边界

- 每个有消息 handler 的插件默认自动获得 `plugin.<Name>.run`；只有后台定时/服务、没有消息入口的插件没有可调用面。
- `FakeAi`、`SkillHub`、内置 `_system_manager` 默认在 denylist，避免递归和管理命令被代发；可以用 `plugin_allowlist` 收窄。
- 逐条前缀命令仍可用 `exposed_commands` 精确开放，且只会以当前说话人的身份、在当前群执行。
- 子智能体是进程内受限 agent，不是独立进程；真正需要跑不可信代码时再启用 `sandbox.run` / bwrap / Docker。
- 图库、图片生成依赖原有插件；TTS 优先 MiMo，失败会自动回退本机语音（Windows SAPI / espeak / macOS say），本机也没有语音后端时才明确失败。
- 浏览器渲染、ComfyUI 绘图、按会话代理令牌尚未接入；现有 `web.search/fetch` 已覆盖大部分联网需求。
