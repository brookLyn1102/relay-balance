# -*- coding: utf-8 -*-
"""中转管家 · 菜单栏（macOS 版）

菜单栏右侧显示 `↑12K ↓3.4M $64.43 1.0M tok 内存 6.4G`（每一项都能关掉），
点开是菜单：显示面板 / 立即刷新 / 刷新间隔 / 开机自启 / 退出。

面板是另一个进程（relay_panel.py），这里负责开关它。
"""

import argparse
import fcntl
import os
import signal
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


def checked_item(title, callback, state):
    """rumps 0.4 takes state as a property, not a constructor argument."""
    item = rumps.MenuItem(title, callback=callback)
    item.state = bool(state)
    return item


class MenuBarApp(rumps.App):
    def __init__(self):
        super().__init__(core.APP_NAME, title=core.APP_NAME, quit_button=None)
        self.cfg = core.load_config()
        self.net = core.NetMeter()
        self.data = None
        self.error = None
        self.busy = False
        self.panel = None
        self._show_requested = True
        self.last_at = 0.0
        self.mem_gb, self.mem_pct = core.mem_status()   # 先取一次，别让标题闪「—」

        self.mi_panel = rumps.MenuItem("显示面板", callback=self.toggle_panel)
        self.interval_item = self.interval_menu()
        self.menu = [
            self.mi_panel,
            None,
            rumps.MenuItem("立即刷新余额", callback=self.force_refresh),
            self.interval_item,
            checked_item(
                f"夜间暂停 {core.fmt_hm(self.cfg['night_from'])}–"
                f"{core.fmt_hm(self.cfg['night_to'])}",
                callback=self.toggle_night,
                state=self.cfg.get("night_pause", True)),
            None,
            checked_item("面板显示网速", callback=self.toggle_net,
                           state=self.cfg.get("net_panel", True)),
            checked_item("菜单栏显示速度", callback=self.toggle_speed,
                           state=self.cfg.get("menu_speed", True)),
            checked_item("菜单栏显示余额", callback=self.toggle_balance,
                           state=self.cfg.get("menu_balance", True)),
            checked_item("菜单栏显示今日 token", callback=self.toggle_token,
                           state=self.cfg.get("menu_token", True)),
            checked_item("菜单栏显示剩余内存", callback=self.toggle_mem,
                           state=self.cfg.get("menu_mem", True)),
            None,
            checked_item("开机自启", callback=self.toggle_autostart,
                           state=self.autostart_on()),
            rumps.MenuItem("退出", callback=self.quit),
        ]

        self.timer_net = rumps.Timer(self.on_second, 1)
        self.timer_net.start()
        if not core.in_night(self.cfg):
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
        submenu = rumps.MenuItem("刷新间隔")
        submenu.update(items)
        return submenu

    def make_set_interval(self, sec):
        label = next(l for s, l in core.INTERVALS if s == sec)

        def handler(_sender):
            self.cfg["interval"] = int(sec)
            core.save_config(self.cfg)
            for name, item in self.interval_items.items():
                item.state = (name == label)
            self.refresh()
        return handler

    def toggle_night(self, sender):
        self.cfg["night_pause"] = not self.cfg.get("night_pause", True)
        sender.state = self.cfg["night_pause"]
        core.save_config(self.cfg)
        if not self.cfg["night_pause"]:
            self.refresh()

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

    def toggle_mem(self, sender):
        self.cfg["menu_mem"] = not self.cfg.get("menu_mem", True)
        sender.state = self.cfg["menu_mem"]
        core.save_config(self.cfg)
        self.update_title()

    def force_refresh(self, _sender=None):
        self.refresh()

    def quit(self, _sender=None):
        self.stop_panel()
        rumps.quit_application()

    # ------------------------------------------------------------ 面板进程

    def request_show(self, *_args):
        # Signal and Cocoa handlers only enqueue work; the timer owns the UI.
        self._show_requested = True

    def show_panel(self):
        if self.panel is not None and self.panel.poll() is None:
            self.panel.send_signal(signal.SIGUSR1)
        else:
            self.toggle_panel()

    def toggle_panel(self, _sender=None):
        if self.panel is not None and self.panel.poll() is None:
            self.stop_panel()
            return
        if not os.path.exists(PANEL):
            rumps.alert("找不到 relay_panel.py", PANEL)
            return
        self.panel = subprocess.Popen([sys.executable, PANEL], cwd=HERE,
                                      stdout=None, stderr=None)
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
        if self._show_requested:
            self._show_requested = False
            self.show_panel()
        self.net.sample()
        if self.cfg.get("menu_mem", True):
            self.mem_gb, self.mem_pct = core.mem_status()
        self._tok_tick = getattr(self, "_tok_tick", 0) + 1
        if self._tok_tick % 5 == 1:              # 每 5 秒读一次日志增量
            try:
                self.today_tok = usage.today_live()
            except Exception:
                self.today_tok = {}
        if (not core.in_night(self.cfg) and not self.busy
                and time.time() - getattr(self, "last_at", 0) >= self.cfg["interval"]):
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
        if self.cfg.get("menu_mem", True):
            parts.append(f"内存 {core.fmt_mem(getattr(self, 'mem_gb', None))}")
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
    <string>--background</string>
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


def acquire_instance(path):
    """Keep the lock open for the app lifetime; another launch reveals its panel."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    lock = os.fdopen(fd, "r+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        try:
            pid = int(lock.read().strip())
            if pid > 1:
                os.kill(pid, signal.SIGUSR1)
        except (ValueError, ProcessLookupError):
            pass
        finally:
            lock.close()
        return None
    lock.seek(0)
    lock.truncate()
    lock.write(str(os.getpid()))
    lock.flush()
    return lock


def install_reopen_handler(app):
    """Finder may deliver a reopen event instead of launching a second process."""
    import objc
    from rumps.rumps import NSApp

    def reopen(_delegate, _sender, _visible):
        app.request_show()
        return False

    callback = objc.selector(
        reopen,
        selector=b"applicationShouldHandleReopen:hasVisibleWindows:",
        signature=objc._C_NSBOOL + b"@:@" + objc._C_NSBOOL,
    )
    objc.classAddMethods(NSApp, [callback])


def main(argv=None):
    parser = argparse.ArgumentParser(description=core.APP_NAME)
    parser.add_argument("--background", action="store_true",
                        help="只启动菜单栏，不自动显示面板（用于开机自启）")
    args = parser.parse_args(argv)
    # Install before publishing our PID, so a concurrent launch cannot terminate us.
    pending_show = [False]
    signal.signal(signal.SIGUSR1, lambda *_: pending_show.__setitem__(0, True))
    lock = acquire_instance(os.path.join(core.CONFIG_DIR, "menubar.lock"))
    if lock is None:
        return
    try:
        app = MenuBarApp()
        app._show_requested = not args.background or pending_show[0]
        signal.signal(signal.SIGUSR1, app.request_show)
        install_reopen_handler(app)
        app.run()
    finally:
        lock.close()


if __name__ == "__main__":
    main()
