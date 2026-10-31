"""Tk 悬浮网速条：TaskbarStrip 挂不上任务栏时的退路。"""
import tkinter as tk

from PIL import Image, ImageDraw, ImageFont, ImageTk

from rb_core import AMBER, BG, RED, fmt_money, fmt_speed, save_config
from rb_win import (as_tool_window, raise_over_taskbar, taskbar_is_light,
                    taskbar_metrics, window_covered)


# ---------------------------------------------------------------- 网速条

FONT_MONO = r"C:\Windows\Fonts\consola.ttf"
FONT_MONO_BOLD = r"C:\Windows\Fonts\consolab.ttf"
FONT_UI = r"C:\Windows\Fonts\msyh.ttc"
KEY_COLOR = "#ff00fe"          # 透明色key（几乎不会用到）


def load_font(size):
    """等宽加粗字体，取不到就退回普通体。"""
    for path in (FONT_MONO_BOLD, FONT_MONO):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


class TrafficStrip(tk.Toplevel):
    """贴在任务栏上的实时网速条。

    Win11 任务栏是 XAML 合成层，SetParent 进去会被盖住，所以这里做成
    无边框、不抢焦点、始终置顶的独立小窗，直接压在任务栏对应位置。
    """

    def __init__(self, app, mode="taskbar"):
        super().__init__(app.root)
        self.app = app
        self.cfg = app.cfg
        self.mode = mode
        self.hwnd = None
        self._img = None
        self._pos = None
        self._transparent = False

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        try:
            self.attributes("-transparentcolor", KEY_COLOR)
            self._transparent = True
        except Exception:
            pass

        self.apply_theme()
        self.render()
        self.place()
        self.hwnd = as_tool_window(self)
        if self.mode == "taskbar":
            raise_over_taskbar(self.hwnd)
            self._guard_hits = 0
            self._guard()

    # ---------------- 外观

    def apply_theme(self):
        self.transparent = bool(self.cfg.get("bar_transparent", True)) and self._transparent
        self.light = taskbar_is_light()
        if self.transparent:
            # 抠掉底色，只留文字；key 色取接近任务栏的颜色，抗锯齿边缘不突兀
            self.key_color = "#f3f4f6" if self.light else "#1f2229"
            self.bg = self.key_color
            if self.light:
                self.fg, self.c_up, self.c_down = "#1a1d23", "#2563eb", "#059669"
            else:
                self.fg, self.c_up, self.c_down = "#e8eaed", "#5aa9ff", "#34d399"
            self.c_bal = self.fg
            self.c_good, self.c_warn, self.c_bad = (
                ("#059669", "#b45309", "#dc2626") if self.light else ("#34d399", AMBER, RED))
            try:
                self.attributes("-transparentcolor", self.key_color)
            except Exception:
                self.transparent = False
        if not self.transparent:
            self.key_color = KEY_COLOR
            dark = bool(self.cfg.get("bar_dark", True))
            if dark:
                self.bg, self.fg = BG, "#e8eaed"      # 跟右键菜单同一底色
                self.c_up, self.c_down, self.c_bal = "#5aa9ff", "#34d399", "#e8eaed"
                self.c_good, self.c_warn, self.c_bad = "#34d399", AMBER, RED
            else:
                self.bg, self.fg = "#f4f5f7", "#1a1d23"
                self.c_up, self.c_down, self.c_bal = "#2563eb", "#059669", "#111827"
                self.c_good, self.c_warn, self.c_bad = "#059669", "#b45309", "#dc2626"
        try:
            self.configure(bg=self.bg)      # 窗口自身底色也跟上，resize 瞬间不露馅
        except Exception:
            pass

        if self.mode == "taskbar":
            m = taskbar_metrics()
            self.h = m[2] if m else 40
        else:
            self.h = 34
        self.stack = bool(self.cfg.get("bar_stack", True))
        size = max(12, int(self.h * (0.30 if self.stack else 0.34)))
        self.f = load_font(size)

        self.pad = max(8, int(self.h * (0.22 if self.stack else 0.26)))
        self.gap = max(8, int(self.h * 0.28))
        # 用最宽的可能值定宽度，数字变化时不会抖动
        up_w = self.f.getlength("↑ 999.9 MB/s")
        dn_w = self.f.getlength("↓ 999.9 MB/s")
        if self.stack:
            net_w = max(up_w, dn_w)
        else:
            net_w = up_w + self.gap + dn_w
        w = self.pad * 2 + net_w
        if self.cfg.get("bar_show_balance", True):
            w += self.gap + self.f.getlength("$9,999.99")
        self.w = int(w) + 4

    def _segments(self):
        segs = [("↑ " + fmt_speed(self.app.net.up), self.c_up),
                ("↓ " + fmt_speed(self.app.net.down), self.c_down)]
        if self.cfg.get("bar_show_balance", True):
            w = self.app.week
            if w:
                segs.append((fmt_money(w["left"]), self.c_color(w["pct"])))
            else:
                segs.append(("$—", self.c_bal))
        return segs

    def c_color(self, pct):
        """和面板进度条同一套阈值：<80 绿、<95 琥珀、≥95 红。"""
        if pct >= 95:
            return self.c_bad
        if pct >= 80:
            return self.c_warn
        return self.c_good

    def render(self):
        size = (self.w, self.h)
        base = self.key_color if self.transparent else self.bg
        if getattr(self, "_photo", None) is None:
            self._pil = Image.new("RGB", size, base)
            self._photo = ImageTk.PhotoImage(self._pil)
            self.label = tk.Label(self, image=self._photo, bd=0,
                                  bg=base, highlightthickness=0)
            self.label.pack(fill="both", expand=True)
            self.bind_events()
        elif self._pil.size != size:
            self._pil = Image.new("RGB", size, base)
            self._photo = ImageTk.PhotoImage(self._pil)
            self.label.config(image=self._photo)
        self.label.config(bg=base)

        d = ImageDraw.Draw(self._pil)
        d.rectangle([0, 0, self.w - 1, self.h - 1], fill=base)
        if not self.transparent:
            inset = max(2, int(self.h * 0.08))
            radius = max(4, int(self.h * 0.22))
            d.rounded_rectangle([0, inset, self.w - 1, self.h - inset - 1],
                                radius=radius, fill=self.bg)
        cy = self.h // 2
        segs = self._segments()
        nets, bal = segs[:2], (segs[2] if len(segs) > 2 else None)
        x = self.pad
        if self.stack:
            dy = max(6, int(self.h * 0.215))
            for i, (text, color) in enumerate(nets):
                d.text((x, cy - dy + i * dy * 2), text, font=self.f,
                       fill=color, anchor="lm")
            x += max(self.f.getlength(t) for t, _ in nets) + self.gap
            if bal:
                d.text((x, cy), bal[0], font=self.f, fill=bal[1], anchor="lm")
        else:
            for text, color in segs:
                d.text((x, cy), text, font=self.f, fill=color, anchor="lm")
                x += self.f.getlength(text) + self.gap
        self._photo.paste(self._pil)

    # ---------------- 位置

    def place(self):
        if self.mode == "taskbar":
            m = taskbar_metrics()
            if not m:
                return
            top, right, th = m
            if th != self.h:
                self.apply_theme()
                self.render()
            x = right - self.w - 8 + int(self.cfg.get("bar_dx") or 0)
            y = top + int(self.cfg.get("bar_dy") or 0)
        else:
            x, y = self.cfg.get("bar_x"), self.cfg.get("bar_y")
            if x is None or y is None:
                x = self.winfo_screenwidth() - self.w - 80
                y = self.winfo_screenheight() - 160
            x, y = int(x), int(y)
        if self._pos == (x, y, self.w, self.h):
            return
        self._pos = (x, y, self.w, self.h)
        self.geometry(f"{self.w}x{self.h}+{x}+{y}")

    def tick(self):
        self.render()
        self.place()
        if self.mode == "taskbar":
            self._n = getattr(self, "_n", 0) + 1
            if self._n % 5 == 1:        # 别每帧都抢 band，够用且不闪
                raise_over_taskbar(self.hwnd)
        # 遮挡检测交给 _guard 高频跑，这里不管

    def _guard(self):
        """高频保活：鼠标悬停任务栏图标时，Windows 弹缩略图预览并把任务栏
        重新提到最顶层，贴在任务栏上的条会被压下去。主 tick 一秒才查一次，
        那一段就是肉眼看到的「短暂消失」。这里用 200ms 查一次，刚被压过
        之后连续几次提到 70ms，闪烁基本看不出来。
        """
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        try:
            if window_covered(self.hwnd):
                raise_over_taskbar(self.hwnd, force_top=True)
                self._guard_hits = 8
            elif self._guard_hits > 0:
                self._guard_hits -= 1
        except Exception:
            pass
        delay = 70 if self._guard_hits > 0 else 200
        try:
            self.after(delay, self._guard)
        except Exception:
            pass

    # ---------------- 交互

    def bind_events(self):
        targets = [self, getattr(self, "label", None)]
        for w in targets:
            if w is None:
                continue
            w.bind("<Button-3>", self.popup)
            w.bind("<Double-Button-1>", lambda e: self.app.show())
            w.bind("<Button-1>", self.drag_start)
            w.bind("<B1-Motion>", self.drag_move)
            w.bind("<ButtonRelease-1>", self.drag_end)

    def popup(self, event):
        self.app.build_menu()
        self.app.menu.tk_popup(event.x_root, event.y_root)

    def drag_start(self, event):
        self._dx = event.x_root - self.winfo_x()
        self._dy = event.y_root - self.winfo_y()

    def drag_move(self, event):
        if self.mode != "float":
            return
        self._pos = None
        self.geometry(f"+{event.x_root - self._dx}+{event.y_root - self._dy}")

    def drag_end(self, event):
        if self.mode != "float":
            return
        self.cfg["bar_x"] = self.winfo_x()
        self.cfg["bar_y"] = self.winfo_y()
        save_config(self.cfg)
        self._pos = None
