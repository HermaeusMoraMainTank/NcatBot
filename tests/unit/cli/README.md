# CLI 模块测试

源码模块: `ncatbot.cli`

## 验证规范

### CLI 冒烟 (`test_cli_smoke.py`)

#### `--help`（不进入命令回调）

| 规范 ID | 说明 | 验证点 |
|---------|------|--------|
| CX-01 | 根命令帮助 | `ncatbot --help` 退出码 0 |
| CX-02 | 一级子命令帮助 | `run` / `dev` / `config` / `plugin` / `napcat` / `init` / `adapter` 的 `--help` |
| CX-03 | 嵌套子命令帮助 | `napcat diagnose --help` 退出码 0 |

#### 参数绑定（执行命令回调，mock 副作用）

| 规范 ID | 说明 | 验证点 |
|---------|------|--------|
| CX-04 | `run` 全选项 | `--debug` `--no-hot-reload` `--plugins-dir` → `BotClient` mock 与 `kwargs` |
| CX-05 | `run` 仅插件目录 | 仅 `--plugins-dir` 时 `debug`/`hot_reload` 为 `MISSING` |
| CX-06 | `dev` | `debug=True`、`hot_reload=True`、`--plugins-dir` 绑定 |
| CX-07 | 负向：旧选项名 | `--plugin-dir` 解析失败（非 0） |
| CX-08 | `config show` | 临时 `NCATBOT_CONFIG_PATH` + 最小 yaml，进入回调并输出 |
| CX-09 | `napcat install -y` | `PlatformOps` mock 已安装早退，绑定 `-y` |
| CX-10 | `napcat diagnose ws` | `--uri` / `--token` 传入 `check_ws`（mock） |
| CX-11 | `ref` 下载解压 | mock GitHub API + download_file，`--vscode`，验证 zip 解压到目标目录 |
| CX-12 | `ref --trae` 重命名 | `--trae` 解压后 `.agents` 重命名为 `.trae` |
| CX-13 | `napcat stop` (Linux) | 调用 `PlatformOps.stop_napcat()` |
| CX-14 | `napcat stop` (非 Linux) | 拒绝执行并返回非 0 |
| CX-15 | CLI 行编辑初始化 | CLI 入口初始化 readline 并修补 Click 整行 prompt，保证 `click.prompt` 可处理左右键且不发生提示词错位 |

### SnowLuma CLI (`test_snowluma_cli.py`)

| 规范 ID | 说明 | 验证点 |
|---------|------|--------|
| CX-16 | `snowluma --help` | 一级命令帮助可正常退出 |
| CX-17 | `snowluma diagnose --help` | 诊断子命令帮助可正常退出 |
| CX-18 | `snowluma diagnose ws` | `--uri` / `--token` 传入 `_check_ws()` |
| CX-19 | `snowluma stop` (Linux) | 调用 `PlatformOps.stop_snowluma()` |
| CX-20 | `snowluma install --yes` | `--yes` 绑定到 `SnowLumaInstaller.install(skip_confirm=True)` |

### init 模板插件生成 (`test_init.py`)

| 规范 ID | 说明 | 验证点 |
|---------|------|--------|
| CX-21 | 数字开头用户名 | 模拟 `getpass.getuser` 返回数字用户名，生成的 `plugin.py` 可编译、`entry_class` 为合法标识符且与类名一致 |
| CX-22 | `_to_class_name` | 始终产出合法标识符（数字开头前置 `_`），且不重复 `Plugin` 后缀 |
