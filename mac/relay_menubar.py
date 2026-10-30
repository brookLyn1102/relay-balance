# -*- coding: utf-8 -*-
"""中转管家 · 菜单栏（macOS 版）

菜单栏右侧显示 `↑12K ↓3.4M $64.43`（可关掉其中任一项），点开是菜单：
显示面板 / 立即刷新 / 刷新间隔 / 开机自启 / 退出。

面板是另一个进程（relay_panel.py），这里负责开关它。
"""

import os
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rumps

import relay_core as core
import usage_stats as usage

HERE = os.path.dirname(os.path.abspath(__file__))
PANEL = os.path.join(HERE, "relay_panel.py")
LABEL = "com.relaybalance.menubar"
PLIST = os.path.expanduser(f"~/Library/LaunchAgents/{LABEL}.plist")


class MenuBarApp(rumps.App):
    def __init__(self):
        super().__init__(core.APP_NAME, title=core.APP_NAME, quit_button=None)
        self.cfg = core.load_config()
        self.net = core.NetMeter()
        self.data = None
        self.error = None
        self.busy = False
        self.panel = None
        self.last_at = 0.0

        self.mi_panel = rumps.MenuItem("显示面板", callback=self.toggle_panel)
        self.interval_item = self.interval_menu()
        self.menu = [
            self.mi_panel,
            None,
            rumps.MenuItem("立即刷新余额", callback=self.force_refresh),
            self.interval_item,
            None,
            rumps.MenuItem("面板显示网速", callback=self.toggle_net,
                           state=self.cfg.get("net_panel", True)),
            rumps.MenuItem("菜单栏显示速度", callback=self.toggle_speed,
                           state=self.cfg.get("menu_speed", True)),
            rumps.MenuItem("菜单栏显示余额", callback=self.toggle_balance,
                           state=self.cfg.get("menu_balance", True)),
            rumps.MenuItem("菜单栏显示今日 token", callback=self.toggle_token,
                           state=self.cfg.get("menu_token", True)),
            None,
            rumps.MenuItem("开机自启", callback=self.toggle_autostart,
                           state=self.autostart_on()),
            rumps.MenuItem("退出", callback=self.quit),
        ]

        self.timer_net = rumps.Timer(self.on_second, 1)
        self.timer_net.start()
        self.refresh()

    # ------------------------------------------------------------ 菜单

    def interval_menu(self):
        items = []
        self.interval_items = {}
        for sec, label in core.INTERVALS:
            item = rumps.MenuItem(label, callback=self.make_set_interval(sec))
            item.state = self.cfg["interval"] == sec
            items.append(item)
            self.interval_items[label] = item
        return rumps.MenuItem("刷新间隔", items)

    def make_set_interval(self, sec):
        label = next(l for s, l in core.INTERVALS if s == sec)

        def handler(_sender):
            self.cfg["interval"] = int(sec)
            core.save_config(self.cfg)
            for name, item in self.interval_items.items():
                item.state = (name == label)
            self.refresh()
        return handler

    def toggle_net(self, sender):
        self.cfg["net_panel"] = not self.cfg.get("net_panel", True)
        sender.state = self.cfg["net_panel"]
        core.save_config(self.cfg)

    def toggle_speed(self, sender):
        self.cfg["menu_speed"] = not self.cfg.get("menu_speed", True)
        sender.state = self.cfg["menu_speed"]
        core.save_config(self.cfg)
        self.update_title()

    def toggle_balance(self, sender):
        self.cfg["menu_balance"] = not self.cfg.get("menu_balance", True)
        sender.state = self.cfg["menu_balance"]
        core.save_config(self.cfg)
        self.update_title()

    def toggle_token(self, sender):
        self.cfg["menu_token"] = not self.cfg.get("menu_token", True)
        sender.state = self.cfg["menu_token"]
        core.save_config(self.cfg)
        self.update_title()

    def force_refresh(self, _sender=None):
        self.refresh()

    def quit(self, _sender=None):
        self.stop_panel()
        rumps.quit_application()

    # ------------------------------------------------------------ 面板进程

    def toggle_panel(self, _sender=None):
        if self.panel is not None and self.panel.poll() is None:
            self.stop_panel()
            return
        if not os.path.exists(PANEL):
            rumps.alert("找不到 relay_panel.py", PANEL)
            return
        self.panel = subprocess.Popen([sys.executable, PANEL], cwd=HERE,
                                      stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL)
        self.mi_panel.title = "隐藏面板"

    def stop_panel(self):
        if self.panel is not None and self.panel.poll() is None:
            self.panel.terminate()
            try:
                self.panel.wait(timeout=3)
            except Exception:
                self.panel.kill()
        self.panel = None
        self.mi_panel.title = "显示面板"

    # ------------------------------------------------------------ 数据

    def refresh(self):
        if self.busy:
            return
        self.busy = True
        cfg = dict(self.cfg)
        threading.Thread(target=self._worker, args=(cfg,), daemon=True).start()

    def _worker(self, cfg):
        try:
            self.data = core.fetch_balance(cfg)
            self.error = None
        except Exception as exc:
            self.error = str(exc)[:40]
        self.busy = False
        self.last_at = time.time()

    def on_second(self, _sender):
        self.net.sample()
        self._tok_tick = getattr(self, "_tok_tick", 0) + 1
        if self._tok_tick % 5 == 1:              # 每 5 秒读一次日志增量
            try:
                self.today_tok = usage.today_live()
            except Exception:
                self.today_tok = {}
        if not self.busy and time.time() - getattr(self, "last_at", 0) >= self.cfg["interval"]:
            self.refresh()
        self.update_title()

    def update_title(self):
        parts = []
        if self.cfg.get("menu_speed", True):
            parts.append(f"↑{core.fmt_speed_short(self.net.up)} "
                         f"↓{core.fmt_speed_short(self.net.down)}")
        if self.cfg.get("menu_balance", True):
            if self.data:
                parts.append(core.fmt_money(self.data["left"]))
            elif self.error:
                parts.append("离线")
            else:
                parts.append("$—")
        if self.cfg.get("menu_token", True):
            t = getattr(self, "today_tok", None) or {}
            total = (t.get("in", 0) or 0) + (t.get("out", 0) or 0)
            if total:
                parts.append(f"{usage.fmt_tokens(total)} tok")
        self.title = "  ".join(parts) if parts else core.APP_NAME

    # ------------------------------------------------------------ 自启

    def autostart_on(self):
        return os.path.exists(PLIST)

    def toggle_autostart(self, sender):
        if self.autostart_on():
            try:
                os.remove(PLIST)
                subprocess.run(["launchctl", "unload", PLIST],
                               capture_output=True)
            except Exception:
                pass
            sender.state = False
            return

        os.makedirs(os.path.dirname(PLIST), exist_ok=True)
        python = sys.executable
        plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{python}</string>
    <string>{os.path.join(HERE, "relay_menubar.py")}</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><false/>
</dict>
</plist>
"""
        with open(PLIST, "w", encoding="utf-8") as f:
            f.write(plist)
        subprocess.run(["launchctl", "load", PLIST], capture_output=True)
        sender.state = True


if __name__ == "__main__":
    MenuBarApp().run()
