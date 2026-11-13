"""桌面图标入口。

无参数   ：若服务已在运行就直接打开浏览器，否则启动 server.py 后再打开。
--stop   ：停止本启动器创建的服务进程。

设计要点：
- 端口复用：重复双击图标不会重复起服务、不会端口冲突。
- 服务运行在一个可见的控制台窗口里，关掉窗口即可停止服务。
- 启动失败用系统弹窗 + data/launcher.log 明确告知，避免"双击没反应"。
"""
from __future__ import annotations

import ctypes
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
PID_FILE = DATA_DIR / "server.pid"
LOG_FILE = DATA_DIR / "launcher.log"
SERVER = ROOT / "server.py"


def port() -> int:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("PORT="):
                try:
                    return int(line.split("=", 1)[1].strip())
                except ValueError:
                    pass
    return 8765


def url(p: int) -> str:
    return f"http://127.0.0.1:{p}/"


def listening(p: int, timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", p), timeout=timeout):
            return True
    except OSError:
        return False


def is_our_service(p: int) -> bool:
    try:
        with urllib.request.urlopen(url(p) + "api/config", timeout=1.5) as resp:
            return b"garmin_region" in resp.read(2048)
    except Exception:
        return False


def log(message: str) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')}  {message}\n")
    except Exception:
        pass


def alert(message: str, title: str = "Garmin 健康趋势看板") -> None:
    try:
        user32 = ctypes.windll.user32
        user32.MessageBoxW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
        user32.MessageBoxW.restype = ctypes.c_int
        user32.MessageBoxW(0, message, title, 0x30)  # MB_ICONWARNING
    except Exception:
        pass


def python_exe() -> Path:
    """pythonw.exe 启动的进程没有控制台；服务本体改用 python.exe 便于显示/关闭窗口。"""
    exe = Path(sys.executable)
    if exe.name.lower().startswith("pythonw"):
        candidate = exe.with_name("python.exe")
        if candidate.exists():
            return candidate
    return exe


def start() -> None:
    p = port()
    if listening(p):
        if is_our_service(p):
            log(f"reuse existing server on port {p}")
            webbrowser.open(url(p))
        else:
            alert(f"端口 {p} 已被其他程序占用，无法启动看板。\n可在 .env 里修改 PORT 后重试。")
            log(f"port {p} occupied by another program")
        return

    if not SERVER.exists():
        alert(f"找不到服务文件：{SERVER}")
        return

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    exe = python_exe()
    startup = None
    flags = 0
    if os.name == "nt":
        startup = subprocess.STARTUPINFO()
        startup.lpTitle = "Garmin Health Dashboard - close this window to stop"
        flags = subprocess.CREATE_NEW_CONSOLE
    try:
        proc = subprocess.Popen(
            [str(exe), str(SERVER)], cwd=str(ROOT),
            creationflags=flags, startupinfo=startup,
        )
    except Exception as exc:
        alert(f"服务启动失败：{exc}\n请确认 Python 路径：{exe}")
        log(f"spawn failed: {exc}")
        return

    PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    deadline = time.time() + 25
    while time.time() < deadline:
        if listening(p):
            log(f"server started pid={proc.pid} port={p}")
            webbrowser.open(url(p))
            return
        if proc.poll() is not None:
            break
        time.sleep(0.3)

    log(f"server failed to become ready pid={proc.pid} exit={proc.poll()}")
    alert(f"服务未能在 25 秒内就绪。\n请查看 {LOG_FILE}，或手动运行 python server.py 看报错。")


def stop() -> None:
    p = port()
    pid = None
    try:
        pid = int(PID_FILE.read_text(encoding="utf-8").strip())
    except Exception:
        pass
    if pid is None:
        alert("未找到启动记录（data/server.pid）。\n若服务仍在运行，请直接关闭那个服务控制台窗口。")
        return
    result = subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass
    if listening(p):
        alert("服务仍在监听端口，可能由其他方式启动。\n请关闭对应的控制台窗口或在任务管理器结束 python.exe。")
        log(f"stop incomplete pid={pid}")
    else:
        log(f"server stopped pid={pid} rc={result.returncode}")


if __name__ == "__main__":
    if "--stop" in sys.argv:
        stop()
    else:
        start()
