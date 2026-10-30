# -*- coding: utf-8 -*-
"""中转管家 · 共用逻辑（macOS 版）

配置、额度查询、网速采样都不依赖界面，面板和菜单栏两个进程各自引用。
"""

import datetime
import json
import os
import re
import sqlite3
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
CCSWITCH_DB = os.path.expanduser("~/.cc-switch/cc-switch.db")

DEFAULT_CONFIG = {
    "provider": None,        # magpie 里的供应商 id；留空则自动取第一个可用供应商
    "interval": 600,         # 余额刷新间隔（秒）
    # 夜间暂停：这段时间不查额度
    "night_pause": True,
    "night_from": 21,        # 21:00 起
    "night_to": 8.5,         # 次日 08:30 止（可带半小时）
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
    "menu_mem": True,        # 菜单栏标题里显示剩余内存
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


# ------------------------------------------------- CC Switch 配置读取

def url_host(base):
    """https://a.com/v1 → a.com，用于区分同名供应商。"""
    body = (base or "").split("//")[-1]
    return body.split("/")[0] or base


def looks_like_key(text):
    """sk-xxx 或 bigmodel 那种长串，排除模型名和地址。"""
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


# ---------------------------------------------------------------- 内存

_MEM = {"t": 0.0, "gb": None, "pct": None}


def mem_status():
    """(可用 GB, 已用百分比)。macOS 的 available 已含可回收缓存。"""
    if psutil is None:
        return None, None
    now = time.time()
    if _MEM["gb"] is not None and now - _MEM["t"] < 4.0:   # 4 秒内复用
        return _MEM["gb"], _MEM["pct"]
    try:
        vm = psutil.virtual_memory()
        gb = vm.available / (1024.0 ** 3)
        pct = float(vm.percent)
    except Exception:
        gb = pct = None
    _MEM.update(t=now, gb=gb, pct=pct)
    return gb, pct


def fmt_mem(gb):
    """菜单栏用：6.4G / 512M。"""
    if gb is None:
        return "—"
    if gb < 1:
        return f"{gb * 1024:.0f}M"
    if gb >= 10:
        return f"{gb:.0f}G"
    return f"{gb:.1f}G"


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
