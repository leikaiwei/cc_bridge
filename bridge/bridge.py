"""单例守护进程：收各会话状态上报，聚合后通过 BLE 写给 ESP32。

- 持 flock 保证全局只有一个实例
- Unix socket server 接收 buddyctl 上报的 {session_id, state}
- 聚合：取所有存活会话里优先级最高的状态
- done 衰减、error 锁存(不衰减)、僵尸会话 TTL 清理、空闲超时自退
- bleak 维持与 ESP32 的 BLE 长连接，状态变化时写 1 字节
"""
import asyncio
import fcntl
import json
import logging
import os
import sys
import time
from typing import Optional, TextIO

import config
import protocol
from bleak import BleakClient, BleakScanner


def setup_logging() -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(config.LOG_PATH),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


def acquire_lock() -> Optional[TextIO]:
    """抢单例锁。抢不到说明已有 bridge 在跑，返回 None。"""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    f = open(config.LOCK_PATH, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return None
    f.write(str(os.getpid()))
    f.flush()
    return f  # 返回句柄并保持引用，进程存活期间一直持锁


class Sessions:
    """各会话状态表与聚合逻辑。时间一律用单调时钟。"""

    def __init__(self) -> None:
        self.table: dict[str, tuple[int, float]] = {}  # session_id -> (状态码, 更新时刻)
        self.last_report = time.monotonic()

    def update(self, sid: str, state: str) -> None:
        now = time.monotonic()
        self.last_report = now
        if state == protocol.STATE_END:   # 会话结束，移除
            self.table.pop(sid, None)
            return
        code = protocol.STATE_BY_NAME.get(state)
        if code is None:
            logging.warning("未知状态: %r", state)
            return
        self.table[sid] = (code, now)

    def aggregate(self) -> int:
        """清理僵尸会话，返回当前应显示的状态码(取最高优先级)。"""
        now = time.monotonic()
        best = protocol.STATE_IDLE
        for sid in list(self.table):   # list() 因为循环内会删表
            code, ts = self.table[sid]
            if now - ts > config.SESSION_TTL:   # 超时未更新，剔除
                del self.table[sid]
                logging.info("清理僵尸会话: %s", sid)
                continue
            # done 显示数秒后衰减为空闲；error 不衰减(锁存到该会话下次上报)
            eff = code
            if code == protocol.STATE_DONE and now - ts > config.DONE_DECAY:
                eff = protocol.STATE_IDLE
            best = max(best, eff)
        return best


class BleManager:
    """维持与 ESP32 的 BLE 连接，target 变化时写入；断线自动退避重连。"""

    def __init__(self) -> None:
        self.target = protocol.STATE_IDLE
        self._written: Optional[int] = None

    async def run(self) -> None:
        backoff = 1
        while True:
            try:
                await self._serve()
                backoff = 1   # 正常断开(如对端重启)后快速重连
            except Exception as e:
                logging.warning("BLE 异常: %s", e)
                backoff = min(backoff * 2, 30)
            await asyncio.sleep(backoff)

    async def _find_device(self):
        # 先按设备名找，找不到再按 service UUID 兜底
        dev = await BleakScanner.find_device_by_name(protocol.DEVICE_NAME, timeout=10)
        if dev is None:
            dev = await BleakScanner.find_device_by_filter(
                lambda d, ad: protocol.SERVICE_UUID.lower()
                in [u.lower() for u in (ad.service_uuids or [])],
                timeout=10,
            )
        return dev

    async def _serve(self) -> None:
        dev = await self._find_device()
        if dev is None:
            raise RuntimeError("未发现 ESP32 设备")
        async with BleakClient(dev) as client:
            logging.info("BLE 已连接: %s", dev.address)
            self._written = None   # 重连后强制重发一次当前状态
            while client.is_connected:
                if self.target != self._written:
                    await client.write_gatt_char(
                        protocol.CHAR_UUID, bytes([self.target]), response=True
                    )
                    self._written = self.target
                    logging.info("写状态码 %d", self.target)
                await asyncio.sleep(0.2)


async def handle_client(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, sessions: Sessions
) -> None:
    """处理一次上报：读一行 JSON {session_id, state}。"""
    try:
        line = await reader.readline()
        if line:
            msg = json.loads(line)
            sessions.update(msg["session_id"], msg["state"])
    except Exception as e:
        logging.warning("上报解析失败: %s", e)
    finally:
        writer.close()


async def ticker(sessions: Sessions, ble: BleManager) -> None:
    """主循环：刷新聚合目标；长时间无上报则自动退出。"""
    while True:
        ble.target = sessions.aggregate()
        if time.monotonic() - sessions.last_report > config.IDLE_EXIT:
            logging.info("空闲超时，退出")
            os._exit(0)
        await asyncio.sleep(config.TICK)


async def main() -> None:
    sessions = Sessions()
    # 清掉残留 socket 文件后重新监听
    try:
        os.unlink(config.SOCKET_PATH)
    except FileNotFoundError:
        pass
    await asyncio.start_unix_server(
        lambda r, w: handle_client(r, w, sessions), path=str(config.SOCKET_PATH)
    )
    logging.info("bridge 启动，监听 %s", config.SOCKET_PATH)
    ble = BleManager()
    await asyncio.gather(ble.run(), ticker(sessions, ble))


if __name__ == "__main__":
    setup_logging()
    lock = acquire_lock()
    if lock is None:
        logging.info("已有 bridge 在运行，退出")
        sys.exit(0)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
