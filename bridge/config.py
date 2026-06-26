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

# 时长（秒）
DONE_DECAY = 5          # done 状态多久衰减为 idle
SESSION_TTL = 30 * 60   # 僵尸会话超时清理（终端被强杀时兜底）
IDLE_EXIT = 60 * 60     # 聚合表空后多久自动退出
TICK = 1.0              # 守护进程主循环节拍

# buddyctl 连接守护进程的超时
CONNECT_TIMEOUT = 0.5
