"""token 用量统计：读 magpie 路由日志 + CC Switch 本地库。

两家都是「自己在本地记一笔」的思路，从不向中转站要 access token：
- magpie  每个转发出去的请求，把响应里的 usage 写进 ~/.config/magpie/routing/YYYY-MM-DD.jsonl
- CC Switch 扫各工具的会话日志 + 本地代理记录，落进 ~/.cc-switch/cc-switch.db

两边记录的是同一批请求的两种视角，混着加会翻倍，所以按天取其一：
magpie 优先（它是网关，数字最接近真实计费），CC Switch 只补 magpie 没有的日期。
"""

import os
import glob
import json
import time
import datetime
import sqlite3

MAGPIE_DIR = os.path.expanduser(os.path.join("~", ".config", "magpie", "routing"))
CCSWITCH_DB = os.path.expanduser(os.path.join("~", ".cc-switch", "cc-switch.db"))

# 文件级缓存：path -> (mtime, size, agg)。日志按天追加，没变动就不用重解析
_FILE_CACHE = {}


_SRC_CACHE = {"val": None, "ts": 0.0}


def has_source():
    """本机有没有可读的用量记录（magpie 的 jsonl 或 CC Switch 的库）。

    没有来源时「今日 token」永远是 0，余额刷新就不能靠它触发。
    结果缓存 5 分钟，省得每 5 秒去 stat 目录。
    """
    now = time.time()
    if _SRC_CACHE["val"] is not None and now - _SRC_CACHE["ts"] < 300:
        return _SRC_CACHE["val"]
    ok = False
    try:
        if os.path.isdir(MAGPIE_DIR):
            ok = any(n.endswith(".jsonl") for n in os.listdir(MAGPIE_DIR))
    except Exception:
        ok = False
    if not ok:
        ok = os.path.exists(CCSWITCH_DB)
    _SRC_CACHE["val"] = ok
    _SRC_CACHE["ts"] = now
    return ok


def fmt_tokens(n):
    """1,234,567 -> 1.23M"""
    n = int(n or 0)
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.2f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 10_000:
        return f"{n / 1_000:.0f}K"
    return f"{n:,}"


def _blank(src=""):
    return {"req": 0, "in": 0, "out": 0, "cache": 0, "models": {}, "src": src}


def _put(agg, model, tin, tout, cache):
    m = agg["models"].setdefault(model or "unknown", {"req": 0, "in": 0, "out": 0, "cache": 0})
    m["req"] += 1
    m["in"] += tin
    m["out"] += tout
    m["cache"] += cache


# ------------------------------------------------------------ magpie


def _num(v):
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _parse_magpie_file(path):
    """单个 jsonl 文件 -> 当天聚合。坏行直接跳过。"""
    agg = _blank("magpie")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if not r.get("done"):
                    continue
                usages = r.get("usage") or []
                if not usages:
                    continue
                for u in usages:
                    tin = _num(u.get("in"))
                    tout = _num(u.get("out"))
                    cache = _num(u.get("cache_read"))
                    if tin == 0 and tout == 0:
                        continue
                    agg["req"] += 1
                    agg["in"] += tin
                    agg["out"] += tout
                    agg["cache"] += cache
                    _put(agg, u.get("model"), tin, tout, cache)
    except Exception:
        return agg
    return agg


def scan_magpie(days=40):
    """{date: agg}，只返回有数据的日期。"""
    out = {}
    try:
        files = sorted(glob.glob(os.path.join(MAGPIE_DIR, "*.jsonl")))
    except Exception:
        return out
    for path in files[-days:]:
        name = os.path.basename(path)
        date = name[:-6] if name.endswith(".jsonl") else name
        try:
            st = os.stat(path)
        except Exception:
            continue
        key = (int(st.st_mtime), st.st_size)
        cached = _FILE_CACHE.get(path)
        if cached and cached[0] == key:
            agg = cached[1]
        else:
            agg = _parse_magpie_file(path)
            _FILE_CACHE[path] = (key, agg)
        if agg["req"]:
            out[date] = agg
    return out


# --------------------------------------------------------- CC Switch


def _cc_conn():
    if not os.path.exists(CCSWITCH_DB):
        return None
    try:
        uri = "file:%s?mode=ro" % CCSWITCH_DB.replace("\\", "/")
        return sqlite3.connect(uri, uri=True, timeout=1.0)
    except Exception:
        return None


def scan_ccswitch(days=400):
    """{date: agg}。明细表优先，按天汇总表补更早的历史。"""
    out = {}
    conn = _cc_conn()
    if conn is None:
        return out
    today = datetime.date.today()
    since = today - datetime.timedelta(days=days)
    try:
        # 1) 逐请求明细（近 30 天左右，最细）
        rows = conn.execute(
            "select created_at, model, input_tokens, output_tokens, cache_read_tokens "
            "from proxy_request_logs where created_at is not null"
        ).fetchall()
        for ts, model, tin, tout, cache in rows:
            tin, tout, cache = _num(tin), _num(tout), _num(cache)
            if tin == 0 and tout == 0:
                continue
            try:
                date = datetime.date.fromtimestamp(float(ts)).isoformat()
            except Exception:
                continue
            if date < since.isoformat():
                continue
            agg = out.setdefault(date, _blank("ccswitch"))
            agg["req"] += 1
            agg["in"] += tin
            agg["out"] += tout
            agg["cache"] += cache
            _put(agg, model, tin, tout, cache)
    except Exception:
        pass

    try:
        # 2) 按天汇总（覆盖到更早，明细表没有的月份靠它）
        rows = conn.execute(
            "select date, model, sum(request_count), sum(input_tokens), "
            "sum(output_tokens), sum(cache_read_tokens) from usage_daily_rollups "
            "where date >= ? group by date, model",
            (since.isoformat(),),
        ).fetchall()
        for date, model, req, tin, tout, cache in rows:
            if not date:
                continue
            agg = out.get(date)
            if agg is not None:
                continue          # 当天已有明细，汇总表会重复
            agg = out.setdefault(date, _blank("ccswitch"))
            agg["req"] += _num(req)
            agg["in"] += _num(tin)
            agg["out"] += _num(tout)
            agg["cache"] += _num(cache)
            _put(agg, model, _num(tin), _num(tout), _num(cache))
    except Exception:
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return out


# ------------------------------------------------------------- 汇总


def collect(days=120):
    """{date: agg}，magpie 与 CC Switch 按天互斥合并。"""
    merged = scan_ccswitch(days)
    merged.update(scan_magpie(days))     # magpie 同上则覆盖，避免重复计数
    return merged


def totals(data, dates):
    agg = _blank("")
    for d in dates:
        row = data.get(d)
        if not row:
            continue
        agg["req"] += row["req"]
        agg["in"] += row["in"]
        agg["out"] += row["out"]
        agg["cache"] += row["cache"]
        for m, v in row["models"].items():
            t = agg["models"].setdefault(m, {"req": 0, "in": 0, "out": 0, "cache": 0})
            for k in ("req", "in", "out", "cache"):
                t[k] += v[k]
    return agg


def today_str():
    return datetime.date.today().isoformat()


def week_dates(ref=None):
    """本周起（周一）到今天。"""
    d = ref or datetime.date.today()
    start = d - datetime.timedelta(days=d.weekday())
    out = []
    while start <= d:
        out.append(start.isoformat())
        start += datetime.timedelta(days=1)
    return out


def month_dates(ref=None):
    d = ref or datetime.date.today()
    return [x.isoformat() for x in
            (d.replace(day=1) + datetime.timedelta(days=i) for i in range(0, d.day))]


def recent_dates(n=7, ref=None):
    d = ref or datetime.date.today()
    return [(d - datetime.timedelta(days=i)).isoformat() for i in range(n - 1, -1, -1)]


def by_month(data):
    """{'2026-09': agg}，按月汇总，新的在前。"""
    out = {}
    for date, row in data.items():
        key = date[:7]
        agg = out.setdefault(key, _blank(""))
        agg["req"] += row["req"]
        agg["in"] += row["in"]
        agg["out"] += row["out"]
        agg["cache"] += row["cache"]
        agg["src"] = agg["src"] or row.get("src", "")
        for m, v in row["models"].items():
            t = agg["models"].setdefault(m, {"req": 0, "in": 0, "out": 0, "cache": 0})
            for k in ("req", "in", "out", "cache"):
                t[k] += v[k]
    return dict(sorted(out.items(), reverse=True))


# ------------------------------------------------------- 今日实时（面板/条）

_TAIL = {}          # path -> {"offset", "req", "in", "out", "cache"}
_CC_TODAY = {"ts": 0.0, "val": None}


def _consume_new(path, st):
    """只读日志新增的行。5 秒一轮，不能每次把整天的文件重读一遍。"""
    try:
        size = os.path.getsize(path)
    except OSError:
        return
    if size < st["offset"]:        # 文件被截断或换天，重头来
        st.update({"offset": 0, "req": 0, "in": 0, "out": 0, "cache": 0})
    if size <= st["offset"]:
        return
    try:
        with open(path, "rb") as f:
            f.seek(st["offset"])
            chunk = f.read()
    except OSError:
        return

    pos = 0
    while True:
        nl = chunk.find(b"\n", pos)
        if nl < 0:
            break                  # 末行可能还在写，留到下一轮
        raw = chunk[pos:nl].strip()
        pos = nl + 1
        if not raw:
            continue
        try:
            r = json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            continue
        if not r.get("done"):
            continue
        for u in (r.get("usage") or []):
            tin, tout = _num(u.get("in")), _num(u.get("out"))
            if tin == 0 and tout == 0:
                continue
            st["req"] += 1
            st["in"] += tin
            st["out"] += tout
            st["cache"] += _num(u.get("cache_read"))
    st["offset"] += pos


def today_live():
    """今日用量。magpie 优先，它今天还没有记录时用 CC Switch 的当天数据。"""
    date = datetime.date.today().isoformat()
    path = os.path.join(MAGPIE_DIR, date + ".jsonl")
    st = _TAIL.get(path)
    if st is None:
        st = {"offset": 0, "req": 0, "in": 0, "out": 0, "cache": 0}
        _TAIL[path] = st
    _consume_new(path, st)
    if st["req"]:
        return {"req": st["req"], "in": st["in"], "out": st["out"],
                "cache": st["cache"], "src": "magpie"}

    now = time.time()
    if _CC_TODAY["val"] is None or now - _CC_TODAY["ts"] > 60:
        row = scan_ccswitch(2).get(date)
        _CC_TODAY["val"] = row
        _CC_TODAY["ts"] = now
    row = _CC_TODAY["val"]
    if row:
        return {"req": row["req"], "in": row["in"], "out": row["out"],
                "cache": row["cache"], "src": "ccswitch"}
    return {"req": 0, "in": 0, "out": 0, "cache": 0, "src": ""}


if __name__ == "__main__":
    t0 = time.time()
    data = collect()
    print(f"读耗时 {time.time() - t0:.2f}s，{len(data)} 天有数据")
    td = totals(data, month_dates())
    print(f"本月 {fmt_tokens(td['in'] + td['out'])} tok ({td['req']} 次) "
          f"入 {fmt_tokens(td['in'])} 出 {fmt_tokens(td['out'])} 缓存 {fmt_tokens(td['cache'])}")
    for d in recent_dates(7):
        r = data.get(d)
        print(f"  {d}  " + (f"{fmt_tokens(r['in'] + r['out']):>8s}  {r['req']:>5d} 次  [{r['src']}]" if r else "—"))
    print("\n按月:")
    for k, v in list(by_month(data).items())[:5]:
        print(f"  {k}  {fmt_tokens(v['in'] + v['out']):>8s}  {v['req']:>6d} 次")
    print("\n本月按模型:")
    for m, v in sorted(td["models"].items(), key=lambda x: -(x[1]["in"] + x[1]["out"]))[:5]:
        print(f"  {m:28s} {fmt_tokens(v['in'] + v['out']):>8s}  {v['req']:>5d} 次")
