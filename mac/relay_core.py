# -*- coding: utf-8 -*-
"""中转管家 · 共用逻辑（macOS 版）

配置、额度查询、网速采样都不依赖界面，面板和菜单栏两个进程各自引用。
"""

import datetime
import json
import os
import time
import urllib.request

try:
    import psutil
except Exception:                      # 没装 psutil 就只显示余额
    psutil = None

APP_NAME = "中转管家"
CONFIG_DIR = os.path.expanduser("~/.config/relay-balance")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
PROVIDERS_PATH = os.path.expanduser("~/.config/magpie/providers.json")

DEFAULT_CONFIG = {
    "provider": None,        # magpie 里的供应商 id；留空则自动取第一个可用供应商
    "interval": 600,         # 余额刷新间隔（秒）
    "topmost": True,
    "panel_x": None,
    "panel_y": None,
    "accounts": [],          # [{name, base, key}]
    "active": None,
    "week_quota": 100.0,     # 每周额度
    "week_key": None,        # 当前所属的周，仅用于显示
    "net_panel": True,       # 面板里显示上传 / 下载
    "menu_speed": True,      # 菜单栏标题里显示速度
    "menu_balance": True,    # 菜单栏标题里显示余额
    "panel_token": True,     # 面板里显示今日 token
    "menu_token": True,      # 菜单栏标题里显示今日 token
}

INTERVALS = [
    (30, "30 秒"),
    (60, "1 分钟"),
    (120, "2 分钟"),
    (300, "5 分钟"),
    (600, "10 分钟"),
    (1800, "30 分钟"),
]


# ---------------------------------------------------------------- 配置

def load_config():
    cfg = dict(DEFAULT_CONFIG)
    os.makedirs(CONFIG_DIR, exist_ok=True)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except Exception:
        pass
    return cfg


def save_config(cfg):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)
    except Exception:
        pass


# ---------------------------------------------------------------- 账号

def norm_base(base):
    base = (base or "").strip().rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    return base


def active_account(cfg):
    name = cfg.get("active")
    if not name:
        return None
    for acc in cfg.get("accounts") or []:
        if acc.get("name") == name and acc.get("base") and acc.get("key"):
            return acc
    return None


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


def read_magpie_credentials(pid):
    with open(PROVIDERS_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    if not pid:
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


def current_credentials(cfg):
    """优先用设置里选中的账号，没有则回退到 magpie。返回 (base, key, 来源名)。"""
    acc = active_account(cfg)
    if acc:
        return norm_base(acc["base"]), acc["key"], acc["name"]
    base, key = read_magpie_credentials(cfg.get("provider"))
    return base, key, f"magpie · {cfg.get('provider') or '默认'}"


# ---------------------------------------------------------------- 额度

def api_get(base, key, path):
    req = urllib.request.Request(base + path, headers={"Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


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


def week_split(cfg, pool):
    """本周已用 = 周额度 − 总池剩余；补满即自动归零。"""
    quota = float(cfg.get("week_quota") or 100.0)
    spent = max(0.0, quota - float(pool))
    pct = (spent / quota * 100) if quota else 0.0
    return spent, quota, pct


def week_key(ts=None):
    t = time.localtime(ts) if ts else time.localtime()
    y, w, _ = datetime.date(t.tm_year, t.tm_mon, t.tm_mday).isocalendar()
    return f"{y}-W{w:02d}"


# ---------------------------------------------------------------- 格式化

def fmt_money(v):
    """负数写成 -$1.23，而不是 $-1.23。"""
    v = float(v)
    body = f"${abs(v):,.2f}"
    return ("-" + body) if v < 0 else body


def fmt_speed(v):
    if v < 1024:
        return f"{v:.0f} B/s"
    if v < 1024 ** 2:
        return f"{v / 1024:.1f} KB/s"
    return f"{v / 1024 ** 2:.2f} MB/s"


def fmt_speed_short(v):
    """菜单栏用：省掉 /s，塞得下。"""
    if v < 1024:
        return f"{v:.0f}B"
    if v < 1024 ** 2:
        return f"{v / 1024:.0f}K"
    return f"{v / 1024 ** 2:.1f}M"


# ---------------------------------------------------------------- 网速

SKIP_PREFIX = ("lo", "utun", "awdl", "llw", "gif", "stf", "bridge", "ap", "anpi")


class NetMeter:
    """系统整体上传 / 下载速率（字节每秒），排除回环与虚拟接口。

    本机代理走 127.0.0.1 / utun，若不排除会把同一份流量算两遍。
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
            if low.startswith(SKIP_PREFIX) or "loopback" in low or "teredo" in low:
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
