#!/usr/bin/env python3
"""构建带蓝牙用途声明的最小 .app，让 bridge 经它启动以拿到 BLE 的 TCC 授权归属。

为什么必须这么做（已实测验证）：
- macOS 下裸 python 进程没有 Info.plist，初始化 CoreBluetooth 时会被 TCC 直接
  SIGABRT 杀掉、且**完全不弹授权窗**（崩溃报告 namespace=TCC，要求
  NSBluetoothAlwaysUsageDescription）。Python 的 try/except 抓不住信号，故只能换壳。
- 把 bridge 包进一个声明了用途、已签名的 .app，并用 `open` 经 LaunchServices 拉起，
  macOS 才会把蓝牙权限归属到该 .app，首次正常弹窗、授权后持久保留，与终端彻底解耦。

关键设计（踩坑后固定下来）：
- .app 主程序必须是 Mach-O 二进制：脚本作主程序会被 LaunchServices 拒绝（-10669），
  故用 clang 编译一个极小 launcher。
- launcher 路径无关：python 路径与 bridge 目录在运行时由 `open --args` 传入，
  保证 .app 内容跨机/跨更新字节一致 → 签名 cdhash 稳定 → 蓝牙授权长期不丢。
- launcher fork 出 python 子进程跑 bridge.py，自身 waitpid 保活，始终当 TCC 责任进程。

幂等：内容版本未变且 .app 已存在则跳过重建，避免无谓重签。
"""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile

import config

# 改动 launcher 源码 / plist 时同步 +1，触发重建
BUILD_VERSION = 1

BUNDLE_ID = "com.kelvin.cc-bridge"
EXEC_NAME = "ccbridge"

# 极小 launcher：argv[1]=python 绝对路径 argv[2]=bridge 目录。
# fork 出 python 跑 bridge.py，父进程(=bundle 主程序)保活当 TCC 责任进程。
LAUNCHER_C = r"""
#include <unistd.h>
#include <sys/wait.h>
int main(int argc, char** argv) {
    if (argc < 3) return 2;            /* 需 python 路径与 bridge 目录 */
    pid_t pid = fork();
    if (pid < 0) return 1;
    if (pid == 0) {
        if (chdir(argv[2]) != 0) _exit(126);
        execl(argv[1], argv[1], "bridge.py", (char*)0);
        _exit(127);                    /* exec 失败 */
    }
    int st;
    waitpid(pid, &st, 0);              /* 保活：bundle 全程在场，作蓝牙 TCC 责任进程 */
    return 0;
}
"""

INFO_PLIST = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleIdentifier</key><string>{BUNDLE_ID}</string>
  <key>CFBundleName</key><string>CC Bridge</string>
  <key>CFBundleExecutable</key><string>{EXEC_NAME}</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSUIElement</key><true/>
  <key>NSBluetoothAlwaysUsageDescription</key>
  <string>CC Bridge 通过蓝牙把 Claude Code 会话状态同步到 RGB 状态灯。</string>
</dict>
</plist>
"""

VERSION_FILE = config.DATA_DIR / ".ccbridge-app-version"


def _fingerprint() -> str:
    """内容指纹：版本 + 源码 + plist 变化时触发重建。"""
    h = hashlib.sha256()
    h.update(str(BUILD_VERSION).encode())
    h.update(LAUNCHER_C.encode())
    h.update(INFO_PLIST.encode())
    return h.hexdigest()


def _log(msg: str) -> None:
    """构建关键信息追加到 bridge.log，便于排查（buddyctl 会丢弃 stdout）。"""
    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(config.LOG_PATH, "a") as f:
            f.write(f"[macapp] {msg}\n")
    except Exception:
        pass


def build(force: bool = False) -> bool:
    """构建/更新 .app。已是最新则跳过。成功返回 True。"""
    app = config.APP_PATH
    fp = _fingerprint()

    # 幂等：内容未变且 .app 完好则跳过
    if not force and app.exists():
        try:
            if VERSION_FILE.read_text().strip() == fp:
                return True
        except OSError:
            pass

    clang = shutil.which("clang") or "/usr/bin/clang"
    if not os.path.exists(clang):
        _log("找不到 clang（需安装 Xcode Command Line Tools：xcode-select --install）")
        return False

    macos_dir = app / "Contents" / "MacOS"
    try:
        # 全量重建，避免残留旧签名
        if app.exists():
            shutil.rmtree(app)
        macos_dir.mkdir(parents=True, exist_ok=True)
        (app / "Contents" / "Info.plist").write_text(INFO_PLIST)

        # 编译 launcher 到 bundle 主程序
        with tempfile.NamedTemporaryFile("w", suffix=".c", delete=False) as cf:
            cf.write(LAUNCHER_C)
            csrc = cf.name
        try:
            subprocess.run(
                [clang, "-O2", "-o", str(macos_dir / EXEC_NAME), csrc],
                check=True, capture_output=True,
            )
        finally:
            os.unlink(csrc)

        # ad-hoc 签名（TCC 要求 bundle 有签名才认其用途声明）
        subprocess.run(
            ["codesign", "--force", "--sign", "-", str(app)],
            check=True, capture_output=True,
        )

        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        VERSION_FILE.write_text(fp)
        _log(f"已构建 .app: {app}")
        return True
    except subprocess.CalledProcessError as e:
        _log(f"构建失败: {e.stderr.decode('utf-8', 'replace')[:300]}")
        return False
    except OSError as e:
        _log(f"构建失败(IO): {e}")
        return False


if __name__ == "__main__":
    ok = build(force="--force" in sys.argv[1:])
    sys.exit(0 if ok else 1)
