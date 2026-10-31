"""设置窗口：换 Key、周额度。"""
import threading
import tkinter as tk

from rb_core import (APP_VERSION, BG, CARD, GREEN, LINE, MUTED, RED, TEXT,
                     save_config)
from rb_providers import (ccswitch_providers, mask_key, norm_base,
                          query_balance, read_credentials, url_host)
from rb_win import round_window


# ---------------------------------------------------------------- 设置窗口

class SettingsWindow(tk.Toplevel):
    """换 Key：管理多组「名称 / 地址 / Key」，选中即切换。"""

    W = 462

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.cfg = app.cfg
        self.f = app.f_zh
        self.current = None
        self.accounts = [dict(a) for a in (self.cfg.get("accounts") or [])]

        self.overrideredirect(True)
        self.configure(bg=BG)
        self.attributes("-topmost", bool(self.cfg["topmost"]))
        self.build()
        self.place_near()
        round_window(self)
        self.bind("<Escape>", lambda e: self.destroy())
        self.reload_list(select=self.cfg.get("active"))
        if not self.accounts:
            # 没有任何自建账号时，先用 magpie 的配置填好，改 Key 就能存
            self.load_from_magpie()

    # ---------------- 布局

    def build(self):
        outer = tk.Frame(self, bg=BG, highlightbackground=LINE, highlightthickness=1)
        outer.pack(fill="both", expand=True)

        head = tk.Frame(outer, bg=BG)
        head.pack(fill="x", padx=14, pady=(12, 0))
        tk.Label(head, text="设置 · 换 Key", bg=BG, fg=TEXT,
                 font=(self.f, 10, "bold")).pack(side="left")
        close = tk.Label(head, text="✕", bg=BG, fg=MUTED, font=(self.f, 10), cursor="hand2")
        close.pack(side="right")
        close.bind("<Button-1>", lambda e: self.destroy())

        tk.Label(outer, text=f"Key 只存在本机 config.json，不会上传　·　v{APP_VERSION}",
                 bg=BG, fg=MUTED, font=(self.f, 8), anchor="w").pack(fill="x", padx=14, pady=(2, 0))

        wrap = tk.Frame(outer, bg=LINE, padx=1, pady=1)
        wrap.pack(fill="x", padx=14, pady=(10, 0))
        self.lst = tk.Listbox(wrap, bg=CARD, fg=TEXT, selectbackground="#2b303a",
                              selectforeground=TEXT, font=(self.f, 9), bd=0,
                              highlightthickness=0, activestyle="none", height=3,
                              exportselection=False)
        self.lst.pack(fill="both", expand=True)
        self.lst.bind("<<ListboxSelect>>", self.on_pick)
        self.lst.bind("<Double-Button-1>", lambda e: self.save())
        self.lst.bind("<MouseWheel>",
                      lambda e: self.lst.yview_scroll(-1 * (e.delta // 120), "units"))

        form = tk.Frame(outer, bg=BG)
        form.pack(fill="x", padx=14, pady=(10, 0))
        form.columnconfigure(1, weight=1)

        self.e_name = self.field(form, 0, "名称")
        self.e_name.configure(fg=TEXT)
        self.e_base = self.field(form, 1, "地址")
        self.e_key = self.field(form, 2, "Key", show="•")
        self.e_key.bind("<Return>", lambda e: self.save())

        self.lb_show = tk.Label(form, text="显示", bg=BG, fg=MUTED,
                                font=(self.f, 8), cursor="hand2")
        self.lb_show.grid(row=2, column=2, padx=(6, 0))
        self.lb_show.bind("<Button-1>", lambda e: self.toggle_show())

        row1 = tk.Frame(outer, bg=BG)
        row1.pack(fill="x", padx=14, pady=(10, 0))
        self.pill(row1, "读 magpie", self.load_from_magpie).pack(side="left")
        self.pill(row1, "读 CC Switch", self.load_from_ccswitch).pack(side="left", padx=(6, 0))
        self.pill(row1, "清空", self.new_account).pack(side="left", padx=(6, 0))
        self.pill(row1, "测试连接", self.test).pack(side="right")

        qrow = tk.Frame(outer, bg=BG)
        qrow.pack(fill="x", padx=14, pady=(9, 0))
        tk.Label(qrow, text="每周额度 $", bg=BG, fg=MUTED,
                 font=(self.f, 9)).pack(side="left")
        qbox = tk.Frame(qrow, bg="#0f1115", highlightbackground=LINE, highlightthickness=1)
        qbox.pack(side="left", padx=(6, 0))
        self.e_quota = tk.Entry(qbox, bg="#0f1115", fg=TEXT, insertbackground=TEXT,
                                relief="flat", bd=0, font=(self.f, 9),
                                highlightthickness=0, width=7)
        self.e_quota.pack(padx=8, pady=5)
        self.e_quota.bind("<FocusIn>", lambda ev: qbox.config(highlightbackground=GREEN))
        self.e_quota.bind("<FocusOut>", lambda ev: qbox.config(highlightbackground=LINE))
        self.set_entry(self.e_quota, f"{float(self.cfg.get('week_quota') or 100):g}")
        self.e_quota.bind("<Return>", self.apply_week_cfg)

        wrow = tk.Frame(outer, bg=BG)
        wrow.pack(fill="x", padx=14, pady=(6, 0))
        tk.Label(wrow, text="本周已用 $", bg=BG, fg=MUTED,
                 font=(self.f, 9)).pack(side="left")
        wbox = tk.Frame(wrow, bg="#0f1115", highlightbackground=LINE, highlightthickness=1)
        wbox.pack(side="left", padx=(6, 0))
        self.e_spent = tk.Entry(wbox, bg="#0f1115", fg=MUTED, insertbackground=TEXT,
                                readonlybackground="#0f1115", state="readonly",
                                relief="flat", bd=0, font=(self.f, 9),
                                highlightthickness=0, width=7)
        self.e_spent.pack(padx=8, pady=5)
        spent = self.app.week["spent"] if self.app.week else 0.0
        self.set_entry(self.e_spent, f"{spent:.2f}")
        tk.Label(wrow, text="额度 − 总池剩余", bg=BG, fg=MUTED,
                 font=(self.f, 8)).pack(side="right")

        self.lb_msg = tk.Label(outer, text="", bg=BG, fg=MUTED, font=(self.f, 8), anchor="w")
        self.lb_msg.pack(fill="x", padx=14, pady=(8, 0))

        foot = tk.Frame(outer, bg=BG)
        foot.pack(fill="x", padx=14, pady=(8, 12))
        self.pill(foot, "删除", self.delete, fg=RED).pack(side="left")
        self.save_label = self.pill(foot, "保存并使用", self.save,
                                    fg="#0f1115", bg=GREEN)
        self.save_label.pack(side="right")

        for w in (outer, head):
            w.bind("<Button-1>", self.drag_start)
            w.bind("<B1-Motion>", self.drag_move)

        self.update_idletasks()
        self.lst.config(height=max(1, min(4, len(self.accounts) or 1)))

    def field(self, parent, row, label, show=None):
        tk.Label(parent, text=label, bg=BG, fg=MUTED, font=(self.f, 9),
                 anchor="w").grid(row=row, column=0, sticky="w", pady=4, padx=(0, 9))
        box = tk.Frame(parent, bg="#0f1115", highlightbackground=LINE,
                       highlightthickness=1)
        box.grid(row=row, column=1, sticky="we", pady=4)
        e = tk.Entry(box, bg="#0f1115", fg=TEXT, insertbackground=TEXT,
                     relief="flat", bd=0, font=(self.f, 9), highlightthickness=0)
        if show:
            e.config(show=show)
        e.pack(fill="x", padx=8, pady=6)
        e.bind("<FocusIn>", lambda ev: box.config(highlightbackground=GREEN))
        e.bind("<FocusOut>", lambda ev: box.config(highlightbackground=LINE))
        return e

    def pill(self, parent, text, cmd, fg=TEXT, bg=CARD):
        lb = tk.Label(parent, text=text, bg=bg, fg=fg, font=(self.f, 9),
                      padx=12, pady=5, cursor="hand2")
        dark = fg == "#0f1115"
        lb.bind("<Button-1>", lambda e: cmd())
        lb.bind("<Enter>", lambda e: lb.config(bg="#3ee5ac" if dark else "#2b303a"))
        lb.bind("<Leave>", lambda e: lb.config(bg=bg))
        return lb

    def place_near(self):
        self.update_idletasks()
        h = self.winfo_reqheight()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        rx, ry = self.app.root.winfo_x(), self.app.root.winfo_y()
        x = rx - self.W - 14
        if x < 8:
            x = rx + self.app.w + 14
        if x + self.W > sw - 8:
            x = max(8, (sw - self.W) // 2)
        y = max(8, min(ry, sh - h - 80))
        self.geometry(f"{self.W}x{h}+{x}+{y}")
        self.update_idletasks()

    def drag_start(self, event):
        self._dx = event.x_root - self.winfo_x()
        self._dy = event.y_root - self.winfo_y()

    def drag_move(self, event):
        self.geometry(f"+{event.x_root - self._dx}+{event.y_root - self._dy}")

    # ---------------- 交互

    def set_entry(self, entry, value):
        try:
            ro = entry.cget("state") == "readonly"
        except Exception:
            ro = False
        if ro:
            entry.config(state="normal")
        entry.delete(0, "end")
        entry.insert(0, value or "")
        if ro:
            entry.config(state="readonly")

    def toggle_show(self):
        self.shown = not getattr(self, "shown", False)
        self.e_key.config(show="" if self.shown else "•")
        self.lb_show.config(text="隐藏" if self.shown else "显示",
                            fg=TEXT if self.shown else MUTED)

    def msg(self, text, color=MUTED):
        try:
            self.lb_msg.config(text=text, fg=color)
        except Exception:
            pass

    def reload_list(self, select=None):
        act = self.cfg.get("active")
        self.lst.delete(0, "end")
        if not self.accounts:
            self.lst.insert("end", "  还没有账号 · 点下方「读 magpie / 读 CC Switch」")
            self.lst.itemconfig(0, fg=MUTED)
            self.lst.config(height=1)
            self.fit_height()
            return
        for acc in self.accounts:
            mark = "●" if acc["name"] == act else "○"
            nm = acc["name"] if len(acc["name"]) <= 20 else acc["name"][:19] + "…"
            self.lst.insert("end", f"  {mark}  {nm}      {mask_key(acc['key'])}")
        self.lst.config(height=max(1, min(4, len(self.accounts))))
        idx = next((i for i, a in enumerate(self.accounts) if a["name"] == select), None)
        if idx is not None:
            self.lst.selection_clear(0, "end")
            self.lst.selection_set(idx)
            self.lst.see(idx)
            self.fill_form(self.accounts[idx])
        self.fit_height()

    def fit_height(self):
        """列表行数变了要跟着改窗高，否则底部提示会被切掉。"""
        try:
            self.update_idletasks()
            h = self.winfo_reqheight()
            self.geometry(f"{self.W}x{h}+{self.winfo_x()}+{self.winfo_y()}")
            round_window(self)
        except Exception:
            pass

    def on_pick(self, event=None):
        sel = self.lst.curselection()
        if not sel or sel[0] >= len(self.accounts):
            return
        self.fill_form(self.accounts[sel[0]])

    def fill_form(self, acc):
        self.current = acc.get("name")
        self.set_entry(self.e_name, acc.get("name"))
        self.set_entry(self.e_base, acc.get("base"))
        self.set_entry(self.e_key, acc.get("key"))
        self.msg("")

    def new_account(self):
        self.current = None
        for e in (self.e_name, self.e_base, self.e_key):
            self.set_entry(e, "")
        self.lst.selection_clear(0, "end")
        self.msg("填好后点「保存并使用」")

    def load_from_magpie(self):
        try:
            base, key = read_credentials(self.cfg.get("provider"))
        except Exception as exc:
            self.msg(f"读取失败：{exc}", RED)
            return
        self.current = None
        self.set_entry(self.e_name, self.cfg.get("provider") or "magpie")
        self.set_entry(self.e_base, base)
        self.set_entry(self.e_key, key)
        self.msg("已填入 magpie 的当前配置，保存后即独立")

    def load_from_ccswitch(self):
        """把 CC Switch 里所有带地址和 Key 的供应商导入账号列表。"""
        try:
            items = ccswitch_providers()
        except Exception as exc:
            self.msg(f"读取失败：{exc}", RED)
            return
        if not items:
            self.msg("CC Switch 里没有带地址和 Key 的供应商", RED)
            return

        added, pick = 0, None
        for it in items:
            base, key = it["base"], it["key"]
            same = next((a for a in self.accounts if norm_base(a.get("base")) == base), None)
            if same:                             # 同一个地址已导入过，只同步 Key
                same["key"] = key
                pick = pick or same["name"]
                continue
            name = it["name"] or "cc-switch"
            if any(a.get("name") == name for a in self.accounts):
                name = f"{name} ({url_host(base)})"      # 重名补域名区分
            n = 2
            while any(a.get("name") == name for a in self.accounts):
                name = f"{it['name']} ({url_host(base)} {n})"
                n += 1
            self.accounts.append({"name": name, "base": base, "key": key})
            added += 1
            if it["current"] and pick is None:
                pick = name
        pick = pick or self.accounts[-1]["name"]
        self.reload_list(select=pick)
        if added:
            self.msg(f"已从 CC Switch 导入 {added} 组，选好点「保存并使用」", GREEN)
        else:
            self.msg("CC Switch 的配置都已在列表里，Key 已同步")

    def test(self):
        base = norm_base(self.e_base.get())
        key = self.e_key.get().strip()
        if not base or not key:
            self.msg("先填地址和 Key", RED)
            return
        self.msg("测试中…")

        def work():
            try:
                d = query_balance(base, key)
                text = f"✓ 可用 · 总额 ${d['total']:,.2f} · 剩余 ${d['left']:,.2f}"
                color = GREEN
            except Exception as exc:
                text, color = f"✗ {self.app.short_error(str(exc))}", RED
            self.after(0, lambda: self.msg(text, color))

        threading.Thread(target=work, daemon=True).start()

    def apply_week_cfg(self, event=None):
        """回车即生效，不用改账号也能调额度。"""
        note = self.apply_week_inputs(silent=True)
        if note:
            self.msg("已生效  ·  " + note, GREEN)
            self.app.refresh()

    def apply_week_inputs(self, silent=False):
        """把「每周额度」落到配置并立即重绘。返回变更摘要。"""
        notes = []
        try:
            q = float(self.e_quota.get().strip() or 0)
            if q > 0 and abs(q - float(self.cfg.get("week_quota") or 0)) > 1e-9:
                self.app.set_week_quota(q)
                notes.append(f"额度 ${q:,.0f}")
        except Exception:
            if not silent:
                self.msg("每周额度要填数字", RED)
                return ""
        self.app.repaint()
        spent = self.app.week["spent"] if self.app.week else 0.0
        self.set_entry(self.e_spent, f"{spent:.2f}")
        return "，".join(notes)

    def save(self):
        try:
            self._save()
        except Exception as exc:                     # 出错要看得见，别静默失败
            self.msg("保存失败：" + str(exc)[:36], RED)

    def _save(self):
        name = self.e_name.get().strip()
        base = norm_base(self.e_base.get())
        key = self.e_key.get().strip()
        if not name or not base or not key:
            self.msg("名称、地址、Key 都要填", RED)
            return
        if not base.startswith("http"):
            self.msg("地址要以 http:// 或 https:// 开头", RED)
            return

        item = {"name": name, "base": base, "key": key}
        names = {n for n in (self.current, name) if n}
        idx = next((i for i, a in enumerate(self.accounts) if a["name"] in names), None)
        if idx is None:
            self.accounts.append(item)
        else:
            self.accounts[idx] = item

        self.cfg["accounts"] = self.accounts
        self.cfg["active"] = name
        save_config(self.cfg)
        self.current = name
        self.reload_list(select=name)

        note = self.apply_week_inputs()
        short = name if len(name) <= 18 else name[:17] + "…"
        self.msg("已保存：" + short + ("  ·  " + note if note else ""), GREEN)
        self.app.update_title()
        self.app.repaint()
        self.app.refresh()

    def delete(self):
        name = self.current
        if not name:
            self.msg("先在列表里选一个账号", RED)
            return
        self.accounts = [a for a in self.accounts if a["name"] != name]
        self.cfg["accounts"] = self.accounts
        if self.cfg.get("active") == name:
            self.cfg["active"] = None
        save_config(self.cfg)
        self.current = None
        for e in (self.e_name, self.e_base, self.e_key):
            self.set_entry(e, "")
        self.reload_list(select=self.cfg.get("active"))
        tail = "，回退到 magpie 读取" if not self.cfg.get("active") else ""
        self.msg(f"已删除 {name}{tail}")
        self.app.update_title()
        self.app.refresh()
