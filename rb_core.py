"""中转管家 · 基础层

路径、配置读写、配色、格式化、网速计数。不含任何界面代码，
其余模块都可以放心导入本模块。
"""
import datetime
import json
import os
import time

import applog

try:
    import psutil
except Exception:                                    # 没装 psutil 时网速功能自动隐藏
    psutil = None

APP_NAME = "中转管家"
APP_VERSION = "1.4.0"
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
    """原子写：先落临时文件再 os.replace，写一半崩了也不会毁掉原配置。"""
    tmp = CONFIG_PATH + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, CONFIG_PATH)
    except Exception as e:
        applog.log("save_config 失败:", e)
        try:
            os.remove(tmp)
        except Exception:
            pass

# ---------------------------------------------------------------- 格式化

def fmt_money(v, sign=True):
    """负数写成 -$1.23，而不是 $-1.23。"""
    v = float(v)
    body = f"${abs(v):,.2f}"
    return ("-" + body) if (sign and v < 0) else body

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
