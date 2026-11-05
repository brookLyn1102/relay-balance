"""token 用量统计：读 magpie 路由日志 + CC Switch 本地库。

两家都是「自己在本地记一笔」的思路，从不向中转站要 access token：
- magpie  每个转发出去的请求，把响应里的 usage 写进 ~/.config/magpie/routing/YYYY-MM-DD.jsonl
- CC Switch 扫各工具的会话日志 + 本地代理记录，落进 ~/.cc-switch/cc-switch.db

两边记录的是同一批请求的两种视角，混着加会翻倍，所以按天取其一：
magpie 优先（它是网关，数字最接近真实计费），CC Switch 只补 magpie 没有的日期。
"""

import os
import glob
import gzip
import json
import re
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
            ok = any(n.endswith((".jsonl", ".jsonl.gz"))
                     for n in os.listdir(MAGPIE_DIR))
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


def tok_total(agg, with_cache=True):
    """总量口径：输入 + 输出 + 缓存读。

    缓存读通常占九成（一次长上下文请求能命中几十万），外面那套面板
    （喜鹊网页、CC Switch）都把它算进 token 总数，这里跟着算才对得上。
    """
    if not agg:
        return 0
    n = _num(agg.get("in")) + _num(agg.get("out"))
    return n + _num(agg.get("cache")) if with_cache else n


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
    """单个 jsonl（或 .jsonl.gz）文件 -> 当天聚合。坏行直接跳过。

    magpie 每天上午 10 点左右把前一天的日志压成 .jsonl.gz，所以历史日期
    几乎都是压缩包；只认 .jsonl 会让「本周/本月」缺掉前几天，看起来和
    今日一模一样。
    """
    agg = _blank("magpie")
    try:
        opener = gzip.open if path.endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8", errors="replace") as f:
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


def _date_of(name):
    """2026-11-03.jsonl / 2026-11-03.jsonl.gz -> 2026-11-03"""
    for suffix in (".jsonl.gz", ".jsonl"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return ""


def scan_magpie(days=40):
    """{date: agg}，只返回有数据的日期。"""
    out = {}
    try:
        files = [p for p in glob.glob(os.path.join(MAGPIE_DIR, "*.jsonl*"))
                 if _date_of(os.path.basename(p))]
        # 按日期排，别按文件名：字符串序里 "11-04.jsonl" 排在 "11-03.jsonl.gz" 前面
        files.sort(key=lambda p: _date_of(os.path.basename(p)))
    except Exception:
        return out
    for path in files[-days:]:
        date = _date_of(os.path.basename(path))
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

    # 当天的日志已经被压走（手动压缩、或者跨天后才开机）：直接读压缩包
    gz = path + ".gz"
    if os.path.exists(gz):
        try:
            st_info = os.stat(gz)
            key = (int(st_info.st_mtime), st_info.st_size)
        except OSError:
            key = None
        gz_agg = None
        if key:
            cached = _FILE_CACHE.get(gz)
            if cached and cached[0] == key:
                gz_agg = cached[1]
        if gz_agg is None:
            gz_agg = _parse_magpie_file(gz)
            if key:
                _FILE_CACHE[gz] = (key, gz_agg)      # 压缩包不会变，解一次就够
        if gz_agg["req"]:
            return {"req": gz_agg["req"], "in": gz_agg["in"], "out": gz_agg["out"],
                    "cache": gz_agg["cache"], "src": "magpie"}

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


# ---------------- 输出速度（token/秒）

SPEED_WINDOW = 90            # 只认最近 90 秒内跑完的请求
_SPEED = {"ts": 0.0, "val": None}


def _iso_epoch(s):
    """'2026-11-05T09:27:07.3816314+08:00' -> epoch 秒。

    喜鹊写的时间带 7 位小数，fromisoformat 只认到 6 位，先截一下。
    """
    if not isinstance(s, str) or not s:
        return None
    s = re.sub(r"\.(\d{6})\d+", r".\1", s.replace("Z", "+00:00"))
    try:
        return datetime.datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


def _magpie_speed_items(path, tail=400_000):
    """读当天 jsonl 的尾巴，返回 [(结束时刻, 输出 token, 纯生成毫秒)]。

    只读尾巴：日志一天十几兆，每 5 秒全量解析太亏，而速度只看最近几十秒。
    """
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > tail:
                f.seek(size - tail)
                f.readline()                     # 丢掉被截断的半行
            raw = f.read()
    except OSError:
        return []
    items = []
    for line in raw.decode("utf-8", "replace").splitlines():
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
        out = sum(_num(u.get("out")) for u in usages)
        if out <= 0:
            continue
        ms = _num(r.get("ms")) or 0
        ttft = _num(r.get("ttft")) or 0
        if not ms:
            tries = r.get("tries") or []
            if tries and isinstance(tries[0], dict):
                ms = _num(tries[0].get("ms")) or 0
                ttft = ttft or _num(tries[0].get("ttft")) or 0
        end = _iso_epoch(r.get("time"))
        if not end or not ms:
            continue
        # 扣掉等首字的时间：那段时间没出 token，算进去会把速度压得很低
        items.append((end + ms / 1000.0, float(out), max(120.0, float(ms - ttft))))
    return items


def _cc_speed_items(window):
    """CC Switch 的最近请求：output / (latency − first_token)。"""
    conn = _cc_conn()
    if not conn:
        return []
    try:
        since = int(time.time()) - window
        rows = conn.execute(
            "select output_tokens, latency_ms, first_token_ms, created_at "
            "from proxy_request_logs where created_at >= ? and output_tokens > 0 "
            "order by created_at desc limit 50", (since,)).fetchall()
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass
    items = []
    for out, lat, ft, at in rows:
        ms = float(lat or 0)
        if ms <= 0:
            continue
        items.append((float(at or 0) + ms / 1000.0, float(out),
                      max(120.0, ms - float(ft or 0))))
    return items


def speed(window=SPEED_WINDOW):
    """最近一次请求的输出速度（token/秒）。近 window 秒没跑过就返回 None。

    只看最后跑完的那一条：输出 token ÷ 纯生成时长（扣掉 ttft），跟直觉一致
    ——上一条是多少就是多少，不会被之前的请求摊平。喜鹊优先，那边没有
    新记录时再问 CC Switch。
    """
    now = time.time()
    if _SPEED["val"] is not None and now - _SPEED["ts"] < 2:
        return _SPEED["val"]
    date = datetime.date.today().isoformat()
    items = []
    for name in (date + ".jsonl", date + ".jsonl.gz"):   # 当天被压走也认
        path = os.path.join(MAGPIE_DIR, name)
        if os.path.exists(path):
            items = _magpie_speed_items(path)
            if items:
                break
    if not items:
        items = _cc_speed_items(window)
    fresh = [it for it in items if now - it[0] <= window and it[2] > 0]
    if not fresh:                                 # 喜鹊有记录但都过期了，再问一次 CC Switch
        fresh = [it for it in _cc_speed_items(window)
                 if now - it[0] <= window and it[2] > 0]
    val = None
    if fresh:
        # 只取最后一条：上一次跑出来多快就显示多快
        last = max(fresh, key=lambda it: it[0])
        val = last[1] / (last[2] / 1000.0)
    if val is not None:
        val = max(0.0, min(float(val), 9999.0))
    _SPEED["val"] = val
    _SPEED["ts"] = now
    return val


if __name__ == "__main__":
    t0 = time.time()
    data = collect()
    print(f"读耗时 {time.time() - t0:.2f}s，{len(data)} 天有数据")
    td = totals(data, month_dates())
    print(f"本月 {fmt_tokens(tok_total(td))} tok ({td['req']} 次) "
          f"入 {fmt_tokens(td['in'])} 出 {fmt_tokens(td['out'])} 缓存 {fmt_tokens(td['cache'])}")
    for d in recent_dates(7):
        r = data.get(d)
        print(f"  {d}  " + (f"{fmt_tokens(tok_total(r)):>8s}  {r['req']:>5d} 次  [{r['src']}]" if r else "—"))
    print("\n按月:")
    for k, v in list(by_month(data).items())[:5]:
        print(f"  {k}  {fmt_tokens(tok_total(v)):>8s}  {v['req']:>6d} 次")
    print("\n本月按模型:")
    for m, v in sorted(td["models"].items(), key=lambda x: -tok_total(x[1]))[:5]:
        print(f"  {m:28s} {fmt_tokens(tok_total(v)):>8s}  {v['req']:>5d} 次")
