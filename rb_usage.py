"""用量统计窗口：今日 / 本周 / 本月 + 近 7 天 + 按月 + 模型拆分。"""
import tkinter as tk

import usage_stats
from rb_core import AMBER, BG, CARD, GREEN, LINE, MUTED, TEXT, TRACK, UP
from rb_win import round_window


# ---------------------------------------------------------------- 用量

class UsageWindow(tk.Toplevel):
    """token 用量：今日 / 本周 / 本月 + 近 7 天 + 按月 + 模型拆分。"""

    W = 452

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.cfg = app.cfg
        self.f = app.f_zh
        self.data = {}
        self.overrideredirect(True)
        self.configure(bg=BG)
        self.attributes("-topmost", bool(self.cfg["topmost"]))
        self.build()
        self.reload()
        self.place_near()
        round_window(self)
        self.bind("<Escape>", lambda e: self.destroy())

    # ---------------- 布局

    def build(self):
        outer = tk.Frame(self, bg=BG, highlightbackground=LINE, highlightthickness=1)
        outer.pack(fill="both", expand=True)

        head = tk.Frame(outer, bg=BG)
        head.pack(fill="x", padx=14, pady=(12, 0))
        title = tk.Label(head, text="用量统计", bg=BG, fg=TEXT, font=(self.f, 10, "bold"))
        title.pack(side="left")
        close = tk.Label(head, text="✕", bg=BG, fg=MUTED, font=(self.f, 10), cursor="hand2")
        close.pack(side="right")
        close.bind("<Button-1>", lambda e: self.destroy())
        again = tk.Label(head, text="↻", bg=BG, fg=MUTED, font=(self.f, 10), cursor="hand2")
        again.pack(side="right", padx=(0, 12))
        again.bind("<Button-1>", lambda e: self.reload())

        tk.Label(outer, text="喜鹊路由日志 + CC Switch 本地库，不需 token",
                 bg=BG, fg=MUTED, font=(self.f, 8), anchor="w",
                 justify="left", wraplength=self.W - 32).pack(fill="x", padx=14, pady=(2, 0))

        cards = tk.Frame(outer, bg=BG)
        cards.pack(fill="x", padx=14, pady=(10, 0))
        self.c_today = self.card(cards, "今日", last=False)
        self.c_week = self.card(cards, "本周", last=False)
        self.c_month = self.card(cards, "本月", last=True)

        self.lb_split = tk.Label(outer, text="", bg=BG, fg=MUTED, font=(self.f, 8), anchor="w")
        self.lb_split.pack(fill="x", padx=14, pady=(8, 0))
        self.days_box = self.section(outer, "近 7 天")
        self.month_box = self.section(outer, "按月")
        self.model_box = self.section(outer, "本月模型")

        foot = tk.Frame(outer, bg=BG)
        foot.pack(fill="x", padx=14, pady=(14, 12))
        tk.Label(foot, text="数字为输入+输出；缓存读单独列出，两边同一请求只计一次",
                 bg=BG, fg=MUTED, font=(self.f, 7), anchor="w",
                 justify="left", wraplength=self.W - 32).pack(side="left")

        for w in (head, title):
            w.bind("<Button-1>", self.drag_start)
            w.bind("<B1-Motion>", self.drag_move)

    def card(self, parent, title, last=False):
        box = tk.Frame(parent, bg=CARD, highlightbackground=LINE, highlightthickness=1)
        box.pack(side="left", expand=True, fill="both", padx=(0, 0 if last else 6))
        tk.Label(box, text=title, bg=CARD, fg=MUTED, font=(self.f, 8),
                 anchor="w").pack(fill="x", padx=8, pady=(6, 0))
        val = tk.Label(box, text="—", bg=CARD, fg=TEXT, font=(self.f, 13, "bold"), anchor="w")
        val.pack(fill="x", padx=8)
        sub = tk.Label(box, text="", bg=CARD, fg=MUTED, font=(self.f, 8), anchor="w")
        sub.pack(fill="x", padx=8, pady=(0, 6))
        return val, sub

    def section(self, parent, title):
        wrap = tk.Frame(parent, bg=BG)
        wrap.pack(fill="x", padx=14, pady=(14, 0))
        tk.Label(wrap, text=title, bg=BG, fg=MUTED, font=(self.f, 8),
                 anchor="w").pack(fill="x")
        body = tk.Frame(wrap, bg=BG)
        body.pack(fill="x", pady=(5, 0))
        return body

    def bar_row(self, parent, label, value, req, ratio, label_width=6, color=UP):
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", pady=1)
        tk.Label(row, text=label, bg=BG, fg=TEXT if value else MUTED, font=(self.f, 8),
                 width=label_width, anchor="w").pack(side="left")
        tk.Label(row, text=usage_stats.fmt_tokens(value) if value else "—", bg=BG,
                 fg=TEXT if value else MUTED, font=(self.f, 8),
                 width=7, anchor="e").pack(side="right")
        tk.Label(row, text=(f"{req:,}" if req else ""), bg=BG, fg=MUTED, font=(self.f, 8),
                 width=7, anchor="e").pack(side="right")
        track = tk.Frame(row, bg=TRACK, height=5)
        track.pack(side="left", fill="x", expand=True, padx=(6, 8))
        track.pack_propagate(False)
        if ratio > 0:
            tk.Frame(track, bg=color, height=5).place(
                relx=0, rely=0, relwidth=max(0.02, min(1.0, ratio)), relheight=1)

    # ---------------- 数据

    def reload(self):
        try:
            self.data = usage_stats.collect(400)
        except Exception:
            self.data = {}

        today = usage_stats.today_str()
        for card, dates in ((self.c_today, [today]),
                            (self.c_week, usage_stats.week_dates()),
                            (self.c_month, usage_stats.month_dates())):
            agg = usage_stats.totals(self.data, dates)
            val, sub = card
            val.config(text=usage_stats.fmt_tokens(agg["in"] + agg["out"]))
            sub.config(text=f"{agg['req']:,} 次" if agg["req"] else "无记录")

        month = usage_stats.totals(self.data, usage_stats.month_dates())
        self.lb_split.config(
            text=f"本月输入 {usage_stats.fmt_tokens(month['in'])} · "
                 f"输出 {usage_stats.fmt_tokens(month['out'])} · "
                 f"缓存读 {usage_stats.fmt_tokens(month['cache'])}")

        self.fill_days()
        self.fill_months(month)
        self.fill_models(month)
        self.place_near(keep_pos=True)

    def fill_days(self):
        for w in self.days_box.winfo_children():
            w.destroy()
        dates = usage_stats.recent_dates(7)
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

    def fill_months(self, month):
        for w in self.month_box.winfo_children():
            w.destroy()
        months = list(usage_stats.by_month(self.data).items())[:4]
        if not months:
            self.hint(self.month_box, "没有历史记录")
            return
        peak = max([v["in"] + v["out"] for _k, v in months] + [1])
        for key, agg in months:
            v = agg["in"] + agg["out"]
            self.bar_row(self.month_box, key, v, agg["req"], v / peak, label_width=8, color=GREEN)

    def fill_models(self, month):
        for w in self.model_box.winfo_children():
            w.destroy()
        models = sorted(month["models"].items(),
                        key=lambda x: -(x[1]["in"] + x[1]["out"]))[:4]
        if not models:
            self.hint(self.model_box, "本月没有记录")
            return
        peak = max([v["in"] + v["out"] for _k, v in models] + [1])
        for name, v in models:
            short = name if len(name) <= 16 else name[:15] + "…"
            self.bar_row(self.model_box, short, v["in"] + v["out"], v["req"],
                         (v["in"] + v["out"]) / peak, label_width=16, color=AMBER)

    def hint(self, parent, text):
        tk.Label(parent, text=text, bg=BG, fg=MUTED, font=(self.f, 8),
                 anchor="w").pack(fill="x")

    # ---------------- 位置

    def place_near(self, keep_pos=False):
        if keep_pos and self.winfo_ismapped():
            return
        self.update_idletasks()
        w = max(self.W, self.winfo_reqwidth())
        h = self.winfo_reqheight()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        x = max(8, (sw - w) // 2)
        y = max(8, (sh - h) // 2 - 30)
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.update_idletasks()

    def drag_start(self, event):
        self._dx = event.x_root - self.winfo_x()
        self._dy = event.y_root - self.winfo_y()

    def drag_move(self, event):
        self.geometry(f"+{event.x_root - self._dx}+{event.y_root - self._dy}")
