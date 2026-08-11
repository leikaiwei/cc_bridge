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
    """各会话状态表与聚合逻辑。内存里时间一律用单调时钟, 落盘时换算成挂钟时间。

    背景: bridge 每次升级都要重启进程, 而 monotonic 时钟在重启后归零、
    不可跨进程比较。之前表是纯内存的, 重启即清空——哪怕某个会话正等着用户
    在 confirm(需确认)上做选择, 灯也会瞬间被清空重新聚合, 状态凭空丢失。
    落盘用挂钟时间(time.time())做媒介, 读回时按"当前挂钟时间相对存盘时刻
    过去了多久"换算回本进程的单调时钟坐标, 这样 aggregate() 的衰减判断
    (WORKING_DECAY/SESSION_TTL 等)跨重启也是连续的, 不会被重置。
    """

    def __init__(self) -> None:
        self.table: dict[str, tuple[int, float]] = {}  # session_id -> (状态码, 更新时刻/单调)
        self.last_report = time.monotonic()
        self._load()

    def _load(self) -> None:
        """启动时从磁盘恢复会话表, 换算成本进程的单调时钟坐标。"""
        try:
            raw = json.loads(config.STATE_PATH.read_text())
        except (OSError, ValueError):
            return  # 文件不存在/损坏都视为空表, 不阻塞启动
        if not isinstance(raw, dict):
            return
        now_mono, now_wall = time.monotonic(), time.time()
        for sid, entry in raw.items():
            try:
                code, wall_ts = entry
                code, wall_ts = int(code), float(wall_ts)
            except (TypeError, ValueError):
                continue  # 单条记录格式错误跳过, 不因一条坏数据丢全部
            age = now_wall - wall_ts          # 存盘后经过的挂钟时长
            if age > config.SESSION_TTL:      # 重启期间已经僵尸太久, 不必恢复
                continue
            self.table[sid] = (code, now_mono - age)

    def _save(self) -> None:
        """把当前表换算成挂钟时间写盘。原子写, 避免半截文件。"""
        now_mono, now_wall = time.monotonic(), time.time()
        raw = {sid: (code, now_wall - (now_mono - ts)) for sid, (code, ts) in self.table.items()}
        tmp = config.STATE_PATH.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(raw))
            tmp.replace(config.STATE_PATH)
        except OSError as e:
            logging.warning("会话表落盘失败: %s", e)

    def update(self, sid: str, state: str) -> None:
        now = time.monotonic()
        self.last_report = now
        if state == protocol.STATE_END:   # 会话结束，移除
            self.table.pop(sid, None)
            self._save()
            return
        code = protocol.STATE_BY_NAME.get(state)
        if code is None:
            logging.warning("未知状态: %r", state)
            return
        self.table[sid] = (code, now)
        self._save()

    def aggregate(self) -> int:
        """清理僵尸会话，返回当前应显示的状态码(取最高优先级)。"""
        now = time.monotonic()
        best = protocol.STATE_IDLE
        expired = False
        for sid in list(self.table):   # list() 因为循环内会删表
            code, ts = self.table[sid]
            if now - ts > config.SESSION_TTL:   # 超时未更新，剔除
                del self.table[sid]
                expired = True
                logging.info("清理僵尸会话: %s", sid)
                continue
            # done 显示数秒后衰减为空闲；error 不衰减(锁存到该会话下次上报)
            eff = code
            if code == protocol.STATE_DONE and now - ts > config.DONE_DECAY:
                eff = protocol.STATE_IDLE
            # working 久不续报 → 当作已收尾, 降为 done。
            # 覆盖 Esc 打断 / subagent 结束 / 终端被强杀 / async hook 乱序这几种
            # 拿不到收尾事件的情况, 避免灯一直卡在橙色直到 SESSION_TTL。
            elif code == protocol.STATE_WORKING and now - ts > config.WORKING_DECAY:
                eff = protocol.STATE_DONE
            best = max(best, eff)
        if expired:
            self._save()   # 清理结果也落盘, 否则僵尸重启后会复活
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
                # 蓝牙未授权：提示去系统设置勾选 CC Bridge（理论上经 .app 启动会自动弹窗授权）
                if "not authorized" in str(e).lower() or "unauthorized" in str(e).lower():
                    logging.error(
                        "蓝牙未授权。请到「系统设置 → 隐私与安全性 → 蓝牙」勾选 CC Bridge 后重试"
                    )
                else:
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
