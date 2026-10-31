"""轻量滚动日志。

写到 %LOCALAPPDATA%\\relay-balance\\app.log，单文件上限 1MB，超了轮转成
app.log.1（只留一代）。程序里那些 `except Exception: pass` 的地方至少能
留下一条痕迹，出问题时不用靠猜。

刻意不加日志级别和格式化前缀——这是个自用小工具，一行一条带时间就够。
"""
import os
import threading
import time

MAX_BYTES = 1024 * 1024

_LOCK = threading.Lock()
_PATH = None
_ENABLED = True


def log_path():
    """日志文件路径，首次调用时创建目录。"""
    global _PATH
    if _PATH:
        return _PATH
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    folder = os.path.join(base, "relay-balance")
    try:
        os.makedirs(folder, exist_ok=True)
    except Exception:
        folder = os.path.dirname(os.path.abspath(__file__))
    _PATH = os.path.join(folder, "app.log")
    return _PATH


def set_enabled(on):
    global _ENABLED
    _ENABLED = bool(on)


def _rotate(path):
    try:
        if os.path.getsize(path) < MAX_BYTES:
            return
        backup = path + ".1"
        try:
            os.replace(path, backup)
        except OSError:
            with open(path, "w", encoding="utf-8"):
                pass
    except OSError:
        pass


def log(*parts):
    """追加一行。任何异常都吞掉——日志本身不该影响主程序。"""
    if not _ENABLED:
        return
    try:
        line = " ".join(str(p) for p in parts)
    except Exception:
        return
    try:
        path = log_path()
        with _LOCK:
            _rotate(path)
            with open(path, "a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + line + "\n")
    except Exception:
        pass


def tail(limit=200):
    """读最近若干行，给「诊断信息」用。"""
    try:
        with open(log_path(), "r", encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-limit:])
    except Exception:
        return ""


def open_in_explorer():
    """打开日志所在目录并选中文件。"""
    path = log_path()
    try:
        if not os.path.exists(path):
            log("created by open_in_explorer")
        os.startfile(os.path.dirname(path))      # noqa: 仅 Windows
        return path
    except Exception:
        return path
