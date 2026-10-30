"""生成桌面 / 开始菜单快捷方式（PowerShell 的 COM 被安全策略拦住时用这个）。"""

import os
import sys

import win32com.client

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(HERE, "balance_widget.py")
ICON = os.path.join(HERE, "app.ico")

# 优先用当前解释器旁边的 pythonw.exe，保证无控制台黑框
PYW = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
if not os.path.exists(PYW):
    PYW = "pythonw.exe"

APP = "中转管家"


def desktop():
    shell = win32com.client.Dispatch("WScript.Shell")
    return shell, shell.SpecialFolders("Desktop")


def make(shell, path, name, script=None, icon=None, desc=""):
    lnk = shell.CreateShortcut(os.path.join(path, name + ".lnk"))
    lnk.TargetPath = PYW
    lnk.Arguments = f'"{script or TARGET}"'
    lnk.WorkingDirectory = HERE
    lnk.IconLocation = f"{icon or ICON},0"
    lnk.Description = desc
    lnk.WindowStyle = 1
    lnk.Save()
    return os.path.join(path, name + ".lnk")


def main():
    shell, desk = desktop()
    out = make(shell, desk, APP, desc=f"{APP} · 置顶浮窗")
    print("desktop:", desk)
    print("created:", out, os.path.exists(out))

    # 开始菜单里也放一份，方便搜索启动
    start = os.path.join(os.environ["APPDATA"],
                         r"Microsoft\Windows\Start Menu\Programs")
    if os.path.isdir(start):
        s = make(shell, start, APP, desc=f"{APP} · 置顶浮窗")
        print("start menu:", s, os.path.exists(s))

    return 0


if __name__ == "__main__":
    sys.exit(main())
