#!/usr/bin/env python3
"""轻量客户端：把单次会话状态上报给 bridge，必要时懒启动 bridge。

设计要点（务必保持）：
- 只用 Python 标准库，用系统 python3 跑，零额外依赖、极快
- fire-and-forget：发一行就退，任何异常都静默 exit(0)，绝不阻塞终端
- bridge 进程 detached 启动，不挂在终端下；venv bootstrap 也在后台完成

用法：python3 buddyctl.py <state>
  state ∈ idle|working|confirm|done|error|end
  会话 id 从 stdin 的 hook 事件 JSON 里取 session_id
"""
import fcntl
import json
import os
import socket
import subprocess
import sys
import time

import config

BRIDGE_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_PY = os.path.join(BRIDGE_DIR, ".venv", "bin", "python")


def read_session_id():
    """从 hook 传入的 stdin JSON 取 session_id；取不到用 default。"""
    try:
        data = sys.stdin.read()
        if data:
            return json.loads(data).get("session_id") or "default"
    except Exception:
        pass
    return "default"


def send(sid, state):
    """连 bridge 的 socket 发一行 JSON。成功返回 True。"""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(config.CONNECT_TIMEOUT)
    try:
        s.connect(str(config.SOCKET_PATH))
        s.sendall((json.dumps({"session_id": sid, "state": state}) + "\n").encode())
        return True
    finally:
        s.close()


def spawn_bridge():
    """detached 后台启动 bridge：venv 缺失则先 uv sync，再起 bridge.py。

    用 spawn 锁避免多终端同时拉起；bridge 自身还有单例锁兜底。
    """
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    lock = open(config.SPAWN_LOCK_PATH, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return  # 已有别的 buddyctl 在拉起，让它来

    # 一行 shell：venv 不存在就 uv sync，然后接管为 bridge.py
    cmd = '[ -d .venv ] || uv sync >/dev/null 2>&1; exec .venv/bin/python bridge.py'
    logf = open(config.LOG_PATH, "a")
    subprocess.Popen(
        ["/bin/sh", "-c", cmd],
        cwd=BRIDGE_DIR,
        stdin=subprocess.DEVNULL,
        stdout=logf,
        stderr=logf,
        start_new_session=True,  # 脱离终端会话，关终端不影响它
        close_fds=True,
    )
    # 注意：不在此处释放 spawn 锁，进程退出时自动释放


def main():
    state = sys.argv[1] if len(sys.argv) > 1 else "idle"
    sid = read_session_id()

    if send(sid, state):
        return  # 常态：bridge 在跑，发完即退

    # 连不上 → 懒启动 bridge，再做几次快速重试把当前状态送达
    spawn_bridge()
    for _ in range(3):
        time.sleep(0.3)
        try:
            if send(sid, state):
                return
        except Exception:
            pass
    # 仍失败也无妨：下个 hook 会在 bridge 起来后补上状态


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # 绝不让客户端异常影响终端
