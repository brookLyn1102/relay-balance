#!/bin/bash
# 启动中转管家（菜单栏 + 面板）
cd "$(dirname "$0")" || exit 1

if ! command -v python3 >/dev/null 2>&1; then
  echo "没找到 python3，先装一下：https://www.python.org/downloads/macos/"
  exit 1
fi

if ! python3 -c "import psutil, rumps" >/dev/null 2>&1; then
  echo "缺少依赖，正在安装 psutil / rumps …"
  python3 -m pip install --user -r requirements.txt || {
    echo "安装失败，手动跑一遍：python3 -m pip install --user rumps psutil"
    exit 1
  }
fi

exec python3 relay_menubar.py
