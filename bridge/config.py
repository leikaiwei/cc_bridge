"""运行时路径与可调参数。

数据目录用固定用户级路径，保证所有项目/会话共享同一守护进程与 BLE 连接。
"""
import os
from pathlib import Path

# 运行时数据目录（socket / 锁 / 日志）
DATA_DIR = Path(os.path.expanduser("~/.cc-bridge"))
SOCKET_PATH = DATA_DIR / "bridge.sock"
LOCK_PATH = DATA_DIR / "bridge.lock"      # bridge 单例锁，守护进程整生命周期持有
SPAWN_LOCK_PATH = DATA_DIR / "spawn.lock"  # buddyctl 启动锁，避免并发重复拉起
LOG_PATH = DATA_DIR / "bridge.log"
# 承载 bridge 的 .app（声明蓝牙用途）：经它启动才能拿到 BLE 的 TCC 授权，详见 macapp.py
APP_PATH = DATA_DIR / "CCBridge.app"

STATE_PATH = DATA_DIR / "sessions.json"   # 会话表落盘, 供 bridge 重启后恢复(见 bridge.py)

# 时长（秒）
DONE_DECAY = 30 * 60    # done 状态多久衰减为 idle（与 SESSION_TTL 相等，到点一并清理）
SESSION_TTL = 30 * 60   # 僵尸会话超时清理（终端被强杀时兜底）
# working 多久没续报就当它已经不在干活了，衰减为 done。
# 每次上报(UserPromptSubmit/PostToolUse/SubagentStop)都会刷新这个时刻，
# 所以阈值只需盖住"两次上报之间的最大间隔"，也就是单个工具调用本身能跑多久——
# 不是这一轮总耗时(工具接连调用时, 间隔本来就很短)。
# 取 10 分钟：盖住绝大多数工具(WebFetch、普通 Agent 调用), 又远小于原来要等的
# 30 分钟 SESSION_TTL。仍有残留风险：单个工具本身跑得比 10 分钟还久时
# (比如超长测试套件、超慢后台 Agent), 执行期间没有任何 hook 触发, 会被误判
# 直到它完成补报——这是 hook 只在完成时触发、执行中无心跳的本质限制, 调阈值只能
# 缩小误判面, 不能消除。
WORKING_DECAY = 10 * 60
IDLE_EXIT = 60 * 60     # 聚合表空后多久自动退出
TICK = 1.0              # 守护进程主循环节拍
SCAN_TIMEOUT = 5.0      # BLE 每轮扫描时长
SCAN_INTERVAL = 5.0     # 两轮扫描间隔（新设备加入 / 断线重连最多等约一个周期）

# buddyctl 连接守护进程的超时
CONNECT_TIMEOUT = 0.5
