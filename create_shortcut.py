"""在桌面创建「Garmin 健康看板」快捷方式（可在重装/迁移后重复运行，会覆盖同名图标）。

快捷方式直接调用 managed Python 的 pythonw.exe 执行 launcher.py：
- 无控制台闪烁，少一层 WSH 依赖；
- 端口已被占用时 launcher 会复用现有服务，不会重复启动。
项目内的 start_dashboard.vbs / stop_dashboard.vbs 保留为备用入口。

依赖 pywin32（仅创建快捷方式时需要；看板本身零依赖）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import win32com.client

ROOT = Path(__file__).resolve().parent
NAME = "Garmin 健康看板.lnk"


def main() -> None:
    shell = win32com.client.Dispatch("WScript.Shell")
    desktop = Path(shell.SpecialFolders("Desktop"))
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    launcher = ROOT / "launcher.py"
    icon = ROOT / "web" / "favicon.ico"

    for required in (pythonw, launcher, icon):
        if not required.exists():
            raise SystemExit(f"缺少文件：{required}")

    lnk = desktop / NAME
    shortcut = shell.CreateShortcut(str(lnk))
    shortcut.TargetPath = str(pythonw)
    shortcut.Arguments = f'"{launcher}"'
    shortcut.WorkingDirectory = str(ROOT)
    shortcut.IconLocation = f"{icon},0"
    shortcut.Description = "Garmin 健康趋势看板：睡眠 / HRV / 卡路里 + DeepSeek 解读"
    shortcut.Save()

    check = shell.CreateShortcut(str(lnk))
    print(f"shortcut : {lnk}")
    print(f"target   : {check.TargetPath}")
    print(f"args     : {check.Arguments}")
    print(f"workdir  : {check.WorkingDirectory}")
    print(f"icon     : {check.IconLocation}")
    print(f"exists   : {lnk.exists()}")


if __name__ == "__main__":
    main()
