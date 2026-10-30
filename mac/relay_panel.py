# -*- coding: utf-8 -*-
"""中转管家 · 置顶面板（macOS 版）

无边框深色卡片，置顶显示：
    $剩余            本周 xx%
    ━━━━━━────────
    本周 $x / $100
    总额度 $x · 已用 $y
    ↑ 12 KB/s   ↓ 3.4 MB/s
    15:43:04 刷新     每 10 分钟刷新 ▾

右键出菜单，⚙ 打开换 Key 设置。菜单栏那个进程（relay_menubar.py）负责开关本窗口。
"""

import os
import queue
import signal
import sys
import threading
import time
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import relay_core as core
import usage_stats as usage

# ---------------------------------------------------------------- 外观

BG = "#15171c"
CARD = "#1b1e25"
TEXT = "#e8eaed"
MUTED = "#8b93a1"
GREEN = "#34d399"
AMBER = "#f5b544"
RED = "#f87171"
TRACK = "#2a2f38"

ZH = "PingFang SC"
MONO = "SF Mono"


def pick_font(cands, size, weight="normal"):
    """挑一个系统里真有的字体，避免 Tk 默默回退成难看的默认字体。"""
    import tkinter.font as tkfont
    have = set(tkfont.families())
    for name in cands:
        if name in have:
            return (name, size, weight)
    return (cands[-1], size, weight)


class Panel:
    W = 320

    def __init__(self):
        self.cfg = core.load_config()
        self.data = None
        self.net = core.NetMeter()
        self.ok = True
        self.busy = False
        self.busy_since = 0.0
        self.results = queue.Queue()
        self.settings = None
        self.usage = None
        self._drag = None

        self.root = tk.Tk()
        self.root.title("")
        self.root.overrideredirect(True)
        self.root.configure(bg=BG)
        if self.cfg.get("topmost", True):
            self.root.attributes("-topmost", True)
        try:
            self.root.attributes("-alpha", 0.98)
        except Exception:
            pass

        self.f_zh = pick_font([ZH, "Hiragino Sans GB", "Heiti SC", "Arial Unicode MS"], 12)
        self.f_num = pick_font([MONO, "Menlo", "Monaco", "Courier"], 30, "bold")
        self.f_small = pick_font([ZH, "Hiragino Sans GB", "Heiti SC"], 11)

        self.build()
        self.place_window()
        self.bind_keys()
        self.root.after(300, self.draw_bar)
        if not core.in_night(self.cfg):
            self.refresh()
        self.root.after(200, self.tick)
        self.root.after(300, self.tick_usage)
        self.root.after(120, self.poll)

        signal.signal(signal.SIGTERM, lambda *a: self.root.after(0, self.quit))
        signal.signal(signal.SIGINT, lambda *a: self.root.after(0, self.quit))

    # ------------------------------------------------------------ 布局

    def build(self):
        outer = tk.Frame(self.root, bg=BG, padx=16, pady=12)
        outer.pack(fill="both", expand=True)
        self.outer = outer

        head = tk.Frame(outer, bg=BG)
        head.pack(fill="x")
        self.dot = tk.Canvas(head, width=10, height=10, bg=BG, highlightthickness=0)
        self.dot.create_oval(1, 1, 9, 9, fill=GREEN, outline="")
        self.dot.pack(side="left", padx=(0, 6))
        self.lb_title = tk.Label(head, text=core.APP_NAME, bg=BG, fg=MUTED,
                                 font=self.f_small)
        self.lb_title.pack(side="left")
        self.lb_close = tk.Label(head, text="✕", bg=BG, fg=MUTED, font=self.f_small)
        self.lb_close.pack(side="right")
        self.lb_close.bind("<Button-1>", lambda e: self.quit())
        self.lb_gear = tk.Label(head, text="⚙", bg=BG, fg=MUTED, font=self.f_small)
        self.lb_gear.pack(side="right", padx=(0, 8))
        self.lb_gear.bind("<Button-1>", lambda e: self.open_settings())

        # 手动刷新：小圆圈包一个 ↻，hover 变亮，刷新中变绿
        self.btn_refresh = tk.Canvas(head, width=18, height=18, bg=BG,
                                     highlightthickness=0, cursor="hand2")
        self._rf_ring = self.btn_refresh.create_oval(1, 1, 17, 17, outline="#3a4150", width=1)
        self._rf_txt = self.btn_refresh.create_text(9, 9, text="↻", fill=MUTED,
                                                    font=(ZH, 11))
        self.btn_refresh.pack(side="right", padx=(0, 6), pady=(1, 0))
        self.btn_refresh.bind("<Button-1>", lambda e: self.refresh())
        self.btn_refresh.bind("<Enter>", lambda e: self._rf_style("hover"))
        self.btn_refresh.bind("<Leave>", lambda e: self._rf_style("idle"))

        row = tk.Frame(outer, bg=BG)
        row.pack(fill="x", pady=(6, 0))
        self.lb_left = tk.Label(row, text="—", bg=BG, fg=TEXT, font=self.f_num,
                                anchor="w")
        self.lb_left.pack(side="left")
        self.lb_pct = tk.Label(row, text="", bg=BG, fg=MUTED, font=self.f_small)
        self.lb_pct.pack(side="right", pady=(10, 0))

        self.bar = tk.Canvas(outer, height=6, bg=BG, highlightthickness=0)
        self.bar.pack(fill="x", pady=(6, 8))

        self.lb_detail = tk.Label(outer, text="", bg=BG, fg=MUTED,
                                  font=self.f_small, anchor="w")
        self.lb_detail.pack(fill="x")
        self.lb_pool = tk.Label(outer, text="", bg=BG, fg=MUTED,
                                font=self.f_small, anchor="w")
        self.lb_pool.pack(fill="x", pady=(2, 0))

        # 今日消耗的 token（5 秒一刷）
        self.lb_tok = tk.Label(outer, text="", bg=BG, fg=MUTED,
                               font=self.f_small, anchor="w")
        self.lb_tok.pack(fill="x", pady=(2, 0))

        self.net_row = tk.Frame(outer, bg=BG)
        self.net_row.pack(fill="x", pady=(6, 0))
        self.lb_up = tk.Label(self.net_row, text="", bg=BG, fg="#5aa9ff",
                              font=self.f_small)
        self.lb_up.pack(side="left")
        self.lb_down = tk.Label(self.net_row, text="", bg=BG, fg=GREEN,
                                font=self.f_small)
        self.lb_down.pack(side="right")

        foot = tk.Frame(outer, bg=BG)
        foot.pack(fill="x", pady=(6, 0))
        self.lb_status = tk.Label(foot, text="正在读取…", bg=BG, fg=MUTED,
                                  font=self.f_small, anchor="w")
        self.lb_status.pack(side="left")
        self.lb_interval = tk.Label(foot, text="", bg=BG, fg=MUTED, font=self.f_small)
        self.lb_interval.pack(side="right")
        self.lb_interval.bind("<Button-1>", self.popup_interval)

        self.menu = tk.Menu(self.root, tearoff=0)
        self.build_menu()

        targets = [outer, head, row, foot, self.bar, self.lb_left, self.lb_pct,
                   self.lb_detail, self.lb_pool, self.lb_tok, self.net_row, self.lb_up,
                   self.lb_down, self.lb_status, self.lb_interval, self.lb_title, self.dot]
        for w in targets:
            w.bind("<Button-3>", self.popup_menu)
            w.bind("<Control-Button-1>", self.popup_menu)
            w.bind("<Button-1>", self.drag_start)
            w.bind("<B1-Motion>", self.drag_move)
            w.bind("<ButtonRelease-1>", self.drag_end)

        self.apply_net_row()
        self.apply_token_row()
        self.update_interval_label()

    def bind_keys(self):
        self.root.bind("<Escape>", lambda e: self.quit())

    # ------------------------------------------------------------ 位置

    def place_window(self):
        self.root.update_idletasks()
        h = self.root.winfo_reqheight()
        x = self.cfg.get("panel_x")
        y = self.cfg.get("panel_y")
        if x is None or y is None:
            sw = self.root.winfo_screenwidth()
            x, y = sw - self.W - 40, 56
        self.root.geometry(f"{self.W}x{h}+{int(x)}+{int(y)}")

    def drag_start(self, event):
        self._drag = (event.x_root - self.root.winfo_x(),
                      event.y_root - self.root.winfo_y())

    def drag_move(self, event):
        if not self._drag:
            return
        dx, dy = self._drag
        self.root.geometry(f"+{event.x_root - dx}+{event.y_root - dy}")

    def drag_end(self, event):
        if not self._drag:
            return
        self._drag = None
        self.cfg["panel_x"] = self.root.winfo_x()
        self.cfg["panel_y"] = self.root.winfo_y()
        core.save_config(self.cfg)

    # ------------------------------------------------------------ 刷新

    def _rf_style(self, state):
        """刷新按钮三态：平时淡、hover 亮、刷新中变绿。"""
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
        cfg = dict(self.cfg)
        threading.Thread(target=self._worker, args=(cfg,), daemon=True).start()

    def _worker(self, cfg):
        try:
            self.results.put((core.fetch_balance(cfg), None))
        except Exception as exc:
            self.results.put((None, str(exc)))

    def poll(self):
        try:
            while True:
                data, err = self.results.get_nowait()
                self.apply(data, err)
        except queue.Empty:
            pass
        if self.busy and time.time() - self.busy_since > 25:
            self.busy = False
            self._rf_style("idle")
            self.lb_status.config(text="查询超时，稍后重试", fg=AMBER)
        self.root.after(150, self.poll)

    def apply(self, data, error):
        self.busy = False
        self._rf_style("idle")
        if error:
            self.ok = False
            self.dot.itemconfig(1, fill=RED)
            self.lb_status.config(text=self.short_error(error), fg=RED)
            if self.data is None:
                self.lb_left.config(text="—")
                self.lb_pct.config(text="")
                self.lb_detail.config(text="查不到数据")
                self.lb_pool.config(text="")
            return

        self.ok = True
        self.data = data
        pool = data["left"]
        spent, quota, pct = core.week_split(self.cfg, pool)
        self.cfg["week_key"] = core.week_key()
        self.week = {"spent": spent, "quota": quota, "pct": pct, "left": pool}

        color = GREEN if pct < 80 else (AMBER if pct < 95 else RED)
        self.dot.itemconfig(1, fill=color)
        self.lb_left.config(text=core.fmt_money(pool), fg=RED if pool < 0 else TEXT)
        self.lb_pct.config(
            text=(f"本周 {pct:.0f}%" if pct < 1000 else "本周 >999%"), fg=color)
        self.lb_detail.config(text=f"本周 ${spent:,.2f} / ${quota:,.0f}")
        self.lb_pool.config(
            text=f"总额度 ${data['total']:,.2f}   ·   已用 ${data['used']:,.2f}")
        self.lb_status.config(
            text=time.strftime("%H:%M:%S 刷新", time.localtime(data["at"])), fg=MUTED)
        self.draw_bar()
        self.last_at = time.time()
        self.root.title(f"{core.APP_NAME} · {self.source_name()}")

    def short_error(self, msg):
        msg = str(msg).replace("HTTP Error ", "HTTP ")
        if "urlopen error" in msg:
            return "网络不通"
        if "Unauthorized" in msg or "401" in msg:
            return "Key 无效或被禁用"
        return msg[:26]

    def source_name(self):
        acc = core.active_account(self.cfg)
        if acc:
            name = acc["name"]
        else:
            name = self.cfg.get("provider") or "magpie"
        return name if len(name) <= 12 else name[:11] + "…"

    def draw_bar(self):
        self.bar.delete("all")
        width = self.bar.winfo_width()
        if width <= 1:
            return
        pct = max(0.0, min(100.0, self.week["pct"] if hasattr(self, "week") else 0.0))
        self.bar.create_rectangle(0, 0, width, 6, fill=TRACK, outline="")
        fill_w = int(width * pct / 100.0)
        if fill_w > 0:
            color = GREEN if pct < 80 else (AMBER if pct < 95 else RED)
            self.bar.create_rectangle(0, 0, fill_w, 6, fill=color, outline="")

        # 字号变了窗口高度也会变，跟着抖一下高度
        want = self.root.winfo_reqheight()
        if want != self.root.winfo_height():
            x, y = self.root.winfo_x(), self.root.winfo_y()
            self.root.geometry(f"{self.W}x{want}+{x}+{y}")

    def update_title(self):
        self.lb_title.config(text=f"{core.APP_NAME} · {self.source_name()}")

    # ------------------------------------------------------------ 网速

    def tick(self):
        self.net.sample()
        if self.cfg.get("net_panel", True):
            self.lb_up.config(text="↑ " + core.fmt_speed(self.net.up))
            self.lb_down.config(text="↓ " + core.fmt_speed(self.net.down))
        if self.data:
            self.draw_bar()
        # 面板自己也要按时刷新余额（原先只有启动那一次）
        if (not core.in_night(self.cfg) and not self.busy
                and time.time() - getattr(self, "last_at", 0) >= self.cfg["interval"]):
            self.refresh()
        night = core.in_night(self.cfg)
        if night != getattr(self, "_night", None):
            self._night = night
            self.update_interval_label()
        self.root.after(1000, self.tick)

    def apply_net_row(self):
        on = bool(self.cfg.get("net_panel", True))
        if on:
            self.net_row.pack(fill="x", pady=(6, 0))
        else:
            self.net_row.pack_forget()

    # ------------------------------------------------------------ token 用量

    def tick_usage(self):
        try:
            self.today_tok = usage.today_live()
        except Exception:
            self.today_tok = {}
        self.render_tok()
        self.root.after(5000, self.tick_usage)

    def render_tok(self):
        t = getattr(self, "today_tok", None) or {}
        total = (t.get("in", 0) or 0) + (t.get("out", 0) or 0)
        if total:
            self.lb_tok.config(
                text=f"今日 {usage.fmt_tokens(total)} tok · {t.get('req', 0):,} 次")
        else:
            self.lb_tok.config(text="今日 — tok")

    def apply_token_row(self):
        if self.cfg.get("panel_token", True):
            self.lb_tok.pack(fill="x", pady=(2, 0), before=self.net_row)
        else:
            self.lb_tok.pack_forget()

    # ------------------------------------------------------------ 菜单

    def build_menu(self):
        m = self.menu
        m.delete(0, "end")
        m.add_command(label="立即刷新", command=self.refresh)
        sub = tk.Menu(m, tearoff=0)
        for sec, label in core.INTERVALS:
            sub.add_command(label=("✓ " if self.cfg["interval"] == sec else "　") + label,
                            command=lambda s=sec: self.set_interval(s))
        m.add_cascade(label="刷新间隔", menu=sub)
        m.add_separator()
        m.add_command(
            label=("✓ " if self.cfg.get("net_panel", True) else "　") + "面板显示网速",
            command=lambda: self.toggle("net_panel", net=True))
        m.add_command(
            label=("✓ " if self.cfg.get("panel_token", True) else "　") + "面板显示今日 token",
            command=lambda: self.toggle("panel_token", token=True))
        m.add_command(
            label=("✓ " if self.cfg.get("topmost", True) else "　") + "窗口置顶",
            command=lambda: self.toggle("topmost"))
        m.add_separator()
        m.add_command(label="用量统计…", command=self.open_usage)
        m.add_command(label="设置 / 换 Key", command=self.open_settings)
        m.add_command(label="打开配置文件", command=self.open_config_file)
        m.add_separator()
        m.add_command(label="隐藏面板", command=self.quit)

    def popup_menu(self, event):
        self.build_menu()
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def popup_interval(self, event):
        m = tk.Menu(self.root, tearoff=0)
        for sec, label in core.INTERVALS:
            m.add_command(label=("✓ " if self.cfg["interval"] == sec else "　") + label,
                          command=lambda s=sec: self.set_interval(s))
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    def toggle(self, key, net=False, token=False):
        self.cfg[key] = not bool(self.cfg.get(key, True))
        core.save_config(self.cfg)
        if net:
            self.apply_net_row()
        if token:
            self.apply_token_row()
        if key == "topmost":
            self.root.attributes("-topmost", bool(self.cfg["topmost"]))
        self.build_menu()

    def set_interval(self, sec):
        self.cfg["interval"] = int(sec)
        core.save_config(self.cfg)
        self.update_interval_label()
        self.refresh()

    def update_interval_label(self):
        if core.in_night(self.cfg):
            self.lb_interval.config(
                text=f"夜间暂停 {core.fmt_hm(self.cfg['night_from'])}–"
                     f"{core.fmt_hm(self.cfg['night_to'])} ▾")
            return
        label = next((l for s, l in core.INTERVALS if s == self.cfg["interval"]), "10 分钟")
        self.lb_interval.config(text=f"每 {label}刷新 ▾")

    def open_config_file(self):
        os.system(f'open -R "{core.CONFIG_PATH}"')

    # ------------------------------------------------------------ 设置

    def open_settings(self):
        if self.settings is not None and self.settings.winfo_exists():
            self.settings.lift()
            self.settings.focus_force()
            return
        self.settings = SettingsWindow(self)

    def open_usage(self):
        if self.usage is not None and self.usage.winfo_exists():
            self.usage.reload()
            self.usage.lift()
            self.usage.focus_force()
            return
        self.usage = UsageWindow(self)

    def quit(self):
        self.cfg["panel_x"] = self.root.winfo_x()
        self.cfg["panel_y"] = self.root.winfo_y()
        core.save_config(self.cfg)
        try:
            self.root.destroy()
        except Exception:
            pass

    def run(self):
        self.root.mainloop()


# ---------------------------------------------------------------- 设置窗口

class SettingsWindow(tk.Toplevel):
    W = 508

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.cfg = app.cfg
        self.accounts = list(self.cfg.get("accounts") or [])
        self.current = self.cfg.get("active")
        self.f = app.f_small
        self.f_mono = pick_font([MONO, "Menlo", "Monaco"], 11)

        self.title("设置 / 换 Key")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.build()
        self.update_idletasks()
        self.geometry(f"{self.W}x{self.winfo_reqheight()}"
                      f"+{app.root.winfo_x() - 40}+{app.root.winfo_y() + 40}")

    def build(self):
        outer = tk.Frame(self, bg=BG, padx=14, pady=12)
        outer.pack(fill="both", expand=True)

        tk.Label(outer, text="设置 / 换 Key", bg=BG, fg=TEXT,
                 font=pick_font([ZH, "Hiragino Sans GB"], 13, "bold")).pack(anchor="w")
        tk.Label(outer, text="Key 只存在本机 config.json，不会上传", bg=BG, fg=MUTED,
                 font=self.f).pack(anchor="w", pady=(2, 8))

        self.lb_list = tk.Listbox(outer, height=3, bg="#0f1115", fg=TEXT,
                                  selectbackground="#2b303a", selectforeground=TEXT,
                                  highlightthickness=1, highlightbackground=TRACK,
                                  bd=0, font=self.f, exportselection=False)
        self.lb_list.pack(fill="x")
        self.lb_list.bind("<<ListboxSelect>>", self.on_pick)
        self.reload_list()

        for label, attr, show in (("名称", "e_name", None),
                                  ("地址", "e_base", None),
                                  ("Key", "e_key", "•")):
            row = tk.Frame(outer, bg=BG)
            row.pack(fill="x", pady=(6, 0))
            tk.Label(row, text=label, bg=BG, fg=MUTED, width=4, anchor="w",
                     font=self.f).pack(side="left")
            entry = tk.Entry(row, bg="#0f1115", fg=TEXT, insertbackground=TEXT,
                             relief="flat", bd=0, font=self.f_mono,
                             highlightthickness=1, highlightbackground=TRACK)
            if show:
                entry.config(show=show)
            entry.pack(side="left", fill="x", expand=True, ipady=4)
            setattr(self, attr, entry)

        row = tk.Frame(outer, bg=BG)
        row.pack(fill="x", pady=(10, 0))
        self.pill(row, "读 magpie", self.load_from_magpie).pack(side="left")
        self.pill(row, "读 CC Switch", self.load_from_ccswitch).pack(side="left", padx=(6, 0))
        self.pill(row, "清空", self.new_account).pack(side="left", padx=(6, 0))
        self.pill(row, "测试连接", self.test).pack(side="right")

        qrow = tk.Frame(outer, bg=BG)
        qrow.pack(fill="x", pady=(8, 0))
        tk.Label(qrow, text="每周额度 $", bg=BG, fg=MUTED,
                 font=self.f).pack(side="left")
        self.e_quota = tk.Entry(qrow, bg="#0f1115", fg=TEXT, insertbackground=TEXT,
                                relief="flat", bd=0, font=self.f_mono, width=7,
                                highlightthickness=1, highlightbackground=TRACK)
        self.e_quota.pack(side="left", padx=(6, 0), ipady=3)
        self.e_quota.insert(0, f"{float(self.cfg.get('week_quota') or 100):g}")

        srow = tk.Frame(outer, bg=BG)
        srow.pack(fill="x", pady=(6, 0))
        tk.Label(srow, text="本周已用 $", bg=BG, fg=MUTED, font=self.f).pack(side="left")
        self.lb_spent = tk.Label(srow, text="—", bg=BG, fg=TEXT, font=self.f_mono)
        self.lb_spent.pack(side="left", padx=(6, 0))
        tk.Label(srow, text="额度 − 总池剩余", bg=BG, fg=MUTED,
                 font=self.f).pack(side="right")

        self.lb_msg = tk.Label(outer, text="", bg=BG, fg=MUTED, font=self.f, anchor="w")
        self.lb_msg.pack(fill="x", pady=(8, 0))

        foot = tk.Frame(outer, bg=BG)
        foot.pack(fill="x", pady=(10, 0))
        self.pill(foot, "删除", self.delete, fg=RED).pack(side="left")
        self.pill(foot, "保存并使用", self.save, fg="#0f1115", bg=GREEN).pack(side="right")

        if self.accounts:
            idx = next((i for i, a in enumerate(self.accounts)
                        if a["name"] == self.current), 0)
            self.show_account(self.accounts[idx])
            self.lb_list.selection_clear(0, "end")
            self.lb_list.selection_set(idx)

        self.refresh_spent()

    def pill(self, parent, text, cmd, fg=TEXT, bg=CARD):
        lb = tk.Label(parent, text=text, bg=bg, fg=fg, font=self.f,
                      padx=10, pady=4)
        lb.bind("<Button-1>", lambda e: cmd())
        lb.bind("<Enter>", lambda e: lb.config(bg="#232833" if bg == CARD else bg))
        lb.bind("<Leave>", lambda e: lb.config(bg=bg))
        return lb

    def reload_list(self):
        self.lb_list.delete(0, "end")
        for acc in self.accounts:
            nm = acc["name"] if len(acc["name"]) <= 20 else acc["name"][:19] + "…"
            self.lb_list.insert("end", f"  {nm}    {mask_key(acc['key'])}")
        if not self.accounts:
            self.lb_list.insert("end", "  （还没有账号，右边填一个）")

    def show_account(self, acc):
        self.current = acc["name"] if acc else None
        for entry, value in ((self.e_name, acc["name"] if acc else ""),
                             (self.e_base, acc["base"] if acc else ""),
                             (self.e_key, acc["key"] if acc else "")):
            entry.delete(0, "end")
            entry.insert(0, value or "")

    def on_pick(self, event=None):
        sel = self.lb_list.curselection()
        if not sel or not self.accounts:
            return
        idx = sel[0]
        if 0 <= idx < len(self.accounts):
            self.show_account(self.accounts[idx])

    def new_account(self):
        self.current = None
        for entry in (self.e_name, self.e_base, self.e_key):
            entry.delete(0, "end")
        self.msg("")

    def load_from_magpie(self):
        try:
            base, key = core.read_magpie_credentials(self.cfg.get("provider"))
        except Exception as exc:
            self.msg(str(exc)[:24], RED)
            return
        self.e_name.delete(0, "end")
        self.e_name.insert(0, self.cfg.get("provider") or "magpie")
        self.e_base.delete(0, "end")
        self.e_base.insert(0, base)
        self.e_key.delete(0, "end")
        self.e_key.insert(0, key)
        self.msg("已从 magpie 读取", GREEN)

    def load_from_ccswitch(self):
        """把 CC Switch 里所有带地址和 Key 的供应商导入账号列表。"""
        try:
            items = core.ccswitch_providers()
        except Exception as exc:
            self.msg(str(exc)[:24], RED)
            return
        if not items:
            self.msg("CC Switch 里没有可用的配置", RED)
            return

        added, pick = 0, None
        for it in items:
            base, key = it["base"], it["key"]
            same = next((a for a in self.accounts
                         if core.norm_base(a.get("base")) == base), None)
            if same:                             # 同一地址已导入，只同步 Key
                same["key"] = key
                pick = pick or same["name"]
                continue
            name = it["name"] or "cc-switch"
            if any(a.get("name") == name for a in self.accounts):
                name = f"{name} ({core.url_host(base)})"
            n = 2
            while any(a.get("name") == name for a in self.accounts):
                name = f"{it['name']} ({core.url_host(base)} {n})"
                n += 1
            self.accounts.append({"name": name, "base": base, "key": key})
            added += 1
            if it["current"] and pick is None:
                pick = name
        pick = pick or self.accounts[-1]["name"]

        self.reload_list()
        self.lb_list.config(height=max(1, min(4, len(self.accounts))))
        hit = next((a for a in self.accounts if a["name"] == pick), None)
        if hit:
            self.show_account(hit)
            idx = self.accounts.index(hit)
            self.lb_list.selection_clear(0, "end")
            self.lb_list.selection_set(idx)
        self.fit_height()
        if added:
            self.msg(f"已导入 {added} 组，点保存生效", GREEN)
        else:
            self.msg("都在列表里了，Key 已同步", GREEN)

    def fit_height(self):
        """列表行数变了要跟着改窗高。"""
        try:
            self.update_idletasks()
            self.geometry(f"{self.W}x{self.winfo_reqheight()}"
                          f"+{self.winfo_x()}+{self.winfo_y()}")
        except Exception:
            pass

    def test(self):
        base = core.norm_base(self.e_base.get())
        key = self.e_key.get().strip()
        if not base or not key:
            self.msg("先填地址和 Key", RED)
            return
        self.msg("测试中…", MUTED)
        self.update_idletasks()

        def work():
            try:
                data = core.query_balance(base, key)
                self.after(0, lambda: self.msg(
                    f"OK · 总 ${data['total']:,.2f} 剩 {core.fmt_money(data['left'])}",
                    GREEN))
            except Exception as exc:
                self.after(0, lambda: self.msg(str(exc)[:26], RED))

        threading.Thread(target=work, daemon=True).start()

    def save(self):
        name = self.e_name.get().strip()
        base = core.norm_base(self.e_base.get())
        key = self.e_key.get().strip()
        if not name or not base or not key:
            self.msg("名称、地址、Key 都要填", RED)
            return
        if not base.startswith("http"):
            self.msg("地址要以 http:// 或 https:// 开头", RED)
            return
        item = {"name": name, "base": base, "key": key}
        idx = next((i for i, a in enumerate(self.accounts) if a["name"] == name), None)
        if idx is None:
            self.accounts.append(item)
        else:
            self.accounts[idx] = item
        try:
            q = float(self.e_quota.get().strip() or 100)
            if q > 0:
                self.cfg["week_quota"] = q
        except Exception:
            pass
        self.cfg["accounts"] = self.accounts
        self.cfg["active"] = name
        self.current = name
        core.save_config(self.cfg)
        self.reload_list()
        self.msg(f"已保存：{name}", GREEN)
        self.app.update_title()
        self.app.refresh()

    def delete(self):
        name = self.current
        if not name:
            self.msg("先选一个账号", RED)
            return
        self.accounts = [a for a in self.accounts if a["name"] != name]
        self.cfg["accounts"] = self.accounts
        if self.cfg.get("active") == name:
            self.cfg["active"] = self.accounts[0]["name"] if self.accounts else None
        core.save_config(self.cfg)
        self.current = None
        self.new_account()
        self.reload_list()
        self.msg(f"已删除 {name}", GREEN)
        self.app.refresh()

    def refresh_spent(self):
        week = getattr(self.app, "week", None)
        try:
            if week:
                self.lb_spent.config(text=f"${week['spent']:,.2f}")
            else:
                self.lb_spent.config(text="—")
            self.after(2000, self.refresh_spent)
        except Exception:
            pass

    def msg(self, text, color=MUTED):
        self.lb_msg.config(text=text, fg=color)


# ---------------------------------------------------------------- 用量窗口

class UsageWindow(tk.Toplevel):
    """token 用量：今日 / 本周 / 本月 + 近 7 天 + 按月 + 模型拆分。"""

    W = 430

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.cfg = app.cfg
        self.f = app.f_small
        self.f_big = pick_font([ZH, "Hiragino Sans GB", "Heiti SC"], 13, "bold")
        self.f_tiny = pick_font([ZH, "Hiragino Sans GB", "Heiti SC"], 9)
        self.data = {}
        self.overrideredirect(True)
        self.configure(bg=BG)
        try:
            self.attributes("-topmost", bool(self.cfg.get("topmost", True)))
        except Exception:
            pass
        self.build()
        self.reload()
        self.place_center()
        self.bind("<Escape>", lambda e: self.destroy())

    # ---------------- 布局

    def build(self):
        outer = tk.Frame(self, bg=BG, highlightbackground=TRACK, highlightthickness=1)
        outer.pack(fill="both", expand=True)

        head = tk.Frame(outer, bg=BG)
        head.pack(fill="x", padx=14, pady=(12, 0))
        title = tk.Label(head, text="用量统计", bg=BG, fg=TEXT,
                         font=pick_font([ZH, "Hiragino Sans GB"], 12, "bold"))
        title.pack(side="left")
        close = tk.Label(head, text="✕", bg=BG, fg=MUTED, font=self.f)
        close.pack(side="right")
        close.bind("<Button-1>", lambda e: self.destroy())
        again = tk.Label(head, text="↻", bg=BG, fg=MUTED, font=self.f)
        again.pack(side="right", padx=(0, 12))
        again.bind("<Button-1>", lambda e: self.reload())

        tk.Label(outer, text="喜鹊路由日志 + CC Switch 本地库，不需 token",
                 bg=BG, fg=MUTED, font=self.f_tiny, anchor="w",
                 justify="left", wraplength=self.W - 32).pack(fill="x", padx=14, pady=(2, 0))

        cards = tk.Frame(outer, bg=BG)
        cards.pack(fill="x", padx=14, pady=(10, 0))
        self.c_today = self.card(cards, "今日")
        self.c_week = self.card(cards, "本周")
        self.c_month = self.card(cards, "本月", last=True)

        self.lb_split = tk.Label(outer, text="", bg=BG, fg=MUTED,
                                 font=self.f_tiny, anchor="w")
        self.lb_split.pack(fill="x", padx=14, pady=(8, 0))

        self.days_box = self.section(outer, "近 7 天")
        self.month_box = self.section(outer, "按月")
        self.model_box = self.section(outer, "本月模型")

        foot = tk.Frame(outer, bg=BG)
        foot.pack(fill="x", padx=14, pady=(12, 11))
        tk.Label(foot, text="数字为输入+输出；缓存读单独列出，两边同一请求只计一次",
                 bg=BG, fg=MUTED, font=self.f_tiny, anchor="w",
                 justify="left", wraplength=self.W - 32).pack(side="left")

        for w in (head, title):
            w.bind("<Button-1>", self.drag_start)
            w.bind("<B1-Motion>", self.drag_move)

    def card(self, parent, title, last=False):
        box = tk.Frame(parent, bg=CARD, highlightbackground=TRACK, highlightthickness=1)
        box.pack(side="left", expand=True, fill="both", padx=(0, 0 if last else 6))
        tk.Label(box, text=title, bg=CARD, fg=MUTED, font=self.f_tiny,
                 anchor="w").pack(fill="x", padx=8, pady=(6, 0))
        val = tk.Label(box, text="—", bg=CARD, fg=TEXT, font=self.f_big, anchor="w")
        val.pack(fill="x", padx=8)
        sub = tk.Label(box, text="", bg=CARD, fg=MUTED, font=self.f_tiny, anchor="w")
        sub.pack(fill="x", padx=8, pady=(0, 6))
        return val, sub

    def section(self, parent, title):
        wrap = tk.Frame(parent, bg=BG)
        wrap.pack(fill="x", padx=14, pady=(10, 0))
        tk.Label(wrap, text=title, bg=BG, fg=MUTED, font=self.f_tiny,
                 anchor="w").pack(fill="x")
        body = tk.Frame(wrap, bg=BG)
        body.pack(fill="x", pady=(5, 0))
        return body

    def bar_row(self, parent, label, value, req, ratio, label_width=6, color="#5aa9ff"):
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", pady=1)
        tk.Label(row, text=label, bg=BG, fg=TEXT if value else MUTED, font=self.f_tiny,
                 width=label_width, anchor="w").pack(side="left")
        tk.Label(row, text=usage.fmt_tokens(value) if value else "—", bg=BG,
                 fg=TEXT if value else MUTED, font=self.f_tiny,
                 width=7, anchor="e").pack(side="right")
        tk.Label(row, text=(f"{req:,}" if req else ""), bg=BG, fg=MUTED,
                 font=self.f_tiny, width=7, anchor="e").pack(side="right")
        track = tk.Frame(row, bg=TRACK, height=5)
        track.pack(side="left", fill="x", expand=True, padx=(6, 8))
        track.pack_propagate(False)
        if ratio > 0:
            tk.Frame(track, bg=color, height=5).place(
                relx=0, rely=0, relwidth=max(0.02, min(1.0, ratio)), relheight=1)

    # ---------------- 数据

    def reload(self):
        try:
            self.data = usage.collect(400)
        except Exception:
            self.data = {}

        today = usage.today_str()
        for card, dates in ((self.c_today, [today]),
                            (self.c_week, usage.week_dates()),
                            (self.c_month, usage.month_dates())):
            agg = usage.totals(self.data, dates)
            val, sub = card
            val.config(text=usage.fmt_tokens(agg["in"] + agg["out"]))
            sub.config(text=f"{agg['req']:,} 次" if agg["req"] else "无记录")

        month = usage.totals(self.data, usage.month_dates())
        self.lb_split.config(
            text=f"本月输入 {usage.fmt_tokens(month['in'])} · "
                 f"输出 {usage.fmt_tokens(month['out'])} · "
                 f"缓存读 {usage.fmt_tokens(month['cache'])}")

        self.fill_days()
        self.fill_months()
        self.fill_models(month)
        self.resize_to_fit()

    def fill_days(self):
        for w in self.days_box.winfo_children():
            w.destroy()
        dates = usage.recent_dates(7)
        vals = []
        for d in dates:
            row = self.data.get(d) or {}
            vals.append(row.get("in", 0) + row.get("out", 0))
        peak = max(vals + [1])
        if not any(vals):
            self.hint(self.days_box, "近 7 天没有记录")
            return
        for d, v in zip(dates, vals):
            row = self.data.get(d) or {}
            self.bar_row(self.days_box, d[5:], v, row.get("req", 0), v / peak)

    def fill_months(self):
        for w in self.month_box.winfo_children():
            w.destroy()
        months = list(usage.by_month(self.data).items())[:3]
        if not months:
            self.hint(self.month_box, "没有历史记录")
            return
        peak = max([v["in"] + v["out"] for _k, v in months] + [1])
        for key, agg in months:
            v = agg["in"] + agg["out"]
            self.bar_row(self.month_box, key, v, agg["req"], v / peak,
                         label_width=8, color=GREEN)

    def fill_models(self, month):
        for w in self.model_box.winfo_children():
            w.destroy()
        models = sorted(month["models"].items(),
                        key=lambda x: -(x[1]["in"] + x[1]["out"]))[:3]
        if not models:
            self.hint(self.model_box, "本月没有记录")
            return
        peak = max([v["in"] + v["out"] for _k, v in models] + [1])
        for name, v in models:
            short = name if len(name) <= 16 else name[:15] + "…"
            self.bar_row(self.model_box, short, v["in"] + v["out"], v["req"],
                         (v["in"] + v["out"]) / peak, label_width=16, color=AMBER)

    def hint(self, parent, text):
        tk.Label(parent, text=text, bg=BG, fg=MUTED, font=self.f_tiny,
                 anchor="w").pack(fill="x")

    # ---------------- 位置

    def place_center(self):
        self.update_idletasks()
        w = max(self.W, self.winfo_reqwidth())
        h = self.winfo_reqheight()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        x = max(8, (sw - w) // 2)
        y = max(28, (sh - h) // 2 - 60)
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.update_idletasks()

    def resize_to_fit(self):
        try:
            self.update_idletasks()
            w = max(self.W, self.winfo_reqwidth())
            h = self.winfo_reqheight()
            self.geometry(f"{w}x{h}")
        except Exception:
            pass

    def drag_start(self, event):
        self._dx = event.x_root - self.winfo_x()
        self._dy = event.y_root - self.winfo_y()

    def drag_move(self, event):
        self.geometry(f"+{event.x_root - self._dx}+{event.y_root - self._dy}")


def mask_key(key):
    if not key:
        return "（空）"
    if len(key) <= 12:
        return key[:4] + "…"
    return key[:6] + "…" + key[-4:]


if __name__ == "__main__":
    Panel().run()
