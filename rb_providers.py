"""供应商与凭据：magpie / CC Switch 读取、余额查询接口。

只依赖标准库，可以脱离界面直接跑（--check 走的就是这条路）。
"""
import datetime
import json
import os
import re
import sqlite3
import time
import urllib.request

from rb_core import CCSWITCH_DB, PROVIDERS_PATH

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
