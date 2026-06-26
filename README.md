# Claude Code Bridge Kelvin

把 Claude Code 的会话状态通过 BLE 实时同步到 ESP32 的 RGB 灯。
开多个会话时，灯显示所有会话里**最该关注**的那个状态。

## 状态与灯效

| 状态 | 触发时机 | 灯效 | 优先级 |
|------|----------|------|:---:|
| 空闲 idle | 会话开始 / 完成数秒后 | 暗白常亮 | 0 |
| 完成 done | 一轮回复结束 | 绿色常亮（约 5s 后转空闲） | 1 |
| 工作中 working | 提交 prompt | 蓝色呼吸 | 2 |
| 需确认 confirm | Claude 发通知（等你授权/输入） | 琥珀闪烁 | 3 |
| 报错 error | 一轮因错误结束 | 红色慢脉冲（锁存到下次工作） | 4 |

多会话聚合：灯始终显示所有存活会话里优先级最高的状态。

## 工作原理（简述）

- **buddyctl.py**：每个 hook 触发时跑一下，纯标准库、瞬起瞬灭，把 `{会话id, 状态}` 发给后台守护进程。
- **bridge.py**：后台单例守护进程（脱离终端，靠文件锁保证全局唯一），聚合各会话状态，通过 BLE 长连接写给 ESP32。
- **懒启动**：第一个会话的 hook 会自动拉起守护进程，无需手动开服务。
- **自动退出**：所有会话结束、持续 60 分钟无活动后，守护进程自动断开 BLE 并退出（时长见 `bridge/config.py` 的 `IDLE_EXIT`）。

灯效配色写死在 ESP32 固件里；主机只发 1 字节状态码。

## 安装

### 前置条件

- macOS，已装 `python3` 和 [`uv`](https://docs.astral.sh/uv/)
- ESP32 已烧录本项目固件（见下）且在蓝牙范围内
- 安装私有库需配置好 GitHub 访问凭证（SSH key 或 token）

### 通过 marketplace 安装（推荐，多机同步）

```bash
claude plugin marketplace add leikaiwei/cc_bridge
claude plugin install claude-code-bridge-kelvin@cc_bridge
```

装好后每次启动自动加载。首次开会话时会自动 `uv sync` 建好 Python 环境（venv），无需手动配置。

### 更新

```bash
claude plugin update claude-code-bridge-kelvin@cc_bridge
```

### 本地开发模式

```bash
claude --plugin-dir "/path/to/cc_bridge"
```

## macOS 蓝牙授权

首次运行守护进程时，macOS 会要求授予运行 Claude Code 的终端（Terminal / iTerm 等）**蓝牙权限**。若灯不亮，到「系统设置 → 隐私与安全性 → 蓝牙」里确认对应终端已勾选。

## ESP32 固件

固件源码独立维护（NimBLE GATT server + WS2812 RGB）。BLE 设备名与 UUID 写死在 `bridge/protocol.py`，两端必须一致。烧录：

```bash
~/.platformio/penv/bin/pio run -t upload
```

## 手动排查

- venv 没建好时手动建：`cd bridge && uv sync`
- 运行时数据与日志在 `~/.cc-bridge/`（`bridge.log` 可看连接与状态写入记录）
- 守护进程崩了无需手动处理，下一次任意会话的 hook 会自动重新拉起
