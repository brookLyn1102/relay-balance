"""中转管家 —— 置顶小窗 + 托盘图标

余额数据来自 new-api 系列中转的 OpenAI 兼容计费接口：
    /v1/dashboard/billing/subscription   总额度
    /v1/dashboard/billing/usage          累计消费（美分）

密钥从 magpie 的 providers.json 读取，不保存在本文件里。

界面按职责拆成几个模块，这里只剩主窗口和启动入口：
    rb_core       路径 / 配置 / 配色 / 格式化 / 网速计数
    rb_providers  magpie、CC Switch 凭据与余额接口
    rb_win        Win32 窗口工具与托盘图标
    rb_strip      Tk 悬浮网速条
    rb_settings   设置窗口（换 Key）
    rb_usage      用量统计窗口
"""
import ctypes
import os
import queue
import re
import subprocess
import sys
import threading
import time

import tkinter as tk

import pystray

try:
    import psutil
except Exception:                                    # 没装 psutil 时网速功能自动隐藏
    psutil = None

try:
    from taskbar_strip import TaskbarStrip
except Exception:                                    # 新条不可用时回退 Tk 版
    TaskbarStrip = None

import applog
import usage_stats
import updater
from rb_core import (
    AMBER, APP_NAME, APP_VERSION, BG, CARD, DOWN, GREEN, HERE, INTERVALS, LINE,
    MUTED, RED, RUN_KEY, RUN_NAME, TEXT, TRACK, UP, NetMeter, fmt_hm, fmt_mem,
    fmt_money, fmt_speed, in_night, load_config, mem_status, save_config,
)
from rb_providers import active_account, fetch_balance, week_key
from rb_settings import SettingsWindow
from rb_strip import TrafficStrip
from rb_usage import UsageWindow
from rb_win import round_window, tray_image


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
        self.root.after(3000, self.watch_strip)
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
        self.menu.add_command(label="打开日志", command=self.open_log)
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

    def watch_strip(self):
        """每 3 秒看一眼任务栏条还在不在。explorer 重启会把它带走。"""
        try:
            if (self.cfg.get("bar_mode") == "taskbar"
                    and TaskbarStrip is not None
                    and isinstance(self.strip, TaskbarStrip)
                    and not self.strip.health()):
                applog.log("任务栏条失联（explorer 可能重启过），重建")
                self.strip = None
                self.retry_taskbar_strip(0)
        except Exception as e:
            applog.log("watch_strip err", e)
        self.root.after(3000, self.watch_strip)

    # ---------------- 网速条

    # 挂任务栏失败后按这个节奏重试（秒），用完就认命用浮条
    STRIP_RETRY = (5, 10, 20, 40, 60)

    def apply_bar_mode(self, mode):
        self.cfg["bar_mode"] = mode
        save_config(self.cfg)
        self.destroy_strip()
        self._strip_try = 0
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
            except Exception as e:
                # 开机自启时可能比 explorer 早，任务栏还没建好——先浮条顶上，稍后重试
                applog.log("任务栏条挂载失败:", e)
                self._strip_try = 0
                self.schedule_strip_retry(0)
        try:
            return TrafficStrip(self, mode)
        except Exception as e:
            applog.log("浮条也建不起来:", e)
            return None

    def schedule_strip_retry(self, attempt):
        if attempt >= len(self.STRIP_RETRY):
            applog.log("任务栏条重挂次数用尽，本次用浮条")
            return
        delay = self.STRIP_RETRY[attempt] * 1000
        applog.log("任务栏条 %ds 后第 %d 次重挂" % (delay // 1000, attempt + 1))
        self.root.after(delay, lambda: self.retry_taskbar_strip(attempt))

    def retry_taskbar_strip(self, attempt):
        if self.cfg.get("bar_mode") != "taskbar" or TaskbarStrip is None:
            return
        if isinstance(self.strip, TaskbarStrip) and self.strip.health():
            self._strip_try = 0
            return
        try:
            strip = TaskbarStrip(self, fmt_speed, fmt_money, mem_status)
        except Exception as e:
            applog.log("重挂仍失败:", e)
            self._strip_try = attempt + 1
            self.schedule_strip_retry(attempt + 1)
            return
        applog.log("任务栏条重挂成功")
        self._strip_try = 0
        self.destroy_strip()
        self.strip = strip
        self.flash_status("任务栏条已挂上", GREEN, hold=2000)

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

    def open_log(self):
        """把日志目录打开并选中文件，出问题时让用户自己就能看一眼。"""
        path = applog.open_in_explorer()
        applog.log("用户打开日志", path)
        self.flash_status("日志：" + os.path.basename(path), MUTED, hold=2500)

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
            applog.log("查询失败:", error)
            # 网络抖一下就等满整个间隔太亏，5 秒后补一次（只补一次）
            self.root.after(5000, self.retry_after_error)
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
        """把原始异常翻成一句人话。状态栏只有三十来字，宁可含糊也别露英文堆栈。"""
        low = (msg or "").lower()

        # 本地配置类，先判——这些跟网络无关
        if "no such file" in low or "找不到" in msg or "没有供应商" in msg:
            return "读不到 magpie / CC Switch 配置"
        if "没有可用" in msg or "未配置" in msg or "密钥为空" in msg:
            return "还没填密钥"

        # HTTP 状态
        if "429" in low or "too many requests" in low or "rate limit" in low:
            return "请求太频繁，稍后再试"
        if "401" in low or "unauthorized" in low or "invalid token" in low:
            return "密钥无效"
        if "403" in low or "forbidden" in low:
            return "密钥无权访问"
        if "404" in low:
            return "中转没有计费接口"
        for code in ("500", "502", "503", "504"):
            if code in low:
                return "中转服务异常（%s）" % code

        # 传输层
        if "timed out" in low or "timeout" in low:
            return "超时，稍后再试"
        if "certificate" in low or "ssl" in low or "tls" in low:
            return "证书校验失败，检查系统时间或代理"
        if "10061" in low or "refused" in low or "积极拒绝" in msg:
            return "连不上中转，检查网络或代理"
        if "11001" in low or "getaddrinfo" in low or "name or service" in low:
            return "域名解析失败，检查 DNS"
        if "10051" in low or "unreachable" in low or "10054" in low:
            return "网络不通，连接被中断"
        if "proxy" in low:
            return "代理设置有问题"

        # 兜底：去掉 urllib 的套话外壳，留最像原因的那段
        text = re.sub(r"<urlopen error (.*)>", r"\1", msg or "")
        text = re.sub(r"\[Errno -?\d+\]\s*", "", text)
        text = re.sub(r"^\[WinError \d+\]\s*", "", text)
        text = " ".join(text.split())
        return text[:42] or "查询失败"

    def retry_after_error(self):
        """失败 5 秒后补一次。只补一次，避免断网时无限重试。"""
        if self.busy or self.ok or in_night(self.cfg):
            return
        applog.log("上次查询失败，5 秒后重试一次")
        self.flash_status("重试中…", MUTED, hold=1500)
        self.refresh()

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
            pystray.MenuItem("打开日志", lambda item: self.root.after(0, self.open_log)),
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
    applog.log("启动", APP_NAME, APP_VERSION, "python", sys.version.split()[0],
               "pid", os.getpid())
    try:
        Widget().run()
    finally:
        applog.log("退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
