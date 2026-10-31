"""Win32 窗口工具 + 托盘图标。

Tk 窗口圆角、任务栏度量、把条提到任务栏之上、遮挡检测、托盘图标生成。
"""
import ctypes
import os
from ctypes import wintypes

from PIL import Image, ImageDraw, ImageFont

from rb_core import HERE

# ---------------------------------------------------------------- 窗口工具

def round_window(win):
    """Win11 圆角：无边框窗口默认是直角。"""
    try:
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()
        pref = ctypes.c_int(2)  # DWMWCP_ROUND
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref))
    except Exception:
        pass


def taskbar_metrics():
    """返回 (任务栏顶边 y, 托盘区左边 x, 任务栏高度)，单位物理像素。"""
    u = ctypes.windll.user32
    tb = u.FindWindowW("Shell_TrayWnd", None)
    if not tb:
        return None
    tr = wintypes.RECT()
    u.GetWindowRect(tb, ctypes.byref(tr))
    tray = u.FindWindowExW(tb, None, "TrayNotifyWnd", None)
    right = tr.right
    if tray:
        nr = wintypes.RECT()
        u.GetWindowRect(tray, ctypes.byref(nr))
        if nr.right - nr.left > 0:
            right = nr.left
    return tr.top, right, tr.bottom - tr.top


def taskbar_is_light():
    """任务栏是否为浅色（系统强调色/明暗主题）。浅色时文字要用深色。"""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        val, _ = winreg.QueryValueEx(key, "SystemUsesLightTheme")
        return bool(val)
    except Exception:
        return False


def as_tool_window(win, no_activate=True):
    """任务栏条：不出现在 Alt+Tab，点击不抢焦点。"""
    try:
        win.update_idletasks()
        hwnd = int(win.frame(), 16)
        u = ctypes.windll.user32
        u.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        u.GetWindowLongW.restype = ctypes.c_long
        u.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
        u.SetWindowLongW.restype = ctypes.c_long
        ex = u.GetWindowLongW(hwnd, -20)
        add = 0x80                      # WS_EX_TOOLWINDOW
        if no_activate:
            add |= 0x08000000           # WS_EX_NOACTIVATE
        u.SetWindowLongW(hwnd, -20, ctypes.c_long(ex | add))
        # 改扩展样式后必须 frame changed 重画，否则分层窗口会整块残影/不显示
        SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER = 0x1, 0x2, 0x4
        SWP_NOACTIVATE, SWP_FRAMECHANGED, SWP_SHOWWINDOW = 0x10, 0x20, 0x40
        u.SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                       SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER |
                       SWP_NOACTIVATE | SWP_FRAMECHANGED | SWP_SHOWWINDOW)
        return hwnd
    except Exception:
        return None


def raise_over_taskbar(hwnd, force_top=False):
    """任务栏是 XAML 合成层，普通 topmost 会被它盖住；用 SetWindowBand 提到更高 band。

    force_top=True 时先按 HWND_TOP 重排一次 Z 序——截图工具、任务栏重排之后
    任务栏会把自己提到最上面，只调 TOPMOST 抢不回来，必须先插到 Z 序顶部。
    """
    if not hwnd:
        return
    u = ctypes.windll.user32
    try:
        fn = u.SetWindowBand
        fn.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_uint]
        fn.restype = wintypes.BOOL
        fn(hwnd, None, 16)              # ZBID_SYSTEM_TOOLS
    except Exception:
        pass
    SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x1, 0x2, 0x10, 0x40
    flags = SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_SHOWWINDOW
    if force_top:
        u.SetWindowPos(hwnd, 0, 0, 0, 0, 0, flags)      # HWND_TOP
    u.SetWindowPos(hwnd, -1, 0, 0, 0, 0, flags)         # HWND_TOPMOST


def is_cloaked(hwnd):
    """虚拟桌面/挂起的 UWP 窗口仍"可见"但不显示，遍历 Z 序时要跳过。"""
    try:
        v = wintypes.DWORD(0)
        hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd), 14, ctypes.byref(v), ctypes.sizeof(v))  # DWMWA_CLOAKED
        return hr == 0 and bool(v.value)
    except Exception:
        return False


def window_covered(hwnd):
    """条是否被外部窗口（截图遮罩、任务栏重排后的层级）盖住了。

    走 Z 序比对而不是 WindowFromPoint：透明底的条在空白处点击会穿透，
    按点命中判断会误报成「被任务栏盖住」。自家的面板 / 菜单不算。

    条自己是 topmost，Z 序里非置顶的窗口不可能盖在它上面，所以连续遇到
    几个普通窗口就提前收工——这条路径实测 0.06ms，比全量遍历快 12 倍。
    留 3 个容错，防止个别窗口读不到 EXSTYLE 被误判。
    """
    if not hwnd:
        return False
    u = ctypes.windll.user32
    try:
        r = wintypes.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(r))
        plain = 0
        w = u.GetTopWindow(None)
        while w and w != hwnd:
            if u.IsWindowVisible(w) and not u.IsIconic(w):
                if not (u.GetWindowLongW(w, -20) & 0x8):    # WS_EX_TOPMOST
                    plain += 1
                    if plain >= 3:
                        break
                else:
                    plain = 0
                    wr = wintypes.RECT()
                    u.GetWindowRect(w, ctypes.byref(wr))
                    if (wr.left < r.right and wr.right > r.left and
                            wr.top < r.bottom and wr.bottom > r.top):
                        pid = wintypes.DWORD(0)
                        u.GetWindowThreadProcessId(w, ctypes.byref(pid))
                        if pid.value != os.getpid() and not is_cloaked(w):
                            return True
            w = u.GetWindow(w, 2)       # GW_HWNDNEXT：往 Z 序下方走
        return False
    except Exception:
        return False

# ---------------------------------------------------------------- 图标

def tray_image():
    ico = os.path.join(HERE, "app.ico")
    if os.path.exists(ico):
        try:
            return Image.open(ico).convert("RGBA").resize((64, 64), Image.LANCZOS)
        except Exception:
            pass
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([2, 2, 62, 62], radius=15, fill=(52, 211, 153, 255))
    font = None
    for path in (r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\seguisb.ttf"):
        try:
            font = ImageFont.truetype(path, 44)
            break
        except Exception:
            continue
    if font:
        d.text((32, 31), "¥", font=font, fill=(18, 21, 26, 255), anchor="mm")
    return img
