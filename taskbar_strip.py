"""任务栏网速条：纯 Win32 子窗口，SetParent 进 Shell_TrayWnd。

思路和 TrafficMonitor 一样——不是"贴在任务栏上方的置顶窗口"，而是真正
成为任务栏窗口树的一员（子窗口），任务栏上浮时跟着走，不存在被覆盖。
绘制用 GDI 双缓冲，跑在自己的消息线程里，不占 Tk 主循环。
"""
import ctypes
import os
import threading
from ctypes import wintypes

import applog

try:
    from usage_stats import fmt_tokens as _fmt_tok
except Exception:                                     # 单独跑时也不能崩
    def _fmt_tok(n):
        return str(int(n or 0))

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

WM_PAINT = 0x000F
WM_ERASEBKGND = 0x0014
WM_TIMER = 0x0113
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_RBUTTONUP = 0x0205
WM_LBUTTONDBLCLK = 0x0203
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_MOUSEMOVE = 0x0200
WM_NCHITTEST = 0x0084
WM_APP_REDRAW = 0x8001

WS_CHILD = 0x40000000
WS_POPUP = 0x80000000
WS_VISIBLE = 0x10000000
WS_CLIPSIBLINGS = 0x04000000

WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_EX_LAYERED = 0x00080000

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040

LWA_COLORKEY = 0x1
DT_SINGLELINE = 0x0020
DT_VCENTER = 0x0004
DT_LEFT = 0x0000
DT_RIGHT = 0x0002
DT_CENTER = 0x0001
DT_NOPREFIX = 0x0800
DT_END_ELLIPSIS = 0x8000
SRCCOPY = 0x00CC0020
TRANSPARENT = 1
FW_BOLD = 700
DEFAULT_CHARSET = 1
OUT_TT_PRECIS = 5
CLIP_DEFAULT_PRECIS = 0
CLEARTYPE_QUALITY = 5
FF_MODERN = 0x30

HWND_TOPMOST = -1

_ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [
        ("hdc", wintypes.HDC),
        ("fErase", wintypes.BOOL),
        ("rcPaint", wintypes.RECT),
        ("fRestore", wintypes.BOOL),
        ("fIncUpdate", wintypes.BOOL),
        ("rgbReserved", ctypes.c_byte * 32),
    ]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", wintypes.POINT),
    ]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class APPBARDATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uCallbackMessage", wintypes.UINT),
        ("uEdge", wintypes.UINT),
        ("rc", wintypes.RECT),
        ("lParam", wintypes.LPARAM),
    ]


# 任务栏停靠边（SHAppBarMessage ABM_GETTASKBARPOS 的 uEdge）
ABE_LEFT, ABE_TOP, ABE_RIGHT, ABE_BOTTOM = 0, 1, 2, 3
ABM_GETTASKBARPOS = 5
_EDGES = ("left", "top", "right", "bottom")


user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
user32.RegisterClassExW.restype = wintypes.ATOM
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.CreateWindowExW.restype = wintypes.HWND
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                  wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.SetParent.argtypes = [wintypes.HWND, wintypes.HWND]
user32.SetParent.restype = wintypes.HWND
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                wintypes.UINT]
user32.SetWindowPos.restype = wintypes.BOOL
user32.BeginPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
user32.BeginPaint.restype = wintypes.HDC
user32.EndPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.SetTimer.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.LPVOID]
user32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.COLORREF,
                                              ctypes.c_ubyte, wintypes.DWORD]
gdi32.CreateFontW.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, wintypes.DWORD, wintypes.DWORD,
                              wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                              wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                              wintypes.LPCWSTR]
gdi32.CreateFontW.restype = wintypes.HFONT
user32.DrawTextW.argtypes = [wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int,
                            ctypes.POINTER(wintypes.RECT), wintypes.UINT]
gdi32.GetTextExtentPoint32W.argtypes = [wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int,
                                        ctypes.POINTER(SIZE)]

# 64 位下句柄是 8 字节，函数签名必须显式声明，否则会被截成 c_int
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
user32.SetWindowLongW.restype = ctypes.c_long
user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND,
                                 wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowExW.restype = wintypes.HWND
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.InvalidateRect.argtypes = [wintypes.HWND, wintypes.LPVOID, wintypes.BOOL]
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                wintypes.WPARAM, wintypes.LPARAM]
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.LoadCursorW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
user32.LoadCursorW.restype = wintypes.HANDLE
user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), wintypes.HWND,
                               wintypes.UINT, wintypes.UINT]
user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
user32.DispatchMessageW.restype = ctypes.c_ssize_t
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.CreateSolidBrush.argtypes = [wintypes.COLORREF]
gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
user32.FillRect.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.HBRUSH]
gdi32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
gdi32.SetTextColor.argtypes = [wintypes.HDC, wintypes.COLORREF]
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         ctypes.c_int, wintypes.HDC, ctypes.c_int, ctypes.c_int,
                         wintypes.DWORD]
shell32.SHAppBarMessage.argtypes = [wintypes.DWORD, ctypes.POINTER(APPBARDATA)]
shell32.SHAppBarMessage.restype = ctypes.c_size_t

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "taskbar_strip.log")


def _log(*a):
    """统一进 applog（%LOCALAPPDATA%\\relay-balance\\app.log），不再单开一份。"""
    try:
        applog.log("[strip]", *a)
    except Exception:
        pass


def cref(hex_color):
    """'#2563eb' -> COLORREF (0x00bbggrr)"""
    h = hex_color.lstrip("#")
    return (int(h[4:6], 16) << 16) | (int(h[2:4], 16) << 8) | int(h[0:2], 16)


def fmt_speed(bps):
    if bps is None:
        return "—"
    v = float(bps)
    for unit in ("B/s", "KB/s", "MB/s", "GB/s"):
        if v < 1024 or unit == "GB/s":
            return (f"{v:.1f} {unit}" if v < 100 else f"{v:.0f} {unit}")
        v /= 1024.0
    return "—"


def fmt_money(v, sign=True):
    if v is None:
        return "$—"
    v = float(v)
    body = f"${abs(v):,.2f}"
    return ("-" + body) if (sign and v < 0) else body


def fmt_speed_short(bps):
    """竖排任务栏太窄，单位压成一个字母：9.9M / 812K / 1.2G。"""
    if bps is None:
        return "—"
    v = float(bps)
    for unit in ("B", "K", "M", "G"):
        if v < 1024 or unit == "G":
            return (f"{v:.0f}{unit}" if v >= 100 else f"{v:.1f}{unit}")
        v /= 1024.0
    return "—"


def fmt_money_short(v, digits=2):
    if v is None:
        return "$—"
    v = float(v)
    if abs(v) >= 10000:
        return f"${v / 1000:.1f}k"
    return f"${v:.{digits}f}"


class TaskbarStrip:
    """挂在任务栏里的网速条。对外接口和 Tk 版一致：tick() / destroy()。"""

    _cls_registered = False

    def __init__(self, app, fmt_speed_fn=None, fmt_money_fn=None, mem_fn=None):
        self.app = app
        self.cfg = app.cfg
        self._fs = fmt_speed_fn or fmt_speed
        self._fm = fmt_money_fn or fmt_money
        self._mem = mem_fn                       # 返回 (可用 GB, 已用 %)
        self.hwnd = None
        self.ready = threading.Event()
        self.failed = None
        self._alive = True
        self._w = 200
        self._h = 40
        self.edge = "bottom"          # left / top / right / bottom
        self.vertical = False         # 左/右停靠要走竖排布局
        self._font = None
        self._font_px = 0
        self._key = None
        self._texts = []
        self._mid_w = 0
        self._sep_w = 0
        self._sep_gap = 0
        self._rgap = 0
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="taskbar-strip")
        self._thread.start()
        self.ready.wait(3.0)
        if self.failed:
            raise RuntimeError(self.failed)

    # ---------------- 线程入口

    def _run(self):
        try:
            self._create()
        except Exception as e:  # noqa
            self.failed = f"{type(e).__name__}: {e}"
            self.ready.set()
            return
        self.ready.set()
        msg = MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _create(self):
        hinst = kernel32.GetModuleHandleW(None)
        cls_name = "RelayBalanceStrip"
        if not TaskbarStrip._cls_registered:
            self._wndproc = WNDPROC(self._on_message)
            wc = WNDCLASSEXW()
            wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
            wc.style = 0
            wc.lpfnWndProc = self._wndproc
            wc.hInstance = hinst
            wc.hCursor = None                                  # 继承任务栏的指针
            wc.lpszClassName = cls_name
            if not user32.RegisterClassExW(ctypes.byref(wc)):
                err = ctypes.get_last_error()
                if err not in (0, 1410):                       # 1410 = 已注册
                    raise OSError(f"RegisterClassEx failed: {err}")
            TaskbarStrip._cls_registered = True
        else:
            self._wndproc = WNDPROC(self._on_message)

        tb = self._taskbar()
        style = WS_CHILD | WS_VISIBLE | WS_CLIPSIBLINGS
        ex = WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        hwnd = user32.CreateWindowExW(ex, cls_name, "relay-strip", style,
                                      0, 0, self._w, self._h,
                                      tb, None, hinst, None)
        if not hwnd:
            raise OSError(f"CreateWindowEx failed: {ctypes.get_last_error()}")
        self.hwnd = hwnd
        _log("created", hex(hwnd))

        user32.SetParent(hwnd, tb)
        _log("SetParent ->", hex(tb), "parent now", hex(user32.GetParent(hwnd) or 0))

        self._apply_layered()
        self._measure()
        self._place()
        user32.SetTimer(hwnd, 1, 1000, None)
        self._self_check()
        self._paint(hwnd)

    def _taskbar(self):
        tb = user32.FindWindowW("Shell_TrayWnd", None)
        if not tb:
            raise OSError("找不到任务栏")
        return tb

    def _apply_layered(self):
        """透明底：和 Tk 版一样用接近任务栏的 key 色，抗锯齿边缘不突兀。"""
        if not self.cfg.get("bar_transparent", True):
            self._key = None
            return
        light = self._is_light()
        key = "#f3f4f6" if light else "#1f2229"
        ex = user32.GetWindowLongW(self.hwnd, -20)
        user32.SetWindowLongW(self.hwnd, -20, ex | WS_EX_LAYERED)
        ok = user32.SetLayeredWindowAttributes(self.hwnd, cref(key), 0, LWA_COLORKEY)
        _log("layered", "ok" if ok else "fail", "key", key)
        self._key = key

    @staticmethod
    def _is_light():
        try:
            import winreg
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                               r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
            v, _ = winreg.QueryValueEx(k, "SystemUsesLightTheme")
            winreg.CloseKey(k)
            return bool(v)
        except Exception:
            return True

    # ---------------- 尺寸与位置

    def _theme(self):
        light = self._is_light()
        if getattr(self, "_light", None) == light and getattr(self, "_colors", None):
            return
        self._light = light
        if self.cfg.get("bar_transparent", True):
            self.bg = "#f3f4f6" if light else "#1f2229"
            self.fg = "#1a1d23" if light else "#e8eaed"
            self.c_up = "#2563eb" if light else "#5aa9ff"
            self.c_down = "#059669" if light else "#34d399"
            self.c_dim = "#7c8592" if light else "#8b95a3"
            self.c_good = "#059669" if light else "#34d399"
            self.c_warn = "#b45309" if light else "#fbbf24"
            self.c_bad = "#dc2626" if light else "#f87171"
        else:
            dark = bool(self.cfg.get("bar_dark", True))
            if dark:
                self.bg, self.fg = "#12151a", "#e8eaed"
                self.c_up, self.c_down = "#5aa9ff", "#34d399"
                self.c_good, self.c_warn, self.c_bad = "#34d399", "#f5b544", "#f87171"
            else:
                self.bg, self.fg = "#f4f5f7", "#1a1d23"
                self.c_up, self.c_down = "#2563eb", "#059669"
                self.c_good, self.c_warn, self.c_bad = "#059669", "#b45309", "#dc2626"
            self.c_dim = "#8b95a3" if dark else "#7c8592"
        self._colors = True

    def _hdc(self):
        return user32.GetDC(self.hwnd)

    def _measure(self):
        """用最宽的可能值定尺寸，数字变化时不抖。

        任务栏可以停在上/下/左/右四条边，左右停靠时厚度只有 40~60px，
        横排三列根本放不下，得整体换成「一行一项」的竖排布局。
        """
        self.edge = self._detect_edge()
        self.vertical = self.edge in ("left", "right")
        tb_rc = self._tb_rect()
        self._thick = (tb_rc.right - tb_rc.left) if self.vertical \
            else (tb_rc.bottom - tb_rc.top)
        if self.vertical:
            self._measure_vertical(tb_rc)
        else:
            self._measure_horizontal(tb_rc)

    def _tb_rect(self):
        rc = wintypes.RECT()
        user32.GetWindowRect(self._taskbar(), ctypes.byref(rc))
        return rc

    def _detect_edge(self):
        """任务栏停在哪条边。SHAppBarMessage 拿不到时用矩形和屏幕比。"""
        try:
            abd = APPBARDATA()
            abd.cbSize = ctypes.sizeof(APPBARDATA)
            if shell32.SHAppBarMessage(ABM_GETTASKBARPOS, ctypes.byref(abd)) \
                    and abd.uEdge <= 3:
                return _EDGES[abd.uEdge]
        except Exception as e:
            _log("edge query fail", e)
        rc = self._tb_rect()
        if rc.right - rc.left >= rc.bottom - rc.top:
            return "top" if rc.top <= 0 else "bottom"
        return "left" if rc.left <= 0 else "right"

    def _measure_horizontal(self, tb_rc):
        self._h = tb_rc.bottom - tb_rc.top
        self._font_px = max(12, int(self._h * 0.30))
        self._make_font()

        hdc = self._hdc()
        try:
            gdi32.SelectObject(hdc, self._font)
            def width(text):
                sz = SIZE()
                gdi32.GetTextExtentPoint32W(hdc, text, len(text), ctypes.byref(sz))
                return sz.cx
            up_w = width("↑ 999.9 MB/s")
            dn_w = width("↓ 999.9 MB/s")
            bal_w = width("$9,999.99")
            tok_w = width("999.9M tok")
            mid_w = max(width("100%"), width("MEM"))
        finally:
            user32.ReleaseDC(self.hwnd, hdc)

        self.stack = bool(self.cfg.get("bar_stack", True))
        self.pad = max(8, int(self._h * 0.22))
        self.gap = max(10, int(self._h * 0.30))
        net_w = max(up_w, dn_w) if self.stack else up_w + self.gap + dn_w
        show_mem = bool(self.cfg.get("bar_show_mem", True))
        self._mid_w = mid_w if show_mem else 0
        self._sep_w = max(2, int(self._h * 0.03)) if show_mem else 0
        self._sep_gap = max(4, int(self._h * 0.07))
        # 有中列时，竖线已经起到分隔作用，右列不用再留一大段
        self._rgap = self._sep_gap + 5 if show_mem else self.gap
        w = self.pad * 2 + net_w
        if show_mem:
            w += self._sep_gap * 4 + self._sep_w * 2 + self._mid_w

        show_bal = bool(self.cfg.get("bar_show_balance", True))
        show_tok = bool(self.cfg.get("bar_show_token", True))
        right_w = 0
        if show_bal:
            if show_tok and self.stack:
                right_w = max(bal_w, tok_w)          # 右侧上下两行，取宽的
            else:
                right_w = bal_w
                if show_tok:
                    right_w += self.gap + tok_w      # 单行时并排
        elif show_tok:
            right_w = tok_w
        if right_w:
            w += self._rgap + right_w
        self._w = int(w)

        self._net_w = net_w
        _log("measure", "h", self._h, "w", self._w, "font", self._font_px, "stack", self.stack)

    def _make_font(self):
        if self._font and self._font_px == getattr(self, "_font_used", 0):
            return
        if self._font:
            gdi32.DeleteObject(self._font)
        self._font_used = self._font_px
        self._font = gdi32.CreateFontW(
            -self._font_px, 0, 0, 0, FW_BOLD, 0, 0, 0, DEFAULT_CHARSET,
            OUT_TT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY,
            FF_MODERN, "Consolas")

    def _measure_vertical(self, tb_rc):
        """竖排：一屏一项，从上往下摆。左右停靠的任务栏只有 40~60px 厚，
        原来的三列横排塞不下，字号和单位都得跟着瘦身。"""
        thick = max(24, tb_rc.right - tb_rc.left)
        length = max(60, tb_rc.bottom - tb_rc.top)
        self.stack = True
        self._w = max(18, thick - 2)
        self.pad = max(4, int(thick * 0.10))
        self._row_n = 2                                   # ↑ ↓
        if self.cfg.get("bar_show_mem", True) and self._mem is not None:
            self._row_n += 1
        if self.cfg.get("bar_show_balance", True):
            self._row_n += 1
        if self.cfg.get("bar_show_token", True):
            self._row_n += 1
        self._font_px = max(10, int(thick * 0.26))
        while True:
            self._make_font()
            row_h = self._font_px + max(3, int(self._font_px * 0.30))
            total = row_h * self._row_n + self.pad * 2
            if total <= length * 0.86 or self._font_px <= 9:
                break
            self._font_px -= 1
        self._row_h = row_h
        self._h = total
        _log("measure vertical", self.edge, "w", self._w, "h", self._h,
             "rows", self._row_n, "font", self._font_px)

    def _place(self):
        tb = self._taskbar()
        trc = wintypes.RECT()
        user32.GetWindowRect(tb, ctypes.byref(trc))
        tw, th = trc.right - trc.left, trc.bottom - trc.top
        dx = int(self.cfg.get("bar_dx") or 0)
        dy = int(self.cfg.get("bar_dy") or 0)
        notify = user32.FindWindowExW(tb, None, "TrayNotifyWnd", None)
        nrc = wintypes.RECT()
        if notify:
            user32.GetWindowRect(notify, ctypes.byref(nrc))
        if self.vertical:
            # 通知区在竖排任务栏的最下面，条要压在它上方
            ny = (nrc.top - trc.top) if notify else 0
            if ny <= 0:
                ny = th - int(tw * 1.85)
            x = (tw - self._w) // 2 + dx
            y = ny - self._h - 6 + dy
        else:
            nx = (nrc.left - trc.left) if notify else 0
            if nx <= 0:
                nx = tw - int(th * 1.85)                  # 通知区宽度约 1.85 倍栏高
            x = nx - self._w - 8 + dx
            y = (th - self._h) // 2 + dy
        user32.SetWindowPos(self.hwnd, None, x, y, self._w, self._h,
                            SWP_NOZORDER | SWP_NOACTIVATE | SWP_SHOWWINDOW)

    def _self_check(self):
        """条的中心点是否真的属于自己——用来确认没被任务栏内容层盖住。"""
        try:
            r = wintypes.RECT()
            user32.GetWindowRect(self.hwnd, ctypes.byref(r))
            pt = wintypes.POINT((r.left + r.right) // 2, (r.top + r.bottom) // 2)
            hit = user32.WindowFromPoint(pt)
            root = user32.GetAncestor(hit, 2) if hit else 0      # GA_ROOT
            _log("selfcheck point", (pt.x, pt.y), "hit", hex(hit or 0), "root", hex(root or 0),
                 "self", hex(self.hwnd), "me" if hit == self.hwnd else "覆盖")
            if hit != self.hwnd:
                # 找一下是谁盖在上面
                b = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hit, b, 256)
                _log("covered by class", b.value)
        except Exception as e:
            _log("selfcheck err", e)

    # ---------------- 绘制

    def _tok_text(self):
        t = getattr(self.app, "today_tok", None) or {}
        total = (t.get("in") or 0) + (t.get("out") or 0)
        return (_fmt_tok(total) + " tok") if total else ""

    def _pct_color(self, pct):
        """和面板进度条同一套阈值：<80 绿、<95 琥珀、≥95 红。"""
        if pct >= 95:
            return self.c_bad
        if pct >= 80:
            return self.c_warn
        return self.c_good

    def _draw_sep(self, mem, x, h):
        """画一条贯穿两行的竖线（比字符 | 长，视觉上才像分隔），返回下一列起点。"""
        left = x + self._sep_gap
        brush = gdi32.CreateSolidBrush(cref(self.c_dim))
        rect = wintypes.RECT(left, int(h * 0.24), left + self._sep_w, int(h * 0.76))
        user32.FillRect(mem, ctypes.byref(rect), brush)
        gdi32.DeleteObject(brush)
        return left + self._sep_w + self._sep_gap

    def _segments(self):
        up = self._fs(self.app.net.up)
        dn = self._fs(self.app.net.down)
        left = [("↑ " + up, self.c_up), ("↓ " + dn, self.c_down)]

        # 中间两行：占用百分比在上，MEM 在下
        mid = None
        if self.cfg.get("bar_show_mem", True) and self._mem is not None:
            try:
                _gb, pct = self._mem()
            except Exception:
                pct = None
            if pct is not None:
                color = self.fg
                if pct >= 90:
                    color = self.c_bad
                elif pct >= 80:
                    color = self.c_warn
                mid = [(f"{pct:.0f}%", color), ("MEM", self.c_dim)]

        right = []
        if self.cfg.get("bar_show_balance", True):
            w = getattr(self.app, "week", None)
            if w:
                right.append((self._fm(w.get("left"), False),
                              self._pct_color(w.get("pct") or 0)))
            else:
                right.append(("$—", self.c_dim))
        if self.cfg.get("bar_show_token", True):
            tok = self._tok_text()
            if tok:
                right.append((tok, self.fg))
        return left, mid, right

    def _draw_horizontal(self, mem, w, h):
        """上下停靠：网速上下两行，中间一列内存，右边金额 + token。"""
        left, mid, right = self._segments()
        x = self.pad
        if self.stack:
            dy = max(6, int(h * 0.215))
            for i, (text, color) in enumerate(left[:2]):
                gdi32.SetTextColor(mem, cref(color))
                box = wintypes.RECT(x, h // 2 - dy + i * dy * 2 - dy // 2,
                                    x + self._net_w, h // 2 - dy + i * dy * 2 + dy // 2)
                user32.DrawTextW(mem, text, -1, ctypes.byref(box),
                                 DT_SINGLELINE | DT_VCENTER | DT_LEFT | DT_NOPREFIX)
            x += self._net_w
            if mid:
                x = self._draw_sep(mem, x, h)
                # 中列两行，行位置与左侧完全一致
                for i, (text, color) in enumerate(mid[:2]):
                    gdi32.SetTextColor(mem, cref(color))
                    box = wintypes.RECT(x, h // 2 - dy + i * dy * 2 - dy // 2,
                                        x + self._mid_w, h // 2 - dy + i * dy * 2 + dy // 2)
                    user32.DrawTextW(mem, text, -1, ctypes.byref(box),
                                     DT_SINGLELINE | DT_VCENTER | DT_CENTER | DT_NOPREFIX)
                x = self._draw_sep(mem, x + self._mid_w, h)
            if len(right) >= 2:
                # 金额在上（对齐 ↑），今日 token 在下（对齐 ↓），紧贴竖线左对齐
                for i, (text, color) in enumerate(right[:2]):
                    cy = h // 2 - dy + i * dy * 2
                    gdi32.SetTextColor(mem, cref(color))
                    box = wintypes.RECT(x + self._rgap, cy - dy // 2, w - self.pad, cy + dy // 2)
                    user32.DrawTextW(mem, text, -1, ctypes.byref(box),
                                     DT_SINGLELINE | DT_VCENTER | DT_LEFT | DT_NOPREFIX)
            elif right:
                text, color = right[0]
                gdi32.SetTextColor(mem, cref(color))
                box = wintypes.RECT(x + self._rgap, 0, w - self.pad, h)
                user32.DrawTextW(mem, text, -1, ctypes.byref(box),
                                 DT_SINGLELINE | DT_VCENTER | DT_LEFT | DT_NOPREFIX)
        else:
            seq = list(left)
            if mid:
                seq.append((" ".join(t for t, _c in mid), mid[0][1]))
            seq += right
            for text, color in seq:
                gdi32.SetTextColor(mem, cref(color))
                box = wintypes.RECT(x, 0, w, h)
                user32.DrawTextW(mem, text, -1, ctypes.byref(box),
                                 DT_SINGLELINE | DT_VCENTER | DT_LEFT | DT_NOPREFIX)
                sz = SIZE()
                gdi32.GetTextExtentPoint32W(mem, text, len(text), ctypes.byref(sz))
                x += sz.cx + self.gap

    def _draw_vertical(self, mem, w, h):
        """左右停靠：一行一项，居中，超宽自动省略号。"""
        rows = self._row_texts()
        if not rows:
            return
        row_h = max(1, h // len(rows))
        for i, (text, color) in enumerate(rows):
            if not text:
                continue
            gdi32.SetTextColor(mem, cref(color))
            box = wintypes.RECT(2, i * row_h, w - 2, (i + 1) * row_h)
            user32.DrawTextW(mem, text, -1, ctypes.byref(box),
                             DT_SINGLELINE | DT_VCENTER | DT_CENTER
                             | DT_NOPREFIX | DT_END_ELLIPSIS)

    def _row_texts(self):
        """竖排每行一项，单位压到最短，保证 40~60px 宽能塞下。"""
        rows = [("↑" + fmt_speed_short(self.app.net.up), self.c_up),
                ("↓" + fmt_speed_short(self.app.net.down), self.c_down)]
        if self.cfg.get("bar_show_mem", True) and self._mem is not None:
            try:
                _gb, pct = self._mem()
            except Exception:
                pct = None
            if pct is not None:
                color = self.c_bad if pct >= 90 else (
                    self.c_warn if pct >= 80 else self.fg)
                rows.append((f"{pct:.0f}%", color))
        if self.cfg.get("bar_show_balance", True):
            wk = getattr(self.app, "week", None)
            if wk:
                # 任务栏越窄越省字：46px 的窄条放不下 $9,999.99 那种长度
                digits = 2 if self._w >= 54 else 1
                rows.append((fmt_money_short(wk.get("left"), digits),
                             self._pct_color(wk.get("pct") or 0)))
            else:
                rows.append(("$—", self.c_dim))
        if self.cfg.get("bar_show_token", True):
            rows.append((self._tok_text().replace(" tok", ""), self.fg))
        return rows

    def _paint(self, hwnd):
        self._theme()
        ps = PAINTSTRUCT()
        hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
        if not hdc:
            return
        try:
            rc = wintypes.RECT()
            user32.GetClientRect(hwnd, ctypes.byref(rc))
            w, h = rc.right - rc.left, rc.bottom - rc.top
            mem = gdi32.CreateCompatibleDC(hdc)
            bmp = gdi32.CreateCompatibleBitmap(hdc, max(w, 1), max(h, 1))
            old_bmp = gdi32.SelectObject(mem, bmp)
            brush = gdi32.CreateSolidBrush(cref(self.bg))
            user32.FillRect(mem, ctypes.byref(rc), brush)
            gdi32.DeleteObject(brush)

            gdi32.SetBkMode(mem, TRANSPARENT)
            gdi32.SelectObject(mem, self._font)
            if self.vertical:
                self._draw_vertical(mem, w, h)
            else:
                self._draw_horizontal(mem, w, h)

            gdi32.BitBlt(hdc, 0, 0, w, h, mem, 0, 0, SRCCOPY)
            gdi32.SelectObject(mem, old_bmp)
            gdi32.DeleteObject(bmp)
            gdi32.DeleteDC(mem)
        finally:
            user32.EndPaint(hwnd, ctypes.byref(ps))

    # ---------------- 消息

    def _on_message(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_PAINT:
                self._paint(hwnd)
                return 0
            if msg == WM_ERASEBKGND:
                return 1
            if msg in (WM_APP_REDRAW, WM_TIMER):
                self._theme()
                # 配置变了、任务栏换了边、或者用户调了任务栏大小，都要重算尺寸
                edge = self._detect_edge()
                tb_rc = self._tb_rect()
                thick = (tb_rc.right - tb_rc.left) if edge in ("left", "right") \
                    else (tb_rc.bottom - tb_rc.top)
                if edge != self.edge or thick != getattr(self, "_thick", None):
                    _log("layout changed", self.edge, self._thick, "->", edge, thick)
                    self._need_measure = True
                if getattr(self, "_need_measure", False):
                    self._need_measure = False
                    self._measure()              # 配置变了要按新内容重算宽度
                self._place()
                user32.InvalidateRect(hwnd, None, False)
                return 0
            if msg == WM_RBUTTONUP:
                self._popup_menu()
                return 0
            if msg == WM_LBUTTONDBLCLK:
                self.app.root.after(0, self.app.show)
                return 0
            if msg == WM_CLOSE:
                user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
        except Exception as e:
            _log("wndproc err", msg, e)
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _popup_menu(self):
        """右键菜单还是交给 Tk 的，位置取鼠标当前点。"""
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))

        def show():
            try:
                self.app.build_menu()
                self.app.menu.tk_popup(pt.x, pt.y)
            except Exception as e:
                _log("menu err", e)

        self.app.root.after(0, show)

    # ---------------- 对外接口

    def place(self):
        self.tick()

    def refresh_layout(self):
        """条上的内容项变了（显示余额 / token / 换行方式）：重算尺寸再摆位。"""
        self._need_measure = True
        self.tick()

    def tick(self):
        if not self.hwnd or not self._alive:
            return
        user32.PostMessageW(self.hwnd, WM_APP_REDRAW, 0, 0)

    def health(self):
        """条还挂在任务栏上吗。

        explorer 重启（或崩了自动重启）时 Shell_TrayWnd 会换句柄，旧的条跟着
        旧任务栏一起销毁——线程退出、窗口消失，界面看着就是「条没了」。这里
        给主程序一个判断依据，好重建。
        """
        if not self._alive or not self.hwnd:
            return False
        try:
            if not user32.IsWindow(self.hwnd):
                return False
            if not self._thread.is_alive():
                return False
            tb = user32.FindWindowW("Shell_TrayWnd", None)
            if not tb:
                return False
            return user32.GetParent(self.hwnd) == tb
        except Exception:
            return False

    def destroy(self):
        self._alive = False
        if self.hwnd:
            try:
                user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
            except Exception:
                pass
        self.hwnd = None
