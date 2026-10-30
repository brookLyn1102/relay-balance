"""macOS regression checks: python3 -m unittest discover -s mac/tests -v."""
import copy
import fcntl
import os
from pathlib import Path
import signal
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import relay_menubar as menubar
import relay_panel as panel


class MenuTests(unittest.TestCase):
    def setUp(self):
        patches = [
            patch.object(menubar.core, "load_config", return_value=copy.deepcopy(menubar.core.DEFAULT_CONFIG)),
            patch.object(menubar.MenuBarApp, "refresh"),
            patch.object(menubar.MenuBarApp, "autostart_on", return_value=False),
            patch.object(menubar.rumps.Timer, "start"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.app = menubar.MenuBarApp()

    def test_checked_menu_items(self):
        self.assertEqual(self.app.menu["菜单栏显示速度"].state, 1)
        self.assertEqual(self.app.menu["开机自启"].state, 0)

    def test_night_pause_menu_is_checked(self):
        label = (f"夜间暂停 {menubar.core.fmt_hm(self.app.cfg['night_from'])}–"
                 f"{menubar.core.fmt_hm(self.app.cfg['night_to'])}")
        item = self.app.menu[label]
        self.assertEqual(item.state, 1)
        with patch.object(menubar.core, "save_config"):
            self.app.toggle_night(item)
        self.assertEqual(item.state, 0)
        self.assertFalse(self.app.cfg["night_pause"])
        self.app.refresh.assert_called()

    def test_refresh_submenu(self):
        self.assertEqual(len(self.app.interval_items), 6)
        self.assertEqual(self.app.interval_item["10 分钟"].state, 1)
        self.assertEqual(self.app.interval_item["30 秒"].state, 0)

    def test_existing_panel_is_raised_not_closed(self):
        child = self.app.panel = MagicMock()
        child.poll.return_value = None
        self.app.show_panel()
        child.send_signal.assert_called_once_with(signal.SIGUSR1)
        child.terminate.assert_not_called()

    def test_closed_panel_is_recreated(self):
        self.app.panel = MagicMock()
        self.app.panel.poll.return_value = 0
        with patch.object(menubar.subprocess, "Popen") as popen:
            self.app.show_panel()
        self.assertIs(self.app.panel, popen.return_value)
        self.assertIsNone(popen.call_args.kwargs["stderr"])

    def test_signal_only_enqueues_ui_work(self):
        self.app._show_requested = False
        with patch.object(self.app, "show_panel") as show:
            self.app.request_show()
            self.assertTrue(self.app._show_requested)
            show.assert_not_called()

    def test_finder_reopen_enqueues_show(self):
        from rumps.rumps import NSApp
        self.app._show_requested = False
        menubar.install_reopen_handler(self.app)
        delegate = NSApp.alloc().init()
        self.assertFalse(delegate.applicationShouldHandleReopen_hasVisibleWindows_(None, False))
        self.assertTrue(self.app._show_requested)


class InstanceTests(unittest.TestCase):
    def test_lock_records_pid_and_can_be_reacquired_after_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config", "menubar.lock")
            lock = menubar.acquire_instance(path)
            try:
                self.assertEqual(Path(path).read_text(), str(os.getpid()))
            finally:
                lock.close()
            lock = menubar.acquire_instance(path)
            self.assertIsNotNone(lock)
            lock.close()

    def test_duplicate_launch_notifies_existing_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "menubar.lock")
            lock = menubar.acquire_instance(path)
            try:
                with patch.object(menubar.os, "kill") as kill:
                    self.assertIsNone(menubar.acquire_instance(path))
                    kill.assert_called_once_with(os.getpid(), signal.SIGUSR1)
            finally:
                lock.close()

    def test_invalid_pid_does_not_signal_a_process_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "menubar.lock")
            with open(path, "w+") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                lock.write("0")
                lock.flush()
                with patch.object(menubar.os, "kill") as kill:
                    self.assertIsNone(menubar.acquire_instance(path))
                    kill.assert_not_called()

    def test_background_does_not_show_panel(self):
        with patch.object(menubar, "acquire_instance", return_value=MagicMock()), \
             patch.object(menubar.signal, "signal"), \
             patch.object(menubar, "install_reopen_handler"), \
             patch.object(menubar, "MenuBarApp") as app:
            menubar.main(["--background"])
            self.assertFalse(app.return_value._show_requested)
            app.return_value.run.assert_called_once()

    def test_manual_launch_shows_panel(self):
        with patch.object(menubar, "acquire_instance", return_value=MagicMock()), \
             patch.object(menubar.signal, "signal"), \
             patch.object(menubar, "install_reopen_handler"), \
             patch.object(menubar, "MenuBarApp") as app:
            menubar.main([])
            self.assertTrue(app.return_value._show_requested)


class PanelTests(unittest.TestCase):
    def test_bring_to_front_clamps_offscreen_position(self):
        widget = MagicMock()
        widget.W = 320
        widget.root.winfo_screenwidth.return_value = 1440
        widget.root.winfo_screenheight.return_value = 900
        widget.root.winfo_height.return_value = 280
        widget.root.winfo_x.return_value = 3000
        widget.root.winfo_y.return_value = -100
        panel.Panel.bring_to_front(widget)
        widget.root.geometry.assert_called_once_with("+1120+40")
        widget.root.deiconify.assert_called_once()
        widget.root.lift.assert_called_once()
        widget.root.focus_force.assert_called_once()


if __name__ == "__main__":
    unittest.main()
