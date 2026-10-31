"""检查更新 —— 拿本机 git HEAD 和 GitHub 上 main 的最新提交比一比。

只做两件事：查（GitHub compare 接口，无需令牌）、拉（git pull --ff-only）。
没有 git 或不是 clone 下来的副本时不动手，交给界面提示去网页看。
"""

import json
import os
import subprocess
import urllib.error
import urllib.request

REPO = "brookLyn1102/relay-balance"
BRANCH = "main"
HOME_PAGE = f"https://github.com/{REPO}"
API = "https://api.github.com"

HERE = os.path.dirname(os.path.abspath(__file__))

_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_HEADERS = {
    "User-Agent": "relay-balance-updater",
    "Accept": "application/vnd.github+json",
}

_git_ok = None          # 本机 git 是否可用（缓存）


def _get(url, timeout=12):
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _git(args, timeout=60):
    """跑 git，返回 (code, stdout, stderr)；没有 git 时 code = -1。"""
    try:
        p = subprocess.run(
            ["git", "-C", HERE] + args,
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
            creationflags=_NO_WINDOW,
        )
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except FileNotFoundError:
        return -1, "", "找不到 git"
    except subprocess.TimeoutExpired:
        return -2, "", "git 超时"
    except Exception as exc:                        # noqa: BLE001
        return -1, "", str(exc)


def has_git():
    global _git_ok
    if _git_ok is None:
        code, out, _ = _git(["rev-parse", "--is-inside-work-tree"], timeout=10)
        _git_ok = (code == 0 and out.strip() == "true")
    return _git_ok


def _read_git_file(*parts):
    try:
        with open(os.path.join(HERE, ".git", *parts), encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def local_sha():
    """本机 commit；读不到返回 None。没装 git 时直接读 .git 目录。"""
    if has_git():
        code, out, _ = _git(["rev-parse", "HEAD"], timeout=10)
        if code == 0 and len(out) >= 7:
            return out.strip()
    head = _read_git_file("HEAD")
    if not head:
        return None
    if not head.startswith("ref:"):
        return head.strip() or None
    ref = head[4:].strip()
    sha = _read_git_file(*ref.split("/"))
    if sha:
        return sha
    for line in _read_git_file("packed-refs").splitlines():
        line = line.strip()
        if line.endswith(" " + ref) or line.endswith("\t" + ref):
            return line.split()[0]
    return None


def _friendly(exc):
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 403:
            return "GitHub 限流，稍后再试"
        if exc.code == 404:
            return "仓库或提交不存在"
        return f"GitHub 返回 {exc.code}"
    if isinstance(exc, urllib.error.URLError):
        return "网络不通"
    if isinstance(exc, TimeoutError):
        return "请求超时"
    return str(exc)[:40]


def check():
    """查有没有新提交。

    返回 dict：
        ok        查询成功
        behind    落后几个提交（>0 即有更新）
        ahead     本地多出的提交
        local     本机短 sha
        remote    远端短 sha
        message   最新提交标题
        date      最新提交时间
        url       对应网页
        can_pull  能否在本地直接 git pull
        error     失败原因
    """
    info = {
        "ok": False, "behind": 0, "ahead": 0,
        "local": None, "remote": None,
        "message": "", "date": "", "url": HOME_PAGE,
        "can_pull": False, "error": "",
    }
    loc = local_sha()
    if loc:
        info["local"] = loc[:7]
    info["can_pull"] = bool(loc) and has_git()

    try:
        if loc:
            data = _get(f"{API}/repos/{REPO}/compare/{loc}...{BRANCH}")
            # compare 的 base 是本机、head 是远端：ahead_by 才是「远端领先几个」，
            # 也就是本机落后几个提交。
            info["behind"] = int(data.get("ahead_by") or 0)
            info["ahead"] = int(data.get("behind_by") or 0)
            commits = data.get("commits") or []
            if commits:
                newest = commits[-1]
                info["remote"] = (newest.get("sha") or "")[:7]
                info["url"] = newest.get("html_url") or HOME_PAGE
                info["message"] = (newest.get("commit", {}).get("message") or "").splitlines()[0]
                info["date"] = newest.get("commit", {}).get("author", {}).get("date", "")
            else:
                info["remote"] = info["local"]
        else:
            # 不是 git 副本：只能看远端最新，没法判断本机新旧
            data = _get(f"{API}/repos/{REPO}/commits/{BRANCH}")
            if isinstance(data, list) and data:
                data = data[0]
            info["remote"] = (data.get("sha") or "")[:7] or None
            info["url"] = data.get("html_url") or HOME_PAGE
            info["message"] = (data.get("commit", {}).get("message") or "").splitlines()[0]
            info["date"] = data.get("commit", {}).get("author", {}).get("date", "")
            info["error"] = "本地不是 git 副本，无法判断是否最新"
        info["ok"] = True
    except Exception as exc:                        # noqa: BLE001
        info["error"] = _friendly(exc)
    return info


def pull():
    """git pull --ff-only，成功后需要重启程序才生效。"""
    if not has_git():
        return False, "没有 git"
    code, out, err = _git(["pull", "--ff-only", "--quiet"])
    if code == 0:
        return True, out or "已更新"
    if code == -1:
        return False, "找不到 git"
    if code == -2:
        return False, "git pull 超时"
    text = (err or out or "").replace("\n", " ").strip()
    if "local changes" in text or "would be overwritten" in text:
        return False, "本地有改动，先提交或还原"
    if "diverg" in text or "non-fast-forward" in text:
        return False, "本地和远端分叉了"
    if "couldn't find remote ref" in text or "not found" in text:
        return False, "远端分支不存在"
    return False, text[:40] or f"git pull 失败（{code}）"
