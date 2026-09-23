"""打包版入口 —— 既是独立可执行程序，也是 Tauri 的 sidecar。

两种用法：

    mds-server.exe                 # 起服务 + 自动开浏览器（独立使用）
    mds-server.exe --no-open       # 只起服务，窗口由 Tauri 提供（sidecar）
    mds-server.exe --port 0        # 自动挑空闲端口，并把 URL 打到 stdout

端口默认 0（自动挑）：装了两份、或本机 8756 已被占用时不会直接起不来。
选中的端口会以 `MDS_READY <url>` 的形式打到 stdout，Tauri 读这一行拿地址。
"""

from __future__ import annotations

import argparse
import ctypes
import os
import socket
import sys
import threading
import time
import webbrowser


def _free_port(host: str, preferred: int) -> int:
    """优先用指定端口；被占用就让系统分配一个。"""
    if preferred:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((host, preferred))
                return preferred
            except OSError:
                pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(handle, ctypes.byref(code))
        k32.CloseHandle(handle)
        return bool(ok) and code.value == STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _watch_parent(pid: int) -> None:
    """父进程没了就自杀。

    Tauri 壳被强杀（任务管理器 / taskkill /F）时，Rust 的 Drop 不会执行，
    sidecar 会变成孤儿继续占着端口。这个看门狗是最后一道保险。
    """
    while True:
        time.sleep(2)
        if not _pid_alive(pid):
            os._exit(0)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="mds-server",
        description="机械设计物料选型引擎 —— 本地服务（只监听 127.0.0.1）")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8756,
                   help="0 表示自动挑空闲端口；指定端口被占用时也会自动改挑")
    p.add_argument("--no-open", action="store_true",
                   help="不打开浏览器（Tauri sidecar 用这个）")
    p.add_argument("--parent-pid", type=int, default=0,
                   help="父进程 PID；它消失后本进程自动退出，避免留下孤儿服务")
    args = p.parse_args(argv)

    # 打包后没有控制台时 stdout 可能是 None，早点探明免得后面崩在 print 上
    port = _free_port(args.host, args.port)
    url = f"http://{args.host}:{port}"

    if args.parent_pid:
        threading.Thread(target=_watch_parent, args=(args.parent_pid,),
                         daemon=True).start()

    import uvicorn
    from server.main import app

    if not args.no_open:
        # 等服务真正起来再开浏览器，否则会撞上 ERR_CONNECTION_REFUSED
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    _say(f"MDS_READY {url}")
    try:
        uvicorn.run(app, host=args.host, port=port, log_level="warning",
                    access_log=False)
    except KeyboardInterrupt:
        pass
    return 0


def _say(line: str) -> None:
    """stdout 在 windowed 模式下可能是 None。"""
    try:
        if sys.stdout is not None:
            print(line, flush=True)
    except Exception:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
