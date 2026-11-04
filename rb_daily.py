"""按天记录「已用额度」基准，用来算今天花了多少。

接口只给累计已用（`used`），所以要有个当天的起点。基准这样定：

- 进程在跑：每天 0 点查一次接口，把那一刻的已用记成当天基准；
- 半夜没开机：退回到前一天最后一次看到的已用（关机到第二天之间花的
  那点会算进今天——那本来也多是今天的用量，宁可多算也别漏算）；
- 当天第一次查询就是基准（刚装上、刚换服务器时）。

今日用量 = 当前已用 − 今日基准。中途充值不影响：充值是往总额度里加钱，
`used` 只增不减；万一被后台重置成更小值，就按 0 算，不显示负数。

周用量不用另存：本周一的日期在 days 里就有，那天的基准就是周一 0 点的
已用额度，减一下就是本周花费。周一没记录就返回 None，让调用方退回估算。
"""
import datetime
import json
import os
import time

import applog

KEEP_DAYS = 120          # 只留半年，文件一直很小


def path():
    """和 app.log 放一起：%LOCALAPPDATA%\\relay-balance\\daily.json。"""
    base = os.path.dirname(applog.log_path())
    return os.path.join(base, "daily.json")


def _load():
    try:
        with open(path(), encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("days"), dict):
            return data
    except Exception:
        pass
    return {"days": {}, "last": None}


def _save(data):
    """原子写，跟配置一个套路。"""
    try:
        p = path()
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except Exception as e:
        applog.log("daily 写入失败:", e)


def _date(ts):
    return datetime.date.fromtimestamp(ts).isoformat()


def mark(used, now=None):
    """记下今日基准（当天只写一次），返回基准值。"""
    try:
        used = float(used)
    except (TypeError, ValueError):
        return 0.0
    ts = time.time() if now is None else float(now)
    today = _date(ts)
    data = _load()
    days = data["days"]

    base = days.get(today)
    if base is None:
        base = used
        last = data.get("last") or {}
        pu, pts = last.get("used"), last.get("ts")
        if isinstance(pu, (int, float)) and isinstance(pts, (int, float)):
            # 前一天看到过数字，说明只是中途关机了：拿它当起点，
            # 中间花掉的往今天算，比整段丢掉强
            yday = (datetime.date.fromtimestamp(ts)
                    - datetime.timedelta(days=1)).isoformat()
            if _date(pts) == yday and pu <= used:
                base = float(pu)
        days[today] = base
        for old in sorted(days)[:-KEEP_DAYS]:
            days.pop(old, None)
        applog.log(f"daily 基准 {today} = {base:.2f}")

    data["last"] = {"ts": ts, "used": used}
    _save(data)
    return float(base)


def used_today(used, now=None):
    """今天花掉多少（含刚才这一次查询的增量）。"""
    try:
        used = float(used)
    except (TypeError, ValueError):
        return 0.0
    base = mark(used, now)
    return max(0.0, used - base)


def base_of(date=None):
    """某天的基准；不给日期就是今天。"""
    date = date or _date(time.time())
    return float(_load()["days"].get(date) or 0.0)


def week_start(now=None):
    """本周一（ISO 周，周一算第一天）的日期。"""
    ts = time.time() if now is None else float(now)
    d = datetime.date.fromtimestamp(ts)
    return (d - datetime.timedelta(days=d.weekday())).isoformat()


def used_this_week(used, now=None):
    """本周一 0 点以来花了多少。

    周一的基准就是那天 0 点的「已用」，本来就在 days 里，不用另存一份。
    本周一没记录（当时没开机 / 这周才装上）就返回 None，调用方退回老算法。
    """
    try:
        used = float(used)
    except (TypeError, ValueError):
        return None
    base = _load()["days"].get(week_start(now))
    if base is None:
        return None
    return max(0.0, used - float(base))


def history():
    """{日期: 基准}，给以后做「近 7 天花费」用。"""
    return dict(_load()["days"])
