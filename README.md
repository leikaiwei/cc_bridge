# Claude Code Bridge Kelvin

把 Claude Code 的会话状态通过 BLE 实时同步到 ESP32 的 RGB 灯。
开多个会话时，灯显示所有会话里**最该关注**的那个状态。

## 状态与灯效

| 状态 | 触发时机 | 灯效 | 优先级 |
|------|----------|------|:---:|
| 空闲 idle | 会话开始 / 完成 30 分钟后 | 暖白常亮 | 0 |
| 完成 done | 一轮回复结束 | 绿色常亮（约 30 分钟后转空闲） | 1 |
| 工作中 working | 提交 prompt | Claude 橙色呼吸 | 2 |
| 需确认 confirm | Claude 弹窗等你授权 / MCP 交互 | 黄色闪烁 | 3 |
| 报错 error | 一轮因错误结束 | 红色慢脉冲（锁存到下次工作） | 4 |

多会话聚合：灯始终显示所有存活会话里优先级最高的状态。

## 工作原理（简述）

- **buddyctl.py**：每个 hook 触发时跑一下，纯标准库、瞬起瞬灭，把 `{会话id, 状态}` 发给后台守护进程。
- **bridge.py**：后台单例守护进程（脱离终端，靠文件锁保证全局唯一），聚合各会话状态，通过 BLE 长连接写给范围内所有同名 ESP32。
- **懒启动**：第一个会话的 hook 会自动构建并经 `CC Bridge.app` 拉起守护进程（详见下文「macOS 蓝牙授权」），无需手动开服务。
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

macOS 下裸 Python 进程访问蓝牙会被系统隐私机制（TCC）直接终止且**不弹授权窗**，所以守护进程不直接跑 Python，而是装进一个声明了蓝牙用途、经签名的最小 `CC Bridge.app`（首次启动时自动构建于 `~/.cc-bridge/CCBridge.app`），由它经 LaunchServices 启动 bridge。

- **首次**运行时，macOS 会弹窗请求授予 **CC Bridge** 蓝牙权限，点「允许」即可，授权后长期保留。
- 授权归属到 CC Bridge.app，与你用哪个终端（Warp / iTerm / Terminal）无关。
- 若灯不亮，到「系统设置 → 隐私与安全性 → 蓝牙」确认 **CC Bridge** 已勾选；`~/.cc-bridge/bridge.log` 出现「蓝牙未授权」即说明被拒。
- 构建 `.app` 需 Xcode Command Line Tools（`xcode-select --install`，提供 `clang`/`codesign`）。

## ESP32 固件

固件源码独立维护（NimBLE GATT server + WS2812 RGB）。BLE 设备名与 UUID 写死在 `bridge/protocol.py`，两端必须一致。支持多块同名设备同时同步（比如舵机板 + 圆屏板），bridge 会连上范围内所有匹配设备、把状态写给每一块，后上电的板子约 10 秒内自动加入。烧录：

```bash
~/.platformio/penv/bin/pio run -t upload
```

## 手动排查

- venv 没建好时手动建：`cd bridge && uv sync`
- 运行时数据与日志在 `~/.cc-bridge/`（`bridge.log` 可看连接与状态写入记录）
- 守护进程崩了无需手动处理，下一次任意会话的 hook 会自动重新拉起
