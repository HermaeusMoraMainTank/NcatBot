# NapCat Setup 模块

NapCat 启动编排模块，负责 NapCat 的安装、配置、启动和登录。

## 模块结构

| 文件 | 职责 |
|---|---|
| `launcher.py` | 启动编排入口，协调其他模块 |
| `platform.py` | 平台抽象层（Windows/Linux 的启动、停止） |
| `installer.py` | NapCat 安装/更新 |
| `config.py` | NapCat 配置文件管理（OneBot11、WebUI） |
| `auth.py` | WebUI 认证 + QQ 登录（快速登录/二维码） |

## 状态模型

登录状态**仅通过 WebSocket 判定**：

| WS 连通？ | 含义 | 后续动作 |
|---|---|---|
| 连通，返回 `self_id` | QQ 已登录 | 校验 `self_id == bot_uin` |
| 不通 | 未登录 | 通过 WebUI 引导登录 |

WebUI API（`CheckLoginStatus`、`GetQQLoginInfo`）**不参与登录状态判定**，
因为 NapCat 通过 session 缓存自动登录时 WebUI 状态可能不同步。
WebUI 仅在需要主动登录时使用（快速登录 / 扫码）。

## 两种模式

通过配置项 `napcat.skip_setup` 控制（默认 `false`）。

### Connect 模式 (`skip_setup: true`)

直接连接已有的 NapCat WebSocket 服务，**不做任何环境准备**。
适用场景：NapCat 在远端服务器 / Docker / 手动管理。

```
launch()
└─ _connect_only()
    ├─ is_service_ok() → 成功 → DONE
    └─ 失败 → ERROR
```

### Setup 模式 (`skip_setup: false`, 默认)

保证 NapCat 完整运行环境，按需执行安装/配置/启动/登录。

```
launch()
└─ _setup_and_connect()
    │
    ├─ 1. is_service_ok() → WS 在线?
    │   ├─ YES → _verify_account() → DONE
    │   └─ NO  → 继续 ↓
    │
    ├─ 2. check_platform() → 不支持? → ERROR
    ├─ 3. ensure_installed() → 安装/更新 NapCat
    ├─ 4. configure_all() → 写入 OneBot11 / WebUI 配置
    ├─ 5. start_napcat(uin)
    │
    └─ 6. _wait_and_login()
         ├─ is_service_ok(5s) → 缓存登录成功? → DONE
         │
         ├─ enable_webui = false?
         │   → is_service_ok(timeout) → DONE / TIMEOUT ERROR
         │
         └─ enable_webui = true?
             → AuthHandler().login() → wait_for_service(60s) → DONE
```

### _verify_account（WS 在线时的账号校验）

通过 WS lifecycle 消息中的 `self_id` 校验当前登录的 QQ 号：

```
_verify_account()
├─ _test_websocket() → 获取 self_id
├─ self_id == bot_uin → "账号验证通过" → DONE
└─ self_id != bot_uin → ERROR: "登录账号与目标账号不匹配"
```

### _wait_and_login（NapCat 刚启动后的登录流程）

```
_wait_and_login()
├─ is_service_ok(5s) → 缓存 session 自动登录? → DONE
│
├─ enable_webui = false?
│   → is_service_ok(websocket_timeout) → DONE / TIMEOUT
│
└─ enable_webui = true?
    → AuthHandler().login()
       ├─ quick_login() → 成功? → DONE
       └─ qrcode_login()
           ├─ 获取二维码 URL → 终端打印 ASCII 二维码
           └─ 轮询 WebUI check_login_status (60s) → DONE / TIMEOUT
```

## 配置项

| 配置路径 | 说明 | 默认值 |
|---|---|---|
| `napcat.skip_setup` | 跳过环境准备，直连 | `false` |
| `napcat.enable_webui` | 启用 WebUI（登录引导） | `true` |
| `napcat.ws_uri` | WebSocket 地址 | `ws://localhost:3001` |
| `napcat.ws_token` | WebSocket Token | `""` |
| `napcat.webui_uri` | WebUI 地址 | `http://localhost:6099` |
| `napcat.webui_token` | WebUI 认证令牌 | `napcat_webui` |
| `napcat.enable_update_check` | 检查 NapCat 更新 | `false` |
| `websocket_timeout` | WS 等待超时（秒） | `15` |

## Windows 启动方式

通过 `ShellExecuteW("runas")` 弹出 UAC 对话框，以管理员权限运行 `launcher.bat`。
NapCat 在独立进程中运行，NcatBot 退出不影响 NapCat。

## 调试工具

见 `../debug/README.md`，提供 WebSocket / WebUI / 综合对比诊断脚本。
