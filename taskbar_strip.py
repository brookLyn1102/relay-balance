"""任务栏网速条：纯 Win32 子窗口，SetParent 进 Shell_TrayWnd。

思路和 TrafficMonitor 一样——不是"贴在任务栏上方的置顶窗口"，而是真正
成为任务栏窗口树的一员（子窗口），任务栏上浮时跟着走，不存在被覆盖。
绘制用 GDI 双缓冲，跑在自己的消息线程里，不占 Tk 主循环。
"""
import ctypes
import os
import threading
import time
from ctypes import wintypes

try:
    from usage_stats import fmt_tokens as _fmt_tok
except Exception:                                     # 单独跑时也不能崩
    def _fmt_tok(n):
        return str(int(n or 0))

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

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
DT_NOPREFIX = 0x0800
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

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "taskbar_strip.log")


def _log(*a):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(time.strftime("%H:%M:%S ") + " ".join(str(x) for x in a) + "\n")
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
        self._font = None
        self._font_px = 0
        self._key = None
        self._texts = []
        self._mid_w = 0
        self._sep_w = 0
        self._sep_gap = 0
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
        else:
            dark = bool(self.cfg.get("bar_dark", True))
            if dark:
                self.bg, self.fg = "#12151a", "#e8eaed"
                self.c_up, self.c_down = "#5aa9ff", "#34d399"
            else:
                self.bg, self.fg = "#f4f5f7", "#1a1d23"
                self.c_up, self.c_down = "#2563eb", "#059669"
            self.c_dim = "#8b95a3" if dark else "#7c8592"
        self._colors = True

    def _hdc(self):
        return user32.GetDC(self.hwnd)

    def _measure(self):
        """用最宽的可能值定尺寸，数字变化时不抖。"""
        tb_rc = wintypes.RECT()
        user32.GetWindowRect(self._taskbar(), ctypes.byref(tb_rc))
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
        self._sep_gap = max(7, int(self._h * 0.14))
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
            w += self.gap + right_w
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

    def _place(self):
        tb = self._taskbar()
        trc = wintypes.RECT()
        user32.GetWindowRect(tb, ctypes.byref(trc))
        th = trc.bottom - trc.top
        notify = user32.FindWindowExW(tb, None, "TrayNotifyWnd", None)
        nx = 0
        if notify:
            nrc = wintypes.RECT()
            user32.GetWindowRect(notify, ctypes.byref(nrc))
            nx = nrc.left - trc.left
        if nx <= 0:
            nx = (trc.right - trc.left) - int(th * 1.85)      # 通知区宽度约 1.85 倍栏高
        x = nx - self._w - 8 + int(self.cfg.get("bar_dx") or 0)
        y = (th - self._h) // 2 + int(self.cfg.get("bar_dy") or 0)
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
                    color = "#dc2626"
                elif pct >= 80:
                    color = "#b45309" if self._light else "#fbbf24"
                mid = [(f"{pct:.0f}%", color), ("MEM", self.c_dim)]

        right = []
        if self.cfg.get("bar_show_balance", True):
            w = getattr(self.app, "week", None)
            if w:
                pct = w.get("pct") or 0
                color = self.fg
                if pct >= 95:
                    color = "#dc2626"
                elif pct >= 80:
                    color = "#b45309" if self._light else "#fbbf24"
                right.append((self._fm(w.get("left"), False), color))
            else:
                right.append(("$—", self.fg))
        if self.cfg.get("bar_show_token", True):
            tok = self._tok_text()
            if tok:
                right.append((tok, self.fg))
        return left, mid, right

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
                                        DT_SINGLELINE | DT_VCENTER | DT_LEFT | DT_NOPREFIX)
                    x = self._draw_sep(mem, x + self._mid_w, h)
                if len(right) >= 2:
                    # 金额在上（对齐 ↑），今日 token 在下（对齐 ↓），右边缘对齐
                    for i, (text, color) in enumerate(right[:2]):
                        cy = h // 2 - dy + i * dy * 2
                        gdi32.SetTextColor(mem, cref(color))
                        box = wintypes.RECT(x, cy - dy // 2, w - self.pad, cy + dy // 2)
                        user32.DrawTextW(mem, text, -1, ctypes.byref(box),
                                        DT_SINGLELINE | DT_VCENTER | DT_RIGHT | DT_NOPREFIX)
                elif right:
                    text, color = right[0]
                    gdi32.SetTextColor(mem, cref(color))
                    box = wintypes.RECT(x, 0, w - self.pad, h)
                    user32.DrawTextW(mem, text, -1, ctypes.byref(box),
                                    DT_SINGLELINE | DT_VCENTER | DT_RIGHT | DT_NOPREFIX)
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

    def destroy(self):
        self._alive = False
        if self.hwnd:
            try:
                user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
            except Exception:
                pass
        self.hwnd = None
