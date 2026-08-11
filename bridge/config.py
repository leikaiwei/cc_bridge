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

# 时长（秒）
DONE_DECAY = 30 * 60    # done 状态多久衰减为 idle（与 SESSION_TTL 相等，到点一并清理）
SESSION_TTL = 30 * 60   # 僵尸会话超时清理（终端被强杀时兜底）
# working 多久没续报就当它已经不在干活了，衰减为 done。
# 干活时 PostToolUse 会持续续报，正常不会触发；只有这些"没有收尾事件"的情况才会：
# Esc 打断（Stop 不触发）、subagent 结束（只发 SubagentStop）、终端被强杀、async hook 乱序。
# 取 90 秒：比单次工具调用的常见间隔宽裕，又远小于原来要等的 30 分钟 SESSION_TTL。
WORKING_DECAY = 90
IDLE_EXIT = 60 * 60     # 聚合表空后多久自动退出
TICK = 1.0              # 守护进程主循环节拍

# buddyctl 连接守护进程的超时
CONNECT_TIMEOUT = 0.5
