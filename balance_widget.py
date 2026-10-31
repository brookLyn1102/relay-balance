"""中转管家 —— 置顶小窗 + 托盘图标

余额数据来自 new-api 系列中转的 OpenAI 兼容计费接口：
    /v1/dashboard/billing/subscription   总额度
    /v1/dashboard/billing/usage          累计消费（美分）

密钥从 magpie 的 providers.json 读取，不保存在本文件里。
"""

import ctypes
import datetime
import json
import os
import queue
import re
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from ctypes import wintypes

import tkinter as tk
from tkinter import font as tkfont

import pystray
from PIL import Image, ImageDraw, ImageFont, ImageTk

try:
    import psutil
except Exception:                                    # 没装 psutil 时网速功能自动隐藏
    psutil = None

try:
    from taskbar_strip import TaskbarStrip
except Exception:                                    # 新条不可用时回退 Tk 版
    TaskbarStrip = None

import usage_stats
import updater

APP_NAME = "中转管家"
APP_VERSION = "1.3.0"
HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
PROVIDERS_PATH = os.path.expanduser("~/.config/magpie/providers.json")
CCSWITCH_DB = os.path.expanduser("~/.cc-switch/cc-switch.db")

DEFAULT_CONFIG = {
    "provider": None,        # 留空则自动取 magpie 里第一个可用供应商
    "interval": 600,
    # 只在今日 token 有增长时才照这个间隔查余额；没用量就不白打接口
    "refresh_on_token": True,
    # 夜间暂停：这段时间不查额度（省中转调用，也让界面安静）
    "night_pause": True,
    "night_from": 21,        # 21:00 起
    "night_to": 8.5,         # 次日 08:30 止（可带半小时）
    "topmost": True,
    "x": None,
    "y": None,
    "autostart": False,
    # 自建账号（换 key 用）；active 为 None 时回退到 magpie 里的 provider
    "accounts": [],
    "active": None,
    # 周额度（公司按周发放，本周已用 = 周额度 − 总池剩余）
    "week_quota": 100.0,
    "week_key": None,          # 当前所属的周，如 2026-W44（仅用于显示）
    # 网速
    "net_panel": True,          # 主面板里显示上传/下载
    "panel_mem": True,          # 主面板里显示剩余内存
    "bar_mode": "taskbar",      # taskbar 贴任务栏 / float 悬浮条 / off 关闭
    "bar_dx": 0,                # 任务栏条水平微调
    "bar_dy": 0,                # 任务栏条垂直微调
    "bar_show_balance": True,   # 条上是否显示余额
    "bar_show_token": True,     # 条上是否显示今日 token
    "bar_show_mem": True,       # 条上是否显示内存占用百分比
    "bar_dark": True,           # 实底时的配色：深色 / 浅色
    "bar_transparent": True,    # 抠掉底色，只留文字，贴合任务栏
    "bar_stack": True,          # 上传/下载两行叠放，窄一些
    "bar_x": None,              # 悬浮条位置
    "bar_y": None,
}

INTERVALS = [
    (30, "30 秒"),
    (60, "1 分钟"),
    (120, "2 分钟"),
    (300, "5 分钟"),
    (600, "10 分钟"),
    (1800, "30 分钟"),
]

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "中转管家"

# 配色（深色卡片）
BG = "#15171c"
CARD = "#1b1e25"
LINE = "#262a33"
TEXT = "#e8eaed"
MUTED = "#8b93a1"
GREEN = "#34d399"
AMBER = "#f5b544"
RED = "#f87171"
TRACK = "#2a2f38"
UP = "#5aa9ff"       # 上传
DOWN = "#34d399"     # 下载


# ---------------------------------------------------------------- 配置

def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except Exception:
        pass
    return cfg


def fmt_hm(v):
    """21 -> 21:00，8.5 -> 08:30。"""
    h = int(v)
    m = int(round((float(v) - h) * 60))
    if m >= 60:
        h, m = h + 1, 0
    return f"{h:02d}:{m:02d}"


def in_night(cfg, now=None):
    """夜间暂停窗口，支持跨零点与半小时（默认 21:00–08:30）。"""
    if not cfg.get("night_pause", True):
        return False
    a = float(cfg.get("night_from", 21))
    b = float(cfg.get("night_to", 8.5))
    if a == b:
        return False
    now = now or datetime.datetime.now()
    t = now.hour + now.minute / 60.0
    return (t >= a or t < b) if a > b else (a <= t < b)


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ---------------------------------------------------------------- 数据

def find_provider(node, pid):
    if isinstance(node, dict):
        if node.get("id") == pid:
            return node
        for v in node.values():
            hit = find_provider(v, pid)
            if hit:
                return hit
    elif isinstance(node, list):
        for item in node:
            hit = find_provider(item, pid)
            if hit:
                return hit
    return None


def iter_providers(node):
    """遍历 magpie 配置，产出所有同时带 url 和 key 的供应商。"""
    if isinstance(node, dict):
        base = key = None
        for v in node.values():
            if isinstance(v, str) and v.startswith("http") and base is None:
                base = v.rstrip("/")
            elif isinstance(v, str) and v.startswith("sk-") and key is None:
                key = v
        if base and key:
            yield base, key
        for v in node.values():
            yield from iter_providers(v)
    elif isinstance(node, list):
        for item in node:
            yield from iter_providers(item)


def read_credentials(pid):
    with open(PROVIDERS_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    if not pid:                      # 没指定就挑第一个可用的
        for base, key in iter_providers(cfg):
            return norm_base(base), key
        raise RuntimeError("magpie 里没有可用的供应商")
    entry = find_provider(cfg, pid)
    if not entry:
        raise RuntimeError(f"magpie 里没有供应商 {pid}")
    base = key = None
    for v in entry.values():
        if isinstance(v, str) and v.startswith("http") and base is None:
            base = v.rstrip("/")
        elif isinstance(v, str) and v.startswith("sk-") and key is None:
            key = v
    if not base or not key:
        raise RuntimeError(f"{pid} 缺少 url 或 key")
    return norm_base(base), key


def norm_base(base):
    base = (base or "").strip().rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    return base


# ------------------------------------------------- CC Switch 配置读取

def url_host(base):
    """https://a.com/v1 → a.com，用于菜单里区分同名供应商。"""
    body = (base or "").split("//")[-1]
    return body.split("/")[0] or base


def looks_like_key(text):
    """sk-xxx 或 bigmodel 那种 32 位十六进制 + 点号后缀，排除模型名 / 地址。"""
    if not isinstance(text, str) or not 20 <= len(text) <= 200:
        return False
    if text.startswith("http") or " " in text or "/" in text:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9._\-]+", text))


def scan_pair(node, base=None, key=None):
    """通用兜底：在任意层级里找 http 地址和像 Key 的字符串。"""
    if isinstance(node, dict):
        for v in node.values():
            base, key = scan_pair(v, base, key)
    elif isinstance(node, list):
        for v in node:
            base, key = scan_pair(v, base, key)
    elif isinstance(node, str):
        if base is None and node.startswith("http"):
            base = node.strip().rstrip("/")
        elif key is None and looks_like_key(node):
            key = node
    return base, key


def extract_pair(app_type, conf):
    """从一条 CC Switch 供应商配置里取出 (base, key)。各家结构不一样。"""
    if not isinstance(conf, dict):
        return None, None
    env = conf.get("env") if isinstance(conf.get("env"), dict) else {}
    auth = conf.get("auth") if isinstance(conf.get("auth"), dict) else {}
    opts = conf.get("options") if isinstance(conf.get("options"), dict) else {}

    base = (env.get("ANTHROPIC_BASE_URL") or env.get("OPENAI_BASE_URL")
            or env.get("GEMINI_BASE_URL") or env.get("GOOGLE_GEMINI_BASE_URL")
            or opts.get("baseURL") or opts.get("baseUrl") or auth.get("base_url"))
    key = (env.get("ANTHROPIC_AUTH_TOKEN") or env.get("ANTHROPIC_API_KEY")
           or env.get("OPENAI_API_KEY") or env.get("GEMINI_API_KEY")
           or env.get("GOOGLE_API_KEY") or opts.get("apiKey")
           or auth.get("OPENAI_API_KEY"))
    if not base or not key:
        # codex 的地址写在 TOML 文本里：base_url = "https://..."
        text = conf.get("config")
        if isinstance(text, str):
            m = re.search(r'base_url\s*=\s*["\']([^"\']+)["\']', text)
            if m and not base:
                base = m.group(1)
    if not base or not key:
        b2, k2 = scan_pair(conf)
        base = base or b2
        key = key or k2
    if base and key:
        return norm_base(base), key
    return None, None


def ccswitch_providers():
    """读 CC Switch 本地库，列出所有同时带地址和 Key 的供应商。只读，不改库。"""
    if not os.path.exists(CCSWITCH_DB):
        raise RuntimeError("没找到 CC Switch 数据库")
    con = sqlite3.connect("file:%s?mode=ro" % CCSWITCH_DB.replace("\\", "/"), uri=True)
    try:
        rows = con.execute(
            "select app_type, name, settings_config, is_current from providers "
            "order by is_current desc, app_type, sort_index").fetchall()
    finally:
        con.close()
    out, seen = [], set()
    for app, name, raw, cur in rows:
        try:
            conf = json.loads(raw or "{}")
        except Exception:
            continue
        base, key = extract_pair(app, conf)
        if not base or not key or (base, key) in seen:
            continue
        seen.add((base, key))
        out.append({"app": app or "", "name": name or "", "base": base,
                    "key": key, "current": bool(cur)})
    return out



def active_account(cfg):
    name = cfg.get("active")
    if not name:
        return None
    for acc in cfg.get("accounts") or []:
        if acc.get("name") == name and acc.get("base") and acc.get("key"):
            return acc
    return None


def current_credentials(cfg):
    """优先用设置里选中的账号，没有则回退到 magpie。返回 (base, key, 来源名)。"""
    acc = active_account(cfg)
    if acc:
        return norm_base(acc["base"]), acc["key"], acc["name"]
    base, key = read_credentials(cfg.get("provider"))
    return base, key, f"magpie · {cfg.get('provider')}"


def mask_key(key):
    if not key:
        return "（空）"
    if len(key) <= 10:
        return key[:2] + "••••"
    return key[:5] + "••••••" + key[-4:]


def fmt_money(v, sign=True):
    """负数写成 -$1.23，而不是 $-1.23。"""
    v = float(v)
    body = f"${abs(v):,.2f}"
    return ("-" + body) if (sign and v < 0) else body


def api_get(base, key, path):
    req = urllib.request.Request(base + path, headers={"Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def week_key(ts=None):
    """ISO 周标识（周一为一周起点），如 2026-W44。"""
    t = time.localtime(ts) if ts else time.localtime()
    y, w, _ = datetime.date(t.tm_year, t.tm_mon, t.tm_mday).isocalendar()
    return f"{y}-W{w:02d}"


def query_balance(base, key):
    sub = api_get(base, key, "/v1/dashboard/billing/subscription")
    use = api_get(base, key, "/v1/dashboard/billing/usage")
    total = float(sub.get("hard_limit_usd") or 0)
    used = float(use.get("total_usage") or 0) / 100.0
    return {
        "total": total,
        "used": used,
        "left": total - used,
        "pct": used / total * 100 if total else 0.0,
        "at": time.time(),
    }


def fetch_balance(cfg):
    base, key, src = current_credentials(cfg)
    data = query_balance(base, key)
    data["source"] = src
    return data


# ---------------------------------------------------------------- 网速

class NetMeter:
    """系统整体上传 / 下载速率（字节每秒），排除回环接口。

    magpie 这类本地代理走 127.0.0.1，若不排除会把同一份流量算两遍。
    """

    def __init__(self):
        self.last = None
        self.up = 0.0
        self.down = 0.0

    @staticmethod
    def _counters():
        if psutil is None:
            return 0, 0
        try:
            per = psutil.net_io_counters(pernic=True)
        except Exception:
            return 0, 0
        sent = recv = 0
        for name, c in per.items():
            low = name.lower()
            if "loopback" in low or "teredo" in low or "isatap" in low:
                continue
            sent += c.bytes_sent
            recv += c.bytes_recv
        return sent, recv

    def sample(self):
        sent, recv = self._counters()
        now = time.monotonic()
        if self.last is None:
            self.last = (sent, recv, now)
            return self.up, self.down
        s0, r0, t0 = self.last
        dt = max(0.2, now - t0)
        self.last = (sent, recv, now)
        self.up = max(0.0, (sent - s0) / dt)
        self.down = max(0.0, (recv - r0) / dt)
        return self.up, self.down


def fmt_speed(v):
    if v < 1024:
        return f"{v:.0f} B/s"
    if v < 1024 ** 2:
        return f"{v / 1024:.1f} KB/s"
    return f"{v / 1024 ** 2:.2f} MB/s"


def mem_status():
    """(可用内存 GB, 已用百分比)。没装 psutil 时返回 (None, None)。"""
    if psutil is None:
        return None, None
    try:
        vm = psutil.virtual_memory()
        return vm.available / 1024 ** 3, vm.percent
    except Exception:
        return None, None


def fmt_mem(v):
    if v is None:
        return "—"
    if v < 1:
        return f"{v * 1024:.0f}M"
    if v < 10:
        return f"{v:.1f}G"
    return f"{v:.0f}G"


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


# ---------------------------------------------------------------- 主界面

class Widget:
    def __init__(self):
        self.cfg = load_config()
        self.data = None
        self.status = "正在读取…"
        self.ok = True
        self.visible = True
        self.busy = False
        self.busy_since = 0.0
        self.results = queue.Queue()
        self.icon = None
        self.net = NetMeter()
        self.strip = None
        self.settings = None
        self.week = None
        self.night_idle = False
        self.update_info = None      # 最近一次检查更新的结果
        self.updating = False        # 正在查 / 正在拉
        self.status_text = ""        # 状态栏当前文案，弹提示后用来还原
        self.status_color = MUTED
        self._flash_job = None
        self.last_refresh = 0.0      # 上次查余额的时间（节流用）
        self.tok_mark = None         # 上次查余额时的今日 token，用来判断有没有新用量

        ctypes.windll.shcore.SetProcessDpiAwareness(1)

        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.overrideredirect(True)
        self.root.configure(bg=BG)
        self.root.attributes("-topmost", bool(self.cfg["topmost"]))
        try:
            self.root.iconbitmap(os.path.join(HERE, "app.ico"))
        except Exception:
            pass

        self.w = 332
        self.root.geometry(f"{self.w}x400")
        self.f_zh = "Microsoft YaHei UI"
        self.build_ui()
        self.update_title()

        # 高度按内容实测，避免换字体 / 换 DPI 时底部被切
        self.root.update_idletasks()
        self.h = max(200, self.root.winfo_reqheight())

        x, y = self.cfg.get("x"), self.cfg.get("y")
        if x is None or y is None:
            sw = self.root.winfo_screenwidth()
            self.root.geometry(f"{self.w}x{self.h}+{sw - self.w - 48}+64")
        else:
            self.root.geometry(f"{self.w}x{self.h}+{int(x)}+{int(y)}")
        self.root.update_idletasks()

        self.bind_drag()
        self.round_corners()
        self.start_tray()
        self.apply_bar_mode(self.cfg.get("bar_mode", "taskbar"))
        self.refresh()
        self.root.after(200, self.tick)
        self.root.after(900, self.tick_net)
        self.root.after(8000, self.auto_check)

    def round_corners(self):
        round_window(self.root)

    # ---------------- UI

    def build_ui(self):
        outer = tk.Frame(self.root, bg=BG, highlightbackground=LINE, highlightthickness=1)
        outer.pack(fill="both", expand=True)

        head = tk.Frame(outer, bg=BG)
        head.pack(fill="x", padx=14, pady=(12, 0))

        self.dot = tk.Canvas(head, width=8, height=8, bg=BG, highlightthickness=0)
        self.dot.create_oval(0, 0, 8, 8, fill=MUTED, outline="")
        self.dot.pack(side="left", pady=(6, 0))

        self.lb_title = tk.Label(head, text=APP_NAME, bg=BG, fg=MUTED, font=(self.f_zh, 9))
        self.lb_title.pack(side="left", padx=(7, 0), pady=(3, 0))

        self.btn_close = tk.Label(head, text="✕", bg=BG, fg=MUTED, font=(self.f_zh, 10), cursor="hand2")
        self.btn_close.pack(side="right", pady=(2, 0))
        self.btn_close.bind("<Button-1>", lambda e: self.hide())

        self.btn_pin = tk.Label(
            head, text="置顶", bg=BG, fg=GREEN if self.cfg["topmost"] else MUTED,
            font=(self.f_zh, 9), cursor="hand2")
        self.btn_pin.pack(side="right", padx=(0, 12), pady=(4, 0))
        self.btn_pin.bind("<Button-1>", lambda e: self.toggle_topmost())

        self.btn_gear = tk.Label(head, text="⚙", bg=BG, fg=MUTED,
                                 font=(self.f_zh, 10), cursor="hand2")
        self.btn_gear.pack(side="right", padx=(0, 10), pady=(1, 0))
        self.btn_gear.bind("<Button-1>", lambda e: self.open_settings())
        self.btn_gear.bind("<Enter>", lambda e: self.btn_gear.config(fg=TEXT))
        self.btn_gear.bind("<Leave>", lambda e: self.btn_gear.config(fg=MUTED))

        # 手动刷新：小圆圈包一个 ↻，hover 变亮，刷新中变绿
        self.btn_refresh = tk.Canvas(head, width=20, height=20, bg=BG,
                                     highlightthickness=0, cursor="hand2")
        self._rf_ring = self.btn_refresh.create_oval(1, 1, 19, 19, outline="#3a4150", width=1)
        self._rf_txt = self.btn_refresh.create_text(10, 10, text="↻", fill=MUTED,
                                                   font=("Segoe UI Symbol", 9))
        self.btn_refresh.pack(side="right", padx=(0, 9), pady=(2, 0))
        self.btn_refresh.bind("<Button-1>", lambda e: self.refresh())
        self.btn_refresh.bind("<Enter>", lambda e: self._rf_style("hover"))
        self.btn_refresh.bind("<Leave>", lambda e: self._rf_style("idle"))

        # 发现新版本才出现：贴在刷新圈左边，点了直接更新并重启
        self.lb_update = tk.Label(head, text="可更新", bg=BG, fg=GREEN,
                                  font=(self.f_zh, 8), cursor="hand2")
        self.lb_update.pack(side="right", padx=(14, 8), pady=(4, 0),
                            after=self.btn_refresh)
        self.lb_update.pack_forget()
        self.lb_update.bind("<Button-1>", lambda e: self.on_update_click())
        self.lb_update.bind("<Enter>", self.on_update_hover)
        self.lb_update.bind("<Leave>", lambda e: self.lb_update.config(fg=GREEN))

        body = tk.Frame(outer, bg=BG)
        body.pack(fill="x", padx=16, pady=(8, 0))

        self.lb_left = tk.Label(body, text="—", bg=BG, fg=TEXT,
                                font=("Segoe UI Semibold", 26), anchor="w")
        self.lb_left.pack(side="left")

        self.lb_pct = tk.Label(body, text="", bg=BG, fg=MUTED,
                               font=(self.f_zh, 9), anchor="se")
        self.lb_pct.pack(side="right", pady=(0, 6))

        self.bar = tk.Canvas(outer, height=6, bg=BG, highlightthickness=0)
        self.bar.pack(fill="x", padx=16, pady=(6, 0))
        self.bar.bind("<Configure>", lambda e: self.draw_bar())

        self.lb_detail = tk.Label(outer, text="", bg=BG, fg=MUTED,
                                  font=(self.f_zh, 8), anchor="w")
        self.lb_detail.pack(fill="x", padx=16, pady=(8, 0))

        # 总额度（中转侧总额 / 已用 / 剩余）
        self.lb_pool = tk.Label(outer, text="", bg=BG, fg=MUTED,
                                font=(self.f_zh, 8), anchor="w")
        self.lb_pool.pack(fill="x", padx=16, pady=(3, 0))

        # 今日消耗的 token（5 秒一刷）
        self.lb_tok = tk.Label(outer, text="", bg=BG, fg=MUTED,
                               font=(self.f_zh, 8), anchor="w")
        self.lb_tok.pack(fill="x", padx=16, pady=(3, 0))

        # 剩余内存（跟 token 一起 5 秒一刷）
        self.lb_mem = tk.Label(outer, text="", bg=BG, fg=MUTED,
                               font=(self.f_zh, 8), anchor="w")
        self.lb_mem.pack(fill="x", padx=16, pady=(3, 0))

        # 实时网速：上传 / 下载
        self.net_row = tk.Frame(outer, bg=BG)
        self.lb_up = tk.Label(self.net_row, text="↑ —", bg=BG, fg=UP,
                              font=("Consolas", 9), anchor="w")
        self.lb_up.pack(side="left")
        self.lb_down = tk.Label(self.net_row, text="↓ —", bg=BG, fg=DOWN,
                                font=("Consolas", 9), anchor="e")
        self.lb_down.pack(side="right")

        foot = self.foot = tk.Frame(outer, bg=BG)
        foot.pack(fill="x", padx=16, pady=(7, 12))

        self.lb_status = tk.Label(foot, text="", bg=BG, fg=MUTED,
                                  font=(self.f_zh, 8), anchor="w")
        self.lb_status.pack(side="left")

        self.lb_interval = tk.Label(foot, text="", bg=BG, fg=MUTED,
                                    font=(self.f_zh, 8), anchor="e", cursor="hand2")
        self.lb_interval.pack(side="right")
        self.lb_interval.bind("<Button-1>", lambda e: self.popup_interval(e))

        self.build_menu()

        for w in (outer, body, head, foot, self.lb_left, self.net_row,
                  self.lb_detail, self.lb_pool, self.lb_tok, self.lb_mem,
                  self.lb_pct, self.lb_status):
            w.bind("<Button-3>", lambda e: self.menu.tk_popup(e.x_root, e.y_root))

        self.update_interval_label()
        self.apply_net_panel()
        self.apply_mem_panel()
        try:
            self.today_tok = usage_stats.today_live()
        except Exception:
            self.today_tok = {}
        self.render_tok()
        self.render_mem()
        self.root.after(1500, self.tick_usage)

    def build_menu(self):
        self.menu = tk.Menu(self.root, tearoff=0, bg=CARD, fg=TEXT,
                            activebackground="#2b303a", activeforeground=TEXT,
                            bd=0, font=(self.f_zh, 9))
        self.menu.add_command(label="立即刷新", command=self.refresh)
        for sec, label in INTERVALS:
            self.menu.add_command(label=f"每 {label}", command=lambda s=sec: self.set_interval(s))
        self.menu.add_separator()
        self.menu.add_cascade(label="网速条", menu=self.build_bar_menu(self.menu))
        self.menu.add_separator()
        self.menu.add_command(label="用量统计…", command=self.open_usage)
        self.menu.add_command(label="设置 / 换 Key", command=self.open_settings)
        self.menu.add_command(label=f"检查更新（v{APP_VERSION}）",
                              command=lambda: self.check_update(manual=True))
        self.menu.add_command(label="隐藏到托盘", command=self.hide)
        self.menu.add_command(label="退出", command=self.quit)

    def build_bar_menu(self, parent):
        m = tk.Menu(parent, tearoff=0, bg=CARD, fg=TEXT,
                    activebackground="#2b303a", activeforeground=TEXT,
                    bd=0, font=(self.f_zh, 9))
        mode = self.cfg.get("bar_mode", "taskbar")
        m.add_command(label=("● " if mode == "taskbar" else "○ ") + "贴任务栏显示",
                      command=lambda: self.apply_bar_mode("taskbar"))
        m.add_command(label=("● " if mode == "float" else "○ ") + "悬浮小条",
                      command=lambda: self.apply_bar_mode("float"))
        m.add_command(label=("● " if mode == "off" else "○ ") + "关闭网速条",
                      command=lambda: self.apply_bar_mode("off"))
        m.add_separator()
        m.add_command(label="左移", command=lambda: self.nudge_bar(-24, 0))
        m.add_command(label="右移", command=lambda: self.nudge_bar(24, 0))
        m.add_command(label="上移", command=lambda: self.nudge_bar(0, -6))
        m.add_command(label="下移", command=lambda: self.nudge_bar(0, 6))
        m.add_command(label="复位位置", command=self.reset_bar_pos)
        m.add_separator()
        m.add_command(label=("✓ " if self.cfg.get("bar_show_balance", True) else "　") + "条上显示余额",
                      command=lambda: self.set_cfg_bool("bar_show_balance"))
        m.add_command(label=("✓ " if self.cfg.get("bar_show_token", True) else "　") + "条上显示今日 token",
                      command=lambda: self.set_cfg_bool("bar_show_token"))
        m.add_command(label=("✓ " if self.cfg.get("bar_show_mem", True) else "　") + "条上显示内存占用",
                      command=lambda: self.set_cfg_bool("bar_show_mem"))
        m.add_command(label=("✓ " if self.cfg.get("bar_transparent", True) else "　") + "透明底",
                      command=lambda: self.set_cfg_bool("bar_transparent"))
        m.add_command(label=("✓ " if self.cfg.get("bar_stack", True) else "　") + "两行叠放",
                      command=lambda: self.set_cfg_bool("bar_stack"))
        m.add_command(label=("✓ " if self.cfg.get("bar_dark", True) else "　") + "实底深色",
                      command=lambda: self.set_cfg_bool("bar_dark"))
        m.add_command(label=("✓ " if self.cfg.get("net_panel", True) else "　") + "面板显示网速",
                      command=lambda: self.set_cfg_bool("net_panel"))
        m.add_command(label=("✓ " if self.cfg.get("panel_mem", True) else "　") + "面板显示内存",
                      command=lambda: self.set_cfg_bool("panel_mem"))
        return m

    def draw_bar(self):
        self.bar.delete("all")
        width = self.bar.winfo_width()
        if width <= 1:
            return
        pct = (self.week["pct"] if self.week else 0)
        pct = max(0.0, min(100.0, pct))
        self.bar.create_rectangle(0, 0, width, 6, fill=TRACK, outline="")
        fill_w = int(width * pct / 100.0)
        if fill_w > 0:
            color = GREEN if pct < 80 else (AMBER if pct < 95 else RED)
            self.bar.create_rectangle(0, 0, fill_w, 6, fill=color, outline="")

    def update_interval_label(self):
        if in_night(self.cfg):
            self.lb_interval.config(text="夜间暂停 ▾")
            return
        label = next((l for s, l in INTERVALS if s == self.cfg["interval"]), "10 分钟")
        self.lb_interval.config(text=f"每 {label}刷新 ▾")

    # ---------------- 网速

    def apply_net_panel(self):
        on = bool(self.cfg.get("net_panel", True)) and psutil is not None
        mapped = bool(self.net_row.winfo_ismapped())
        if on and not mapped:
            self.net_row.pack(fill="x", padx=16, pady=(6, 0), before=self.foot)
        elif not on and mapped:
            self.net_row.pack_forget()
        self.resize_height()

    def apply_mem_panel(self):
        on = bool(self.cfg.get("panel_mem", True)) and psutil is not None
        mapped = bool(self.lb_mem.winfo_ismapped())
        if on and not mapped:
            # 插在网速行之前，否则重新 pack 会掉到最底下
            anchor = self.net_row if self.net_row.winfo_ismapped() else self.foot
            self.lb_mem.pack(fill="x", padx=16, pady=(3, 0), before=anchor)
        elif not on and mapped:
            self.lb_mem.pack_forget()
        self.resize_height()

    def render_tok(self):
        t = getattr(self, "today_tok", None) or {}
        total = (t.get("in", 0) or 0) + (t.get("out", 0) or 0)
        if total:
            self.lb_tok.config(
                text=f"今日 {usage_stats.fmt_tokens(total)} tok · {t.get('req', 0):,} 次")
        else:
            self.lb_tok.config(text="今日 — tok")

    def render_mem(self):
        gb, pct = mem_status()
        if gb is None:
            self.lb_mem.config(text="内存 —")
            return
        color = MUTED
        if pct >= 90:
            color = RED
        elif pct >= 80:
            color = AMBER
        self.lb_mem.config(text=f"内存 {fmt_mem(gb)} 可用 · 已用 {pct:.0f}%", fg=color)

    def tick_usage(self):
        try:
            self.today_tok = usage_stats.today_live()
        except Exception:
            self.today_tok = {}
        for fn in (self.render_tok, self.render_mem):
            try:
                fn()
            except Exception:
                pass
        # 今日 token 只在真有用量时变；顺便决定要不要去查余额
        self.auto_refresh()
        self.root.after(5000, self.tick_usage)

    def tok_total(self):
        """今日 token 总量；统计还没读出来时返回 None。"""
        t = getattr(self, "today_tok", None)
        if not isinstance(t, dict):
            return None
        return int(t.get("in", 0) or 0) + int(t.get("out", 0) or 0)

    def auto_refresh(self):
        """有 token 变动才查余额，间隔仍是 interval 秒。

        没装 magpie / CC Switch（读不到用量）或手动关掉时，退回原来的定时刷新。
        """
        if self.busy or in_night(self.cfg):
            return
        interval = max(10, int(self.cfg["interval"]))
        if time.time() - self.last_refresh < interval:
            return
        if not self.cfg.get("refresh_on_token", True) or not usage_stats.has_source():
            self.refresh()
            return
        total = self.tok_total()
        if total is None or self.tok_mark is None or total != self.tok_mark:
            self.refresh()

    def toggle_token_refresh(self):
        self.cfg["refresh_on_token"] = not self.cfg.get("refresh_on_token", True)
        save_config(self.cfg)
        self.refresh_tray_menu()
        if not self.cfg["refresh_on_token"]:
            self.refresh()               # 关掉后马上补一次，别让人以为坏了

    def resize_height(self):
        try:
            self.root.update_idletasks()
        except Exception:
            return
        h = max(180, self.root.winfo_reqheight())
        if h != getattr(self, "h", None):
            self.h = h
            self.root.geometry(f"{self.w}x{self.h}")

    def render_net_panel(self):
        if not self.net_row.winfo_ismapped():
            return
        self.lb_up.config(text="↑ " + fmt_speed(self.net.up))
        self.lb_down.config(text="↓ " + fmt_speed(self.net.down))

    def tick_net(self):
        try:
            self.drain()
        except Exception:
            pass
        if self.net_row.winfo_ismapped() or self.strip is not None:
            self.net.sample()
            self.render_net_panel()
            if self.strip is not None:
                try:
                    self.strip.tick()
                except Exception:
                    pass
        self.root.after(1000, self.tick_net)

    # ---------------- 网速条

    def apply_bar_mode(self, mode):
        self.cfg["bar_mode"] = mode
        save_config(self.cfg)
        self.destroy_strip()
        if mode != "off" and psutil is not None:
            self.strip = self.make_strip(mode)
        self.build_menu()
        self.refresh_tray_menu()

    def make_strip(self, mode):
        """贴任务栏用纯 Win32 子窗口（挂进任务栏，不会被覆盖）；
        悬浮条还是用 Tk 的置顶小窗。"""
        if mode == "taskbar" and TaskbarStrip is not None:
            try:
                return TaskbarStrip(self, fmt_speed, fmt_money, mem_status)
            except Exception:
                pass
        try:
            return TrafficStrip(self, mode)
        except Exception:
            return None

    def destroy_strip(self):
        if self.strip is not None:
            try:
                self.strip.destroy()
            except Exception:
                pass
            self.strip = None

    def nudge_bar(self, dx, dy):
        if self.cfg.get("bar_mode") == "float":
            if self.strip is not None:
                x = self.strip.winfo_x() + dx
                y = self.strip.winfo_y() + dy
                self.cfg["bar_x"], self.cfg["bar_y"] = x, y
                self.strip.place()
        else:
            self.cfg["bar_dx"] = int(self.cfg.get("bar_dx") or 0) + dx
            self.cfg["bar_dy"] = int(self.cfg.get("bar_dy") or 0) + dy
            if self.strip is not None:
                self.strip.place()
        save_config(self.cfg)

    def reset_bar_pos(self):
        self.cfg["bar_dx"] = 0
        self.cfg["bar_dy"] = 0
        self.cfg["bar_x"] = None
        self.cfg["bar_y"] = None
        save_config(self.cfg)
        if self.strip is not None:
            self.strip.place()

    def set_week_quota(self, quota):
        self.cfg["week_quota"] = float(quota)
        save_config(self.cfg)
        self.repaint()

    def repaint(self):
        """用已有数据立刻重算面板，不等下一次联网。"""
        if self.data:
            self.apply(self.data, None)

    def set_cfg_bool(self, key):
        self.cfg[key] = not bool(self.cfg.get(key, True))
        save_config(self.cfg)
        if key == "net_panel":
            self.apply_net_panel()
        elif key == "panel_mem":
            self.apply_mem_panel()
        elif key.startswith("bar_") and self.strip is not None:
            try:
                if hasattr(self.strip, "refresh_layout"):
                    self.strip.refresh_layout()
                else:
                    self.strip._pos = None
                    self.strip.apply_theme()
                    self.strip.render()
                    self.strip.place()
            except Exception:
                pass
        self.build_menu()
        self.refresh_tray_menu()

    def update_title(self):
        acc = active_account(self.cfg)
        if not acc:
            self.lb_title.config(text=APP_NAME)
            return
        name = acc["name"]
        if len(name) > 9:
            name = name[:8] + "…"
        self.lb_title.config(text=f"{APP_NAME} · {name}")

    def open_settings(self):
        win = getattr(self, "settings", None)
        if win is not None and win.winfo_exists():
            win.lift()
            win.focus_force()
            return
        self.settings = SettingsWindow(self)

    def open_usage(self):
        win = getattr(self, "usage", None)
        if win is not None and win.winfo_exists():
            win.reload()
            win.lift()
            win.focus_force()
            return
        self.usage = UsageWindow(self)

    # ---------------- 拖拽

    def bind_drag(self):
        targets = [self.root]
        for w in self.root.winfo_children():
            targets.append(w)
            for c in w.winfo_children():
                targets.append(c)
        for w in targets:
            w.bind("<Button-1>", self.drag_start, add="+")
            w.bind("<B1-Motion>", self.drag_move, add="+")

    def drag_start(self, event):
        self._dx = event.x_root - self.root.winfo_x()
        self._dy = event.y_root - self.root.winfo_y()

    def drag_move(self, event):
        self.root.geometry(f"+{event.x_root - self._dx}+{event.y_root - self._dy}")

    # ---------------- 状态

    def set_topmost(self, on):
        self.cfg["topmost"] = bool(on)
        self.root.attributes("-topmost", bool(on))
        self.btn_pin.config(fg=GREEN if on else MUTED)
        save_config(self.cfg)
        self.refresh_tray_menu()

    def toggle_topmost(self):
        self.set_topmost(not self.cfg["topmost"])

    def set_interval(self, seconds):
        self.cfg["interval"] = int(seconds)
        save_config(self.cfg)
        self.update_interval_label()
        self.refresh_tray_menu()

    def refresh_tray_menu(self):
        try:
            self.icon.update_menu()
        except Exception:
            pass

    def popup_interval(self, event):
        self.menu.tk_popup(event.x_root, event.y_root)

    def hide(self):
        self.save_pos()
        win = getattr(self, "settings", None)
        if win is not None and win.winfo_exists():
            win.destroy()
        self.root.withdraw()
        self.visible = False

    def show(self):
        self.root.deiconify()
        self.root.attributes("-topmost", bool(self.cfg["topmost"]))
        self.visible = True

    def toggle_visible(self):
        if self.visible:
            self.hide()
        else:
            self.show()

    def save_pos(self):
        self.cfg["x"] = self.root.winfo_x()
        self.cfg["y"] = self.root.winfo_y()
        save_config(self.cfg)

    def quit(self):
        self.save_pos()
        self.destroy_strip()
        try:
            self.icon.stop()
        except Exception:
            pass
        self.root.destroy()

    # ---------------- 更新

    def auto_check(self):
        """启动后查一次，之后每 6 小时一次；夜间不查，改成半小时醒一次等天亮。"""
        if self.updating:
            delay = 10 * 60 * 1000
        elif in_night(self.cfg):
            delay = 30 * 60 * 1000
        else:
            self.check_update(manual=False)
            delay = 6 * 3600 * 1000
        self.root.after(delay, self.auto_check)

    def check_update(self, manual=False):
        if self.updating:
            return
        self.updating = True
        if manual:
            self.lb_status.config(text="检查更新…", fg=MUTED)
        self.refresh_tray_menu()
        threading.Thread(target=self._check_worker, args=(manual,), daemon=True).start()

    def _check_worker(self, manual):
        try:
            info = updater.check()
        except Exception as exc:                    # updater 内部已兜底，这里再保一层
            info = {"ok": False, "behind": 0, "can_pull": False,
                    "error": str(exc)[:40]}
        try:
            self.root.after(0, lambda: self.apply_update(info, manual))
        except Exception:
            pass

    def apply_update(self, info, manual=False):
        self.updating = False
        self.update_info = info
        if not info.get("ok"):
            self.hide_update_label()
            if manual:
                self.flash_status("检查更新失败：" + (info.get("error") or "未知"), AMBER)
        elif info.get("behind", 0) > 0:
            self.show_update_label(info)
            if manual:
                self.flash_status(f"↑ 可更新 {info['behind']} 个提交", GREEN)
        else:
            self.hide_update_label()
            if manual:
                self.flash_status(f"已是最新 v{APP_VERSION}", GREEN)
        self.refresh_tray_menu()

    def show_update_label(self, info):
        # 放在标题栏刷新圈左边，底部一行留给状态文字和刷新间隔
        try:
            self.lb_update.config(text="可更新")
            if not self.lb_update.winfo_ismapped():
                self.lb_update.pack(side="right", padx=(14, 8), pady=(4, 0),
                                    after=self.btn_refresh)
        except Exception:
            pass

    def update_hint(self):
        info = self.update_info or {}
        msg = (info.get("message") or "").strip()
        short = (msg[:16] + "…") if len(msg) > 16 else msg
        self.flash_status(f"{info.get('remote') or ''} {short}".strip(), GREEN)

    def on_update_hover(self, event=None):
        try:
            self.lb_update.config(fg=TEXT)
        except Exception:
            return
        if (self.update_info or {}).get("message"):
            self.update_hint()

    def hide_update_label(self):
        try:
            if self.lb_update.winfo_ismapped():
                self.lb_update.pack_forget()
        except Exception:
            pass

    def update_menu_text(self):
        if self.updating:
            return "检查更新中…"
        info = self.update_info
        if info and info.get("ok") and info.get("behind", 0) > 0:
            return f"↑ 更新到 {info.get('remote') or '最新'}"
        return "检查更新"

    def on_update_click(self):
        if self.updating:
            return
        info = self.update_info
        if not info or not info.get("ok") or info.get("behind", 0) <= 0:
            self.check_update(manual=True)          # 第一次点：先查
            return
        if not info.get("can_pull"):
            url = info.get("url") or updater.HOME_PAGE
            try:
                os.startfile(url)
            except Exception:
                import webbrowser
                webbrowser.open(url)
            self.flash_status("已打开 GitHub 页面", MUTED)
            return
        self.do_pull()

    def do_pull(self):
        self.updating = True
        self.lb_status.config(text="正在更新…", fg=MUTED)
        self.refresh_tray_menu()
        threading.Thread(target=self._pull_worker, daemon=True).start()

    def _pull_worker(self):
        try:
            ok, msg = updater.pull()
        except Exception as exc:
            ok, msg = False, str(exc)[:40]
        try:
            self.root.after(0, lambda: self.after_pull(ok, msg))
        except Exception:
            pass

    def after_pull(self, ok, msg):
        self.updating = False
        if ok:
            self.lb_status.config(text="更新完成，正在重启…", fg=GREEN)
            self.root.after(800, self.restart)
        else:
            self.flash_status("更新失败：" + msg[:24], RED, hold=6000)
        self.refresh_tray_menu()

    def flash_status(self, text, color=MUTED, hold=3600):
        """状态栏临时写一句话，过几秒还原成上次刷新时间。"""
        try:
            self.lb_status.config(text=text, fg=color)
        except Exception:
            return
        if self._flash_job:
            try:
                self.root.after_cancel(self._flash_job)
            except Exception:
                pass
        self._flash_job = self.root.after(hold, self.restore_status)

    def restore_status(self):
        self._flash_job = None
        if self.busy:
            return
        try:
            self.lb_status.config(text=self.status_text or "", fg=self.status_color)
        except Exception:
            pass

    def restart(self):
        """重启自己：先放掉单实例锁，拉起新进程，再退出。"""
        try:
            self.save_pos()
            self.destroy_strip()
            self.icon.stop()
        except Exception:
            pass
        exe = sys.executable
        if exe.lower().endswith("python.exe"):
            cand = exe[:-len("python.exe")] + "pythonw.exe"
            if os.path.exists(cand):
                exe = cand
        flags = 0
        if os.name == "nt":
            flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
                     | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        release_mutex()
        try:
            subprocess.Popen([exe, os.path.abspath(__file__)], cwd=HERE,
                             close_fds=True, creationflags=flags)
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass
        os._exit(0)

    # ---------------- 刷新

    def _rf_style(self, state):
        """刷新按钮的三态：平时淡、hover 亮、刷新中变绿。"""
        if self.busy and state != "busy":
            return
        ring, txt = {"idle": ("#3a4150", MUTED), "hover": (MUTED, TEXT),
                     "busy": (GREEN, GREEN)}.get(state, ("#3a4150", MUTED))
        try:
            self.btn_refresh.itemconfig(self._rf_ring, outline=ring)
            self.btn_refresh.itemconfig(self._rf_txt, fill=txt)
        except Exception:
            pass

    def refresh(self):
        if self.busy:
            return
        self.busy = True
        self.busy_since = time.time()
        self._rf_style("busy")
        self.lb_status.config(text="刷新中…", fg=MUTED)
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        """子线程只往队列里丢结果，绝不碰 Tk 组件。"""
        try:
            self.results.put((fetch_balance(self.cfg), None))
        except Exception as exc:
            self.results.put((None, str(exc)))

    def drain(self):
        """主线程每秒取一次结果；顺带做超时兜底，避免卡在「刷新中」。"""
        while True:
            try:
                data, err = self.results.get_nowait()
            except queue.Empty:
                break
            self.apply(data, err)
        if self.busy and time.time() - self.busy_since > 25:
            self.busy = False
            self._rf_style("idle")
            self.lb_status.config(text="查询超时，稍后重试", fg=AMBER)


    def apply(self, data, error):
        self.busy = False
        self.last_refresh = time.time()
        self._rf_style("idle")
        if error:
            self.ok = False
            self.dot.itemconfig(1, fill=RED)
            self.status_text = self.short_error(error)
            self.status_color = RED
            self.lb_status.config(text=self.status_text, fg=RED)
            if self.data is None:
                self.lb_left.config(text="—")
                self.lb_pct.config(text="")
                self.lb_detail.config(text="查不到数据")
                self.lb_pool.config(text="")
            return

        self.ok = True
        self.data = data
        quota = float(self.cfg.get("week_quota") or 100.0)
        pool = data["left"]                       # 总池剩余＝本周还能花的钱
        spent = max(0.0, quota - pool)            # 本周已用＝周额度 − 总池剩余
        pct = (spent / quota * 100) if quota else 0.0
        self.cfg["week_key"] = week_key()         # 只是让跨周检查不误触发
        week_left = quota - spent
        self.week = {"spent": spent, "quota": quota, "pct": pct,
                     "left": pool, "week_left": week_left}
        self.update_title()
        color = GREEN if pct < 80 else (AMBER if pct < 95 else RED)
        self.dot.itemconfig(1, fill=color)
        self.lb_left.config(text=fmt_money(pool), fg=RED if pool < 0 else TEXT)
        self.lb_pct.config(
            text=(f"本周 {pct:.0f}%" if pct < 1000 else "本周 >999%"), fg=color)
        detail_txt = f"本周 ${spent:,.2f} / ${quota:,.0f}"
        self.lb_detail.config(text=detail_txt)
        self.lb_pool.config(
            text=f"总额度 ${data['total']:,.2f}   ·   已用 ${data['used']:,.2f}")
        self.status_text = time.strftime("%H:%M:%S 刷新", time.localtime(data["at"]))
        self.status_color = MUTED
        self.lb_status.config(text=self.status_text, fg=MUTED)
        self.tok_mark = self.tok_total()
        self.draw_bar()
        if self.icon:
            try:
                self.icon.title = (f"{APP_NAME} · 余额 {fmt_money(pool)}"
                                   f"（本周 {pct:.0f}%）")
            except Exception:
                pass

    @staticmethod
    def short_error(msg):
        low = msg.lower()
        if "timed out" in low or "timeout" in low:
            return "超时，稍后再试"
        if "401" in low or "unauthorized" in low:
            return "密钥无效"
        if "http 404" in low:
            return "中转没有计费接口"
        if "no such file" in low or "找不到" in low or "没有供应商" in msg:
            return "读不到 magpie 配置"
        return msg[:42]

    def tick(self):
        delay = max(10, int(self.cfg["interval"])) * 1000
        if in_night(self.cfg):
            # 夜间不查额度；每 1 分钟醒一次，到点立刻恢复
            if not self.night_idle:
                self.night_idle = True
                self.lb_status.config(
                    text=f"夜间暂停 {fmt_hm(self.cfg['night_from'])}–{fmt_hm(self.cfg['night_to'])}",
                    fg=MUTED)
                self.update_interval_label()
            delay = min(delay, 60000)
        else:
            if self.night_idle:
                self.night_idle = False
                self.update_interval_label()
            self.auto_refresh()
        self.root.after(delay, self.tick)

    # ---------------- 托盘

    def start_tray(self):
        def set_interval(sec):
            self.root.after(0, lambda: self.set_interval(sec))

        def toggle_top(item):
            self.root.after(0, self.toggle_topmost)

        def toggle_show(item):
            self.root.after(0, self.toggle_visible)

        def autostart(item):
            self.root.after(0, self.toggle_autostart)

        def quit_app(item):
            self.root.after(0, self.quit)

        def bar_mode(mode):
            self.root.after(0, lambda: self.apply_bar_mode(mode))

        def bar_menu():
            return pystray.Menu(
                pystray.MenuItem("贴任务栏", lambda i: bar_mode("taskbar"),
                                 checked=lambda i: self.cfg.get("bar_mode") == "taskbar",
                                 radio=True),
                pystray.MenuItem("悬浮条", lambda i: bar_mode("float"),
                                 checked=lambda i: self.cfg.get("bar_mode") == "float",
                                 radio=True),
                pystray.MenuItem("关闭", lambda i: bar_mode("off"),
                                 checked=lambda i: self.cfg.get("bar_mode") == "off",
                                 radio=True),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("左移", lambda i: self.root.after(0, lambda: self.nudge_bar(-24, 0))),
                pystray.MenuItem("右移", lambda i: self.root.after(0, lambda: self.nudge_bar(24, 0))),
                pystray.MenuItem("上移", lambda i: self.root.after(0, lambda: self.nudge_bar(0, -6))),
                pystray.MenuItem("下移", lambda i: self.root.after(0, lambda: self.nudge_bar(0, 6))),
            )

        def interval_menu():
            return pystray.Menu(*[
                pystray.MenuItem(
                    label,
                    (lambda s: (lambda item: set_interval(s)))(sec),
                    checked=(lambda s: (lambda item: self.cfg["interval"] == s))(sec),
                    radio=True,
                )
                for sec, label in INTERVALS
            ])

        menu = pystray.Menu(
            pystray.MenuItem(lambda item: "隐藏面板" if self.visible else "显示面板",
                             toggle_show, default=True),
            pystray.MenuItem("立即刷新", lambda item: self.root.after(0, self.refresh)),
            pystray.MenuItem("用量统计…", lambda item: self.root.after(0, self.open_usage)),
            pystray.MenuItem("设置 / 换 Key", lambda item: self.root.after(0, self.open_settings)),
            pystray.MenuItem(lambda item: self.update_menu_text(),
                             lambda item: self.root.after(0, self.on_update_click)),
            pystray.MenuItem("刷新间隔", interval_menu()),
            pystray.MenuItem(
                f"夜间暂停 {fmt_hm(self.cfg['night_from'])}–{fmt_hm(self.cfg['night_to'])}",
                lambda item: self.root.after(0, self.toggle_night),
                checked=lambda item: bool(self.cfg.get("night_pause", True))),
            pystray.MenuItem(
                "有用量才查余额",
                lambda item: self.root.after(0, self.toggle_token_refresh),
                checked=lambda item: bool(self.cfg.get("refresh_on_token", True))),
            pystray.MenuItem("网速条", bar_menu()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("窗口置顶", toggle_top,
                             checked=lambda item: self.cfg["topmost"]),
            pystray.MenuItem("开机自启", autostart,
                             checked=lambda item: self.is_autostart()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", quit_app),
        )

        self.icon = pystray.Icon("relay_balance", tray_image(), APP_NAME, menu)
        self.icon.run_detached()

    def toggle_night(self):
        self.cfg["night_pause"] = not self.cfg.get("night_pause", True)
        save_config(self.cfg)
        if not self.cfg["night_pause"]:
            if self.night_idle:
                self.night_idle = False
                self.update_interval_label()
            self.refresh()
        elif in_night(self.cfg):
            self.night_idle = False          # 让下一次 tick 重新写上「夜间暂停」

    def is_autostart(self):
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
                winreg.QueryValueEx(k, RUN_NAME)
            return True
        except Exception:
            return False

    def toggle_autostart(self):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                                winreg.KEY_SET_VALUE) as k:
                if self.is_autostart():
                    winreg.DeleteValue(k, RUN_NAME)
                else:
                    pyw = sys.executable.replace("python.exe", "pythonw.exe")
                    winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ,
                                      f'"{pyw}" "{os.path.abspath(__file__)}"')
        except Exception:
            pass
        try:
            self.icon.update_menu()
        except Exception:
            pass

    def run(self):
        self.root.mainloop()


# ---------------------------------------------------------------- 网速条

FONT_MONO = r"C:\Windows\Fonts\consola.ttf"
FONT_MONO_BOLD = r"C:\Windows\Fonts\consolab.ttf"
FONT_UI = r"C:\Windows\Fonts\msyh.ttc"
KEY_COLOR = "#ff00fe"          # 透明色key（几乎不会用到）


def load_font(size):
    """等宽加粗字体，取不到就退回普通体。"""
    for path in (FONT_MONO_BOLD, FONT_MONO):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


class TrafficStrip(tk.Toplevel):
    """贴在任务栏上的实时网速条。

    Win11 任务栏是 XAML 合成层，SetParent 进去会被盖住，所以这里做成
    无边框、不抢焦点、始终置顶的独立小窗，直接压在任务栏对应位置。
    """

    def __init__(self, app, mode="taskbar"):
        super().__init__(app.root)
        self.app = app
        self.cfg = app.cfg
        self.mode = mode
        self.hwnd = None
        self._img = None
        self._pos = None
        self._transparent = False

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        try:
            self.attributes("-transparentcolor", KEY_COLOR)
            self._transparent = True
        except Exception:
            pass

        self.apply_theme()
        self.render()
        self.place()
        self.hwnd = as_tool_window(self)
        if self.mode == "taskbar":
            raise_over_taskbar(self.hwnd)
            self._guard_hits = 0
            self._guard()

    # ---------------- 外观

    def apply_theme(self):
        self.transparent = bool(self.cfg.get("bar_transparent", True)) and self._transparent
        self.light = taskbar_is_light()
        if self.transparent:
            # 抠掉底色，只留文字；key 色取接近任务栏的颜色，抗锯齿边缘不突兀
            self.key_color = "#f3f4f6" if self.light else "#1f2229"
            self.bg = self.key_color
            if self.light:
                self.fg, self.c_up, self.c_down = "#1a1d23", "#2563eb", "#059669"
            else:
                self.fg, self.c_up, self.c_down = "#e8eaed", "#5aa9ff", "#34d399"
            self.c_bal = self.fg
            self.c_good, self.c_warn, self.c_bad = (
                ("#059669", "#b45309", "#dc2626") if self.light else ("#34d399", AMBER, RED))
            try:
                self.attributes("-transparentcolor", self.key_color)
            except Exception:
                self.transparent = False
        if not self.transparent:
            self.key_color = KEY_COLOR
            dark = bool(self.cfg.get("bar_dark", True))
            if dark:
                self.bg, self.fg = BG, "#e8eaed"      # 跟右键菜单同一底色
                self.c_up, self.c_down, self.c_bal = "#5aa9ff", "#34d399", "#e8eaed"
                self.c_good, self.c_warn, self.c_bad = "#34d399", AMBER, RED
            else:
                self.bg, self.fg = "#f4f5f7", "#1a1d23"
                self.c_up, self.c_down, self.c_bal = "#2563eb", "#059669", "#111827"
                self.c_good, self.c_warn, self.c_bad = "#059669", "#b45309", "#dc2626"
        try:
            self.configure(bg=self.bg)      # 窗口自身底色也跟上，resize 瞬间不露馅
        except Exception:
            pass

        if self.mode == "taskbar":
            m = taskbar_metrics()
            self.h = m[2] if m else 40
        else:
            self.h = 34
        self.stack = bool(self.cfg.get("bar_stack", True))
        size = max(12, int(self.h * (0.30 if self.stack else 0.34)))
        self.f = load_font(size)

        self.pad = max(8, int(self.h * (0.22 if self.stack else 0.26)))
        self.gap = max(8, int(self.h * 0.28))
        # 用最宽的可能值定宽度，数字变化时不会抖动
        up_w = self.f.getlength("↑ 999.9 MB/s")
        dn_w = self.f.getlength("↓ 999.9 MB/s")
        if self.stack:
            net_w = max(up_w, dn_w)
        else:
            net_w = up_w + self.gap + dn_w
        w = self.pad * 2 + net_w
        if self.cfg.get("bar_show_balance", True):
            w += self.gap + self.f.getlength("$9,999.99")
        self.w = int(w) + 4

    def _segments(self):
        segs = [("↑ " + fmt_speed(self.app.net.up), self.c_up),
                ("↓ " + fmt_speed(self.app.net.down), self.c_down)]
        if self.cfg.get("bar_show_balance", True):
            w = self.app.week
            if w:
                segs.append((fmt_money(w["left"]), self.c_color(w["pct"])))
            else:
                segs.append(("$—", self.c_bal))
        return segs

    def c_color(self, pct):
        """和面板进度条同一套阈值：<80 绿、<95 琥珀、≥95 红。"""
        if pct >= 95:
            return self.c_bad
        if pct >= 80:
            return self.c_warn
        return self.c_good

    def render(self):
        size = (self.w, self.h)
        base = self.key_color if self.transparent else self.bg
        if getattr(self, "_photo", None) is None:
            self._pil = Image.new("RGB", size, base)
            self._photo = ImageTk.PhotoImage(self._pil)
            self.label = tk.Label(self, image=self._photo, bd=0,
                                  bg=base, highlightthickness=0)
            self.label.pack(fill="both", expand=True)
            self.bind_events()
        elif self._pil.size != size:
            self._pil = Image.new("RGB", size, base)
            self._photo = ImageTk.PhotoImage(self._pil)
            self.label.config(image=self._photo)
        self.label.config(bg=base)

        d = ImageDraw.Draw(self._pil)
        d.rectangle([0, 0, self.w - 1, self.h - 1], fill=base)
        if not self.transparent:
            inset = max(2, int(self.h * 0.08))
            radius = max(4, int(self.h * 0.22))
            d.rounded_rectangle([0, inset, self.w - 1, self.h - inset - 1],
                                radius=radius, fill=self.bg)
        cy = self.h // 2
        segs = self._segments()
        nets, bal = segs[:2], (segs[2] if len(segs) > 2 else None)
        x = self.pad
        if self.stack:
            dy = max(6, int(self.h * 0.215))
            for i, (text, color) in enumerate(nets):
                d.text((x, cy - dy + i * dy * 2), text, font=self.f,
                       fill=color, anchor="lm")
            x += max(self.f.getlength(t) for t, _ in nets) + self.gap
            if bal:
                d.text((x, cy), bal[0], font=self.f, fill=bal[1], anchor="lm")
        else:
            for text, color in segs:
                d.text((x, cy), text, font=self.f, fill=color, anchor="lm")
                x += self.f.getlength(text) + self.gap
        self._photo.paste(self._pil)

    # ---------------- 位置

    def place(self):
        if self.mode == "taskbar":
            m = taskbar_metrics()
            if not m:
                return
            top, right, th = m
            if th != self.h:
                self.apply_theme()
                self.render()
            x = right - self.w - 8 + int(self.cfg.get("bar_dx") or 0)
            y = top + int(self.cfg.get("bar_dy") or 0)
        else:
            x, y = self.cfg.get("bar_x"), self.cfg.get("bar_y")
            if x is None or y is None:
                x = self.winfo_screenwidth() - self.w - 80
                y = self.winfo_screenheight() - 160
            x, y = int(x), int(y)
        if self._pos == (x, y, self.w, self.h):
            return
        self._pos = (x, y, self.w, self.h)
        self.geometry(f"{self.w}x{self.h}+{x}+{y}")

    def tick(self):
        self.render()
        self.place()
        if self.mode == "taskbar":
            self._n = getattr(self, "_n", 0) + 1
            if self._n % 5 == 1:        # 别每帧都抢 band，够用且不闪
                raise_over_taskbar(self.hwnd)
        # 遮挡检测交给 _guard 高频跑，这里不管

    def _guard(self):
        """高频保活：鼠标悬停任务栏图标时，Windows 弹缩略图预览并把任务栏
        重新提到最顶层，贴在任务栏上的条会被压下去。主 tick 一秒才查一次，
        那一段就是肉眼看到的「短暂消失」。这里用 200ms 查一次，刚被压过
        之后连续几次提到 70ms，闪烁基本看不出来。
        """
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        try:
            if window_covered(self.hwnd):
                raise_over_taskbar(self.hwnd, force_top=True)
                self._guard_hits = 8
            elif self._guard_hits > 0:
                self._guard_hits -= 1
        except Exception:
            pass
        delay = 70 if self._guard_hits > 0 else 200
        try:
            self.after(delay, self._guard)
        except Exception:
            pass

    # ---------------- 交互

    def bind_events(self):
        targets = [self, getattr(self, "label", None)]
        for w in targets:
            if w is None:
                continue
            w.bind("<Button-3>", self.popup)
            w.bind("<Double-Button-1>", lambda e: self.app.show())
            w.bind("<Button-1>", self.drag_start)
            w.bind("<B1-Motion>", self.drag_move)
            w.bind("<ButtonRelease-1>", self.drag_end)

    def popup(self, event):
        self.app.build_menu()
        self.app.menu.tk_popup(event.x_root, event.y_root)

    def drag_start(self, event):
        self._dx = event.x_root - self.winfo_x()
        self._dy = event.y_root - self.winfo_y()

    def drag_move(self, event):
        if self.mode != "float":
            return
        self._pos = None
        self.geometry(f"+{event.x_root - self._dx}+{event.y_root - self._dy}")

    def drag_end(self, event):
        if self.mode != "float":
            return
        self.cfg["bar_x"] = self.winfo_x()
        self.cfg["bar_y"] = self.winfo_y()
        save_config(self.cfg)
        self._pos = None


# ---------------------------------------------------------------- 设置窗口

class SettingsWindow(tk.Toplevel):
    """换 Key：管理多组「名称 / 地址 / Key」，选中即切换。"""

    W = 462

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.cfg = app.cfg
        self.f = app.f_zh
        self.current = None
        self.accounts = [dict(a) for a in (self.cfg.get("accounts") or [])]

        self.overrideredirect(True)
        self.configure(bg=BG)
        self.attributes("-topmost", bool(self.cfg["topmost"]))
        self.build()
        self.place_near()
        round_window(self)
        self.bind("<Escape>", lambda e: self.destroy())
        self.reload_list(select=self.cfg.get("active"))
        if not self.accounts:
            # 没有任何自建账号时，先用 magpie 的配置填好，改 Key 就能存
            self.load_from_magpie()

    # ---------------- 布局

    def build(self):
        outer = tk.Frame(self, bg=BG, highlightbackground=LINE, highlightthickness=1)
        outer.pack(fill="both", expand=True)

        head = tk.Frame(outer, bg=BG)
        head.pack(fill="x", padx=14, pady=(12, 0))
        tk.Label(head, text="设置 · 换 Key", bg=BG, fg=TEXT,
                 font=(self.f, 10, "bold")).pack(side="left")
        close = tk.Label(head, text="✕", bg=BG, fg=MUTED, font=(self.f, 10), cursor="hand2")
        close.pack(side="right")
        close.bind("<Button-1>", lambda e: self.destroy())

        tk.Label(outer, text=f"Key 只存在本机 config.json，不会上传　·　v{APP_VERSION}",
                 bg=BG, fg=MUTED, font=(self.f, 8), anchor="w").pack(fill="x", padx=14, pady=(2, 0))

        wrap = tk.Frame(outer, bg=LINE, padx=1, pady=1)
        wrap.pack(fill="x", padx=14, pady=(10, 0))
        self.lst = tk.Listbox(wrap, bg=CARD, fg=TEXT, selectbackground="#2b303a",
                              selectforeground=TEXT, font=(self.f, 9), bd=0,
                              highlightthickness=0, activestyle="none", height=3,
                              exportselection=False)
        self.lst.pack(fill="both", expand=True)
        self.lst.bind("<<ListboxSelect>>", self.on_pick)
        self.lst.bind("<Double-Button-1>", lambda e: self.save())
        self.lst.bind("<MouseWheel>",
                      lambda e: self.lst.yview_scroll(-1 * (e.delta // 120), "units"))

        form = tk.Frame(outer, bg=BG)
        form.pack(fill="x", padx=14, pady=(10, 0))
        form.columnconfigure(1, weight=1)

        self.e_name = self.field(form, 0, "名称")
        self.e_name.configure(fg=TEXT)
        self.e_base = self.field(form, 1, "地址")
        self.e_key = self.field(form, 2, "Key", show="•")
        self.e_key.bind("<Return>", lambda e: self.save())

        self.lb_show = tk.Label(form, text="显示", bg=BG, fg=MUTED,
                                font=(self.f, 8), cursor="hand2")
        self.lb_show.grid(row=2, column=2, padx=(6, 0))
        self.lb_show.bind("<Button-1>", lambda e: self.toggle_show())

        row1 = tk.Frame(outer, bg=BG)
        row1.pack(fill="x", padx=14, pady=(10, 0))
        self.pill(row1, "读 magpie", self.load_from_magpie).pack(side="left")
        self.pill(row1, "读 CC Switch", self.load_from_ccswitch).pack(side="left", padx=(6, 0))
        self.pill(row1, "清空", self.new_account).pack(side="left", padx=(6, 0))
        self.pill(row1, "测试连接", self.test).pack(side="right")

        qrow = tk.Frame(outer, bg=BG)
        qrow.pack(fill="x", padx=14, pady=(9, 0))
        tk.Label(qrow, text="每周额度 $", bg=BG, fg=MUTED,
                 font=(self.f, 9)).pack(side="left")
        qbox = tk.Frame(qrow, bg="#0f1115", highlightbackground=LINE, highlightthickness=1)
        qbox.pack(side="left", padx=(6, 0))
        self.e_quota = tk.Entry(qbox, bg="#0f1115", fg=TEXT, insertbackground=TEXT,
                                relief="flat", bd=0, font=(self.f, 9),
                                highlightthickness=0, width=7)
        self.e_quota.pack(padx=8, pady=5)
        self.e_quota.bind("<FocusIn>", lambda ev: qbox.config(highlightbackground=GREEN))
        self.e_quota.bind("<FocusOut>", lambda ev: qbox.config(highlightbackground=LINE))
        self.set_entry(self.e_quota, f"{float(self.cfg.get('week_quota') or 100):g}")
        self.e_quota.bind("<Return>", self.apply_week_cfg)

        wrow = tk.Frame(outer, bg=BG)
        wrow.pack(fill="x", padx=14, pady=(6, 0))
        tk.Label(wrow, text="本周已用 $", bg=BG, fg=MUTED,
                 font=(self.f, 9)).pack(side="left")
        wbox = tk.Frame(wrow, bg="#0f1115", highlightbackground=LINE, highlightthickness=1)
        wbox.pack(side="left", padx=(6, 0))
        self.e_spent = tk.Entry(wbox, bg="#0f1115", fg=MUTED, insertbackground=TEXT,
                                readonlybackground="#0f1115", state="readonly",
                                relief="flat", bd=0, font=(self.f, 9),
                                highlightthickness=0, width=7)
        self.e_spent.pack(padx=8, pady=5)
        spent = self.app.week["spent"] if self.app.week else 0.0
        self.set_entry(self.e_spent, f"{spent:.2f}")
        tk.Label(wrow, text="额度 − 总池剩余", bg=BG, fg=MUTED,
                 font=(self.f, 8)).pack(side="right")

        self.lb_msg = tk.Label(outer, text="", bg=BG, fg=MUTED, font=(self.f, 8), anchor="w")
        self.lb_msg.pack(fill="x", padx=14, pady=(8, 0))

        foot = tk.Frame(outer, bg=BG)
        foot.pack(fill="x", padx=14, pady=(8, 12))
        self.pill(foot, "删除", self.delete, fg=RED).pack(side="left")
        self.save_label = self.pill(foot, "保存并使用", self.save,
                                    fg="#0f1115", bg=GREEN)
        self.save_label.pack(side="right")

        for w in (outer, head):
            w.bind("<Button-1>", self.drag_start)
            w.bind("<B1-Motion>", self.drag_move)

        self.update_idletasks()
        self.lst.config(height=max(1, min(4, len(self.accounts) or 1)))

    def field(self, parent, row, label, show=None):
        tk.Label(parent, text=label, bg=BG, fg=MUTED, font=(self.f, 9),
                 anchor="w").grid(row=row, column=0, sticky="w", pady=4, padx=(0, 9))
        box = tk.Frame(parent, bg="#0f1115", highlightbackground=LINE,
                       highlightthickness=1)
        box.grid(row=row, column=1, sticky="we", pady=4)
        e = tk.Entry(box, bg="#0f1115", fg=TEXT, insertbackground=TEXT,
                     relief="flat", bd=0, font=(self.f, 9), highlightthickness=0)
        if show:
            e.config(show=show)
        e.pack(fill="x", padx=8, pady=6)
        e.bind("<FocusIn>", lambda ev: box.config(highlightbackground=GREEN))
        e.bind("<FocusOut>", lambda ev: box.config(highlightbackground=LINE))
        return e

    def pill(self, parent, text, cmd, fg=TEXT, bg=CARD):
        lb = tk.Label(parent, text=text, bg=bg, fg=fg, font=(self.f, 9),
                      padx=12, pady=5, cursor="hand2")
        dark = fg == "#0f1115"
        lb.bind("<Button-1>", lambda e: cmd())
        lb.bind("<Enter>", lambda e: lb.config(bg="#3ee5ac" if dark else "#2b303a"))
        lb.bind("<Leave>", lambda e: lb.config(bg=bg))
        return lb

    def place_near(self):
        self.update_idletasks()
        h = self.winfo_reqheight()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        rx, ry = self.app.root.winfo_x(), self.app.root.winfo_y()
        x = rx - self.W - 14
        if x < 8:
            x = rx + self.app.w + 14
        if x + self.W > sw - 8:
            x = max(8, (sw - self.W) // 2)
        y = max(8, min(ry, sh - h - 80))
        self.geometry(f"{self.W}x{h}+{x}+{y}")
        self.update_idletasks()

    def drag_start(self, event):
        self._dx = event.x_root - self.winfo_x()
        self._dy = event.y_root - self.winfo_y()

    def drag_move(self, event):
        self.geometry(f"+{event.x_root - self._dx}+{event.y_root - self._dy}")

    # ---------------- 交互

    def set_entry(self, entry, value):
        try:
            ro = entry.cget("state") == "readonly"
        except Exception:
            ro = False
        if ro:
            entry.config(state="normal")
        entry.delete(0, "end")
        entry.insert(0, value or "")
        if ro:
            entry.config(state="readonly")

    def toggle_show(self):
        self.shown = not getattr(self, "shown", False)
        self.e_key.config(show="" if self.shown else "•")
        self.lb_show.config(text="隐藏" if self.shown else "显示",
                            fg=TEXT if self.shown else MUTED)

    def msg(self, text, color=MUTED):
        try:
            self.lb_msg.config(text=text, fg=color)
        except Exception:
            pass

    def reload_list(self, select=None):
        act = self.cfg.get("active")
        self.lst.delete(0, "end")
        if not self.accounts:
            self.lst.insert("end", "  还没有账号 · 点下方「读 magpie / 读 CC Switch」")
            self.lst.itemconfig(0, fg=MUTED)
            self.lst.config(height=1)
            self.fit_height()
            return
        for acc in self.accounts:
            mark = "●" if acc["name"] == act else "○"
            nm = acc["name"] if len(acc["name"]) <= 20 else acc["name"][:19] + "…"
            self.lst.insert("end", f"  {mark}  {nm}      {mask_key(acc['key'])}")
        self.lst.config(height=max(1, min(4, len(self.accounts))))
        idx = next((i for i, a in enumerate(self.accounts) if a["name"] == select), None)
        if idx is not None:
            self.lst.selection_clear(0, "end")
            self.lst.selection_set(idx)
            self.lst.see(idx)
            self.fill_form(self.accounts[idx])
        self.fit_height()

    def fit_height(self):
        """列表行数变了要跟着改窗高，否则底部提示会被切掉。"""
        try:
            self.update_idletasks()
            h = self.winfo_reqheight()
            self.geometry(f"{self.W}x{h}+{self.winfo_x()}+{self.winfo_y()}")
            round_window(self)
        except Exception:
            pass

    def on_pick(self, event=None):
        sel = self.lst.curselection()
        if not sel or sel[0] >= len(self.accounts):
            return
        self.fill_form(self.accounts[sel[0]])

    def fill_form(self, acc):
        self.current = acc.get("name")
        self.set_entry(self.e_name, acc.get("name"))
        self.set_entry(self.e_base, acc.get("base"))
        self.set_entry(self.e_key, acc.get("key"))
        self.msg("")

    def new_account(self):
        self.current = None
        for e in (self.e_name, self.e_base, self.e_key):
            self.set_entry(e, "")
        self.lst.selection_clear(0, "end")
        self.msg("填好后点「保存并使用」")

    def load_from_magpie(self):
        try:
            base, key = read_credentials(self.cfg.get("provider"))
        except Exception as exc:
            self.msg(f"读取失败：{exc}", RED)
            return
        self.current = None
        self.set_entry(self.e_name, self.cfg.get("provider") or "magpie")
        self.set_entry(self.e_base, base)
        self.set_entry(self.e_key, key)
        self.msg("已填入 magpie 的当前配置，保存后即独立")

    def load_from_ccswitch(self):
        """把 CC Switch 里所有带地址和 Key 的供应商导入账号列表。"""
        try:
            items = ccswitch_providers()
        except Exception as exc:
            self.msg(f"读取失败：{exc}", RED)
            return
        if not items:
            self.msg("CC Switch 里没有带地址和 Key 的供应商", RED)
            return

        added, pick = 0, None
        for it in items:
            base, key = it["base"], it["key"]
            same = next((a for a in self.accounts if norm_base(a.get("base")) == base), None)
            if same:                             # 同一个地址已导入过，只同步 Key
                same["key"] = key
                pick = pick or same["name"]
                continue
            name = it["name"] or "cc-switch"
            if any(a.get("name") == name for a in self.accounts):
                name = f"{name} ({url_host(base)})"      # 重名补域名区分
            n = 2
            while any(a.get("name") == name for a in self.accounts):
                name = f"{it['name']} ({url_host(base)} {n})"
                n += 1
            self.accounts.append({"name": name, "base": base, "key": key})
            added += 1
            if it["current"] and pick is None:
                pick = name
        pick = pick or self.accounts[-1]["name"]
        self.reload_list(select=pick)
        if added:
            self.msg(f"已从 CC Switch 导入 {added} 组，选好点「保存并使用」", GREEN)
        else:
            self.msg("CC Switch 的配置都已在列表里，Key 已同步")

    def test(self):
        base = norm_base(self.e_base.get())
        key = self.e_key.get().strip()
        if not base or not key:
            self.msg("先填地址和 Key", RED)
            return
        self.msg("测试中…")

        def work():
            try:
                d = query_balance(base, key)
                text = f"✓ 可用 · 总额 ${d['total']:,.2f} · 剩余 ${d['left']:,.2f}"
                color = GREEN
            except Exception as exc:
                text, color = f"✗ {self.app.short_error(str(exc))}", RED
            self.after(0, lambda: self.msg(text, color))

        threading.Thread(target=work, daemon=True).start()

    def apply_week_cfg(self, event=None):
        """回车即生效，不用改账号也能调额度。"""
        note = self.apply_week_inputs(silent=True)
        if note:
            self.msg("已生效  ·  " + note, GREEN)
            self.app.refresh()

    def apply_week_inputs(self, silent=False):
        """把「每周额度」落到配置并立即重绘。返回变更摘要。"""
        notes = []
        try:
            q = float(self.e_quota.get().strip() or 0)
            if q > 0 and abs(q - float(self.cfg.get("week_quota") or 0)) > 1e-9:
                self.app.set_week_quota(q)
                notes.append(f"额度 ${q:,.0f}")
        except Exception:
            if not silent:
                self.msg("每周额度要填数字", RED)
                return ""
        self.app.repaint()
        spent = self.app.week["spent"] if self.app.week else 0.0
        self.set_entry(self.e_spent, f"{spent:.2f}")
        return "，".join(notes)

    def save(self):
        try:
            self._save()
        except Exception as exc:                     # 出错要看得见，别静默失败
            self.msg("保存失败：" + str(exc)[:36], RED)

    def _save(self):
        name = self.e_name.get().strip()
        base = norm_base(self.e_base.get())
        key = self.e_key.get().strip()
        if not name or not base or not key:
            self.msg("名称、地址、Key 都要填", RED)
            return
        if not base.startswith("http"):
            self.msg("地址要以 http:// 或 https:// 开头", RED)
            return

        item = {"name": name, "base": base, "key": key}
        names = {n for n in (self.current, name) if n}
        idx = next((i for i, a in enumerate(self.accounts) if a["name"] in names), None)
        if idx is None:
            self.accounts.append(item)
        else:
            self.accounts[idx] = item

        self.cfg["accounts"] = self.accounts
        self.cfg["active"] = name
        save_config(self.cfg)
        self.current = name
        self.reload_list(select=name)

        note = self.apply_week_inputs()
        short = name if len(name) <= 18 else name[:17] + "…"
        self.msg("已保存：" + short + ("  ·  " + note if note else ""), GREEN)
        self.app.update_title()
        self.app.repaint()
        self.app.refresh()

    def delete(self):
        name = self.current
        if not name:
            self.msg("先在列表里选一个账号", RED)
            return
        self.accounts = [a for a in self.accounts if a["name"] != name]
        self.cfg["accounts"] = self.accounts
        if self.cfg.get("active") == name:
            self.cfg["active"] = None
        save_config(self.cfg)
        self.current = None
        for e in (self.e_name, self.e_base, self.e_key):
            self.set_entry(e, "")
        self.reload_list(select=self.cfg.get("active"))
        tail = "，回退到 magpie 读取" if not self.cfg.get("active") else ""
        self.msg(f"已删除 {name}{tail}")
        self.app.update_title()
        self.app.refresh()


# ---------------------------------------------------------------- 用量


class UsageWindow(tk.Toplevel):
    """token 用量：今日 / 本周 / 本月 + 近 7 天 + 按月 + 模型拆分。"""

    W = 452

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.cfg = app.cfg
        self.f = app.f_zh
        self.data = {}
        self.overrideredirect(True)
        self.configure(bg=BG)
        self.attributes("-topmost", bool(self.cfg["topmost"]))
        self.build()
        self.reload()
        self.place_near()
        round_window(self)
        self.bind("<Escape>", lambda e: self.destroy())

    # ---------------- 布局

    def build(self):
        outer = tk.Frame(self, bg=BG, highlightbackground=LINE, highlightthickness=1)
        outer.pack(fill="both", expand=True)

        head = tk.Frame(outer, bg=BG)
        head.pack(fill="x", padx=14, pady=(12, 0))
        title = tk.Label(head, text="用量统计", bg=BG, fg=TEXT, font=(self.f, 10, "bold"))
        title.pack(side="left")
        close = tk.Label(head, text="✕", bg=BG, fg=MUTED, font=(self.f, 10), cursor="hand2")
        close.pack(side="right")
        close.bind("<Button-1>", lambda e: self.destroy())
        again = tk.Label(head, text="↻", bg=BG, fg=MUTED, font=(self.f, 10), cursor="hand2")
        again.pack(side="right", padx=(0, 12))
        again.bind("<Button-1>", lambda e: self.reload())

        tk.Label(outer, text="喜鹊路由日志 + CC Switch 本地库，不需 token",
                 bg=BG, fg=MUTED, font=(self.f, 8), anchor="w",
                 justify="left", wraplength=self.W - 32).pack(fill="x", padx=14, pady=(2, 0))

        cards = tk.Frame(outer, bg=BG)
        cards.pack(fill="x", padx=14, pady=(10, 0))
        self.c_today = self.card(cards, "今日", last=False)
        self.c_week = self.card(cards, "本周", last=False)
        self.c_month = self.card(cards, "本月", last=True)

        self.lb_split = tk.Label(outer, text="", bg=BG, fg=MUTED, font=(self.f, 8), anchor="w")
        self.lb_split.pack(fill="x", padx=14, pady=(8, 0))
        self.days_box = self.section(outer, "近 7 天")
        self.month_box = self.section(outer, "按月")
        self.model_box = self.section(outer, "本月模型")

        foot = tk.Frame(outer, bg=BG)
        foot.pack(fill="x", padx=14, pady=(14, 12))
        tk.Label(foot, text="数字为输入+输出；缓存读单独列出，两边同一请求只计一次",
                 bg=BG, fg=MUTED, font=(self.f, 7), anchor="w",
                 justify="left", wraplength=self.W - 32).pack(side="left")

        for w in (head, title):
            w.bind("<Button-1>", self.drag_start)
            w.bind("<B1-Motion>", self.drag_move)

    def card(self, parent, title, last=False):
        box = tk.Frame(parent, bg=CARD, highlightbackground=LINE, highlightthickness=1)
        box.pack(side="left", expand=True, fill="both", padx=(0, 0 if last else 6))
        tk.Label(box, text=title, bg=CARD, fg=MUTED, font=(self.f, 8),
                 anchor="w").pack(fill="x", padx=8, pady=(6, 0))
        val = tk.Label(box, text="—", bg=CARD, fg=TEXT, font=(self.f, 13, "bold"), anchor="w")
        val.pack(fill="x", padx=8)
        sub = tk.Label(box, text="", bg=CARD, fg=MUTED, font=(self.f, 8), anchor="w")
        sub.pack(fill="x", padx=8, pady=(0, 6))
        return val, sub

    def section(self, parent, title):
        wrap = tk.Frame(parent, bg=BG)
        wrap.pack(fill="x", padx=14, pady=(14, 0))
        tk.Label(wrap, text=title, bg=BG, fg=MUTED, font=(self.f, 8),
                 anchor="w").pack(fill="x")
        body = tk.Frame(wrap, bg=BG)
        body.pack(fill="x", pady=(5, 0))
        return body

    def bar_row(self, parent, label, value, req, ratio, label_width=6, color=UP):
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", pady=1)
        tk.Label(row, text=label, bg=BG, fg=TEXT if value else MUTED, font=(self.f, 8),
                 width=label_width, anchor="w").pack(side="left")
        tk.Label(row, text=usage_stats.fmt_tokens(value) if value else "—", bg=BG,
                 fg=TEXT if value else MUTED, font=(self.f, 8),
                 width=7, anchor="e").pack(side="right")
        tk.Label(row, text=(f"{req:,}" if req else ""), bg=BG, fg=MUTED, font=(self.f, 8),
                 width=7, anchor="e").pack(side="right")
        track = tk.Frame(row, bg=TRACK, height=5)
        track.pack(side="left", fill="x", expand=True, padx=(6, 8))
        track.pack_propagate(False)
        if ratio > 0:
            tk.Frame(track, bg=color, height=5).place(
                relx=0, rely=0, relwidth=max(0.02, min(1.0, ratio)), relheight=1)

    # ---------------- 数据

    def reload(self):
        try:
            self.data = usage_stats.collect(400)
        except Exception:
            self.data = {}

        today = usage_stats.today_str()
        for card, dates in ((self.c_today, [today]),
                            (self.c_week, usage_stats.week_dates()),
                            (self.c_month, usage_stats.month_dates())):
            agg = usage_stats.totals(self.data, dates)
            val, sub = card
            val.config(text=usage_stats.fmt_tokens(agg["in"] + agg["out"]))
            sub.config(text=f"{agg['req']:,} 次" if agg["req"] else "无记录")

        month = usage_stats.totals(self.data, usage_stats.month_dates())
        self.lb_split.config(
            text=f"本月输入 {usage_stats.fmt_tokens(month['in'])} · "
                 f"输出 {usage_stats.fmt_tokens(month['out'])} · "
                 f"缓存读 {usage_stats.fmt_tokens(month['cache'])}")

        self.fill_days()
        self.fill_months(month)
        self.fill_models(month)
        self.place_near(keep_pos=True)

    def fill_days(self):
        for w in self.days_box.winfo_children():
            w.destroy()
        dates = usage_stats.recent_dates(7)
        vals = []
        for d in dates:
            row = self.data.get(d) or {}
            vals.append(row.get("in", 0) + row.get("out", 0))
        peak = max(vals + [1])
        if not any(vals):
            self.hint(self.days_box, "近 7 天没有记录")
            return
        for d, v in zip(dates, vals):
            row = self.data.get(d) or {}
            self.bar_row(self.days_box, d[5:], v, row.get("req", 0), v / peak)

    def fill_months(self, month):
        for w in self.month_box.winfo_children():
            w.destroy()
        months = list(usage_stats.by_month(self.data).items())[:4]
        if not months:
            self.hint(self.month_box, "没有历史记录")
            return
        peak = max([v["in"] + v["out"] for _k, v in months] + [1])
        for key, agg in months:
            v = agg["in"] + agg["out"]
            self.bar_row(self.month_box, key, v, agg["req"], v / peak, label_width=8, color=GREEN)

    def fill_models(self, month):
        for w in self.model_box.winfo_children():
            w.destroy()
        models = sorted(month["models"].items(),
                        key=lambda x: -(x[1]["in"] + x[1]["out"]))[:4]
        if not models:
            self.hint(self.model_box, "本月没有记录")
            return
        peak = max([v["in"] + v["out"] for _k, v in models] + [1])
        for name, v in models:
            short = name if len(name) <= 16 else name[:15] + "…"
            self.bar_row(self.model_box, short, v["in"] + v["out"], v["req"],
                         (v["in"] + v["out"]) / peak, label_width=16, color=AMBER)

    def hint(self, parent, text):
        tk.Label(parent, text=text, bg=BG, fg=MUTED, font=(self.f, 8),
                 anchor="w").pack(fill="x")

    # ---------------- 位置

    def place_near(self, keep_pos=False):
        if keep_pos and self.winfo_ismapped():
            return
        self.update_idletasks()
        w = max(self.W, self.winfo_reqwidth())
        h = self.winfo_reqheight()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        x = max(8, (sw - w) // 2)
        y = max(8, (sh - h) // 2 - 30)
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.update_idletasks()

    def drag_start(self, event):
        self._dx = event.x_root - self.winfo_x()
        self._dy = event.y_root - self.winfo_y()

    def drag_move(self, event):
        self.geometry(f"+{event.x_root - self._dx}+{event.y_root - self._dy}")


# ---------------------------------------------------------------- 入口

MUTEX_HANDLE = None


def single_instance():
    global MUTEX_HANDLE
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, "Global\\RelayBalanceWidget")
    MUTEX_HANDLE = handle
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        return False, handle
    return True, handle


def release_mutex():
    """更新后重启前必须先放锁，否则新进程会以为「已经在运行」。"""
    global MUTEX_HANDLE
    try:
        if MUTEX_HANDLE:
            ctypes.windll.kernel32.CloseHandle(MUTEX_HANDLE)
    except Exception:
        pass
    MUTEX_HANDLE = None


def main():
    if "--check" in sys.argv:
        cfg = load_config()
        pid = next((a for a in sys.argv[1:] if not a.startswith("-")), None)
        if pid:
            cfg["provider"] = pid
            cfg["active"] = None
        data = fetch_balance(cfg)
        quota = float(cfg.get("week_quota") or 100.0)
        pool = data["left"]
        spent = max(0.0, quota - pool)
        pct = (spent / quota * 100) if quota else 0.0
        print(f"来源 {data.get('source')}")
        print(f"本周 {week_key()} 已用 ${spent:,.2f} / ${quota:,.0f}（{pct:.1f}%），"
              f"剩 ${pool:,.2f}")
        print(f"总池 ${data['total']:,.2f} · 累计已用 ${data['used']:,.2f} · "
              f"剩 ${data['left']:,.2f}")
        return 0

    ok, handle = single_instance()
    if not ok:
        print("已经在运行了")
        return 0
    try:
        Widget().run()
    finally:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
