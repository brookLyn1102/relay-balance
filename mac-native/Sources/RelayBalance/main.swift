import AppKit
import SwiftUI

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem!
    private var popover: NSPopover!
    private let model = Model()
    private var settingsWindow: NSWindow?
    private var usageWindow: NSWindow?

    func applicationDidFinishLaunching(_ notification: Notification) {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = statusItem.button {
            button.target = self
            button.action = #selector(handleClick)
            button.sendAction(on: [.leftMouseUp, .rightMouseUp])
            button.font = NSFont.monospacedDigitSystemFont(ofSize: 11, weight: .regular)
        }

        setupPopover()
        model.onChange = { [weak self] in self?.updateTitle() }
        model.start()
        updateTitle()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }

    // MARK: 状态栏

    private func updateTitle() {
        guard let button = statusItem.button else { return }
        var parts: [String] = []
        if model.cfg.menuSpeed {
            parts.append("↑\(fmtSpeedShort(model.up)) ↓\(fmtSpeedShort(model.down))")
        }
        if model.cfg.menuBalance {
            parts.append(model.balance.map { fmtMoney($0.left) } ?? "—")
        }
        if model.cfg.menuToken, let t = model.todayTok, t.total > 0 {
            parts.append(fmtTokens(t.total))
        }
        if model.cfg.menuMem, let m = model.mem {
            parts.append("内存 \(fmtMem(m.availableGB))")
        }
        let text = parts.isEmpty ? APP_NAME : " " + parts.joined(separator: "  ")
        button.attributedTitle = NSAttributedString(
            string: text,
            attributes: [.font: NSFont.monospacedDigitSystemFont(ofSize: 11, weight: .regular)]
        )
    }

    @objc private func handleClick() {
        if let event = NSApp.currentEvent, event.type == .rightMouseUp {
            showMenu()
        } else {
            togglePopover()
        }
    }

    private func showMenu() {
        let menu = NSMenu()

        let refresh = NSMenuItem(title: "立即刷新余额", action: #selector(refreshNow), keyEquivalent: "r")
        refresh.target = self
        menu.addItem(refresh)

        let intervalItem = NSMenuItem(title: "刷新间隔", action: nil, keyEquivalent: "")
        let intervalMenu = NSMenu()
        for (sec, label) in INTERVALS {
            let item = NSMenuItem(title: label, action: #selector(setIntervalAction(_:)), keyEquivalent: "")
            item.target = self
            item.representedObject = sec
            item.state = model.cfg.interval == sec ? .on : .off
            intervalMenu.addItem(item)
        }
        intervalItem.submenu = intervalMenu
        menu.addItem(intervalItem)

        let night = NSMenuItem(
            title: "夜间暂停 \(fmtHM(model.cfg.nightFrom))–\(fmtHM(model.cfg.nightTo))",
            action: #selector(toggleNight), keyEquivalent: "")
        night.target = self
        night.state = model.cfg.nightPause ? .on : .off
        menu.addItem(night)

        let tokenOnly = NSMenuItem(title: "有用量才查余额",
                                   action: #selector(toggleTokenRefresh), keyEquivalent: "")
        tokenOnly.target = self
        tokenOnly.state = model.cfg.refreshOnToken ? .on : .off
        menu.addItem(tokenOnly)

        menu.addItem(.separator())

        let panel = NSMenuItem(title: "显示面板", action: #selector(openPanel), keyEquivalent: "")
        panel.target = self
        menu.addItem(panel)

        let usage = NSMenuItem(title: "用量统计…", action: #selector(openUsage), keyEquivalent: "")
        usage.target = self
        menu.addItem(usage)

        let settings = NSMenuItem(title: "设置 / 换 Key", action: #selector(openSettings), keyEquivalent: ",")
        settings.target = self
        menu.addItem(settings)

        let update = NSMenuItem(title: updateMenuTitle(), action: #selector(checkUpdateAction), keyEquivalent: "")
        update.target = self
        menu.addItem(update)

        menu.addItem(.separator())

        let quit = NSMenuItem(title: "退出", action: #selector(quitApp), keyEquivalent: "q")
        quit.target = self
        menu.addItem(quit)

        statusItem.menu = menu
        statusItem.button?.performClick(nil)
        statusItem.menu = nil
    }

    // MARK: 面板

    private func setupPopover() {
        let pop = NSPopover()
        pop.behavior = .transient
        pop.animates = true
        pop.contentViewController = NSHostingController(rootView: PanelView(
            model: model,
            onClose: { [weak self] in self?.popover.performClose(nil) },
            onSettings: { [weak self] in self?.openSettings() },
            onUsage: { [weak self] in self?.openUsage() }
        ))
        popover = pop
    }

    @objc private func togglePopover() {
        guard let button = statusItem.button else { return }
        if popover.isShown {
            popover.performClose(nil)
        } else {
            popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
            popover.contentViewController?.view.window?.makeKey()
        }
    }

    @objc private func openPanel() {
        if !popover.isShown { togglePopover() }
    }

    // MARK: 动作

    @objc private func refreshNow() {
        model.refresh()
    }

    @objc private func setIntervalAction(_ sender: NSMenuItem) {
        if let sec = sender.representedObject as? Int {
            model.setInterval(sec)
        }
    }

    @objc private func toggleNight() {
        model.toggleNight()
    }

    @objc private func toggleTokenRefresh() {
        model.toggleTokenRefresh()
    }

    // MARK: 更新

    private func updateMenuTitle() -> String {
        if model.checkingUpdate { return "检查更新中…" }
        if let info = model.update, info.behind > 0 {
            return "可更新 · \(info.remote ?? "")"
        }
        return "检查更新（v\(Updater.version)）"
    }

    @objc private func checkUpdateAction() {
        if let info = model.update, info.behind > 0 {
            // .app 是本地编译的，自己拉不了代码，直接开提交页
            NSWorkspace.shared.open(info.url)
        } else {
            model.checkUpdate(manual: true)
        }
    }

    @objc private func quitApp() {
        NSApp.terminate(nil)
    }

    // MARK: 窗口

    @objc private func openSettings() {
        if settingsWindow == nil {
            let window = NSWindow(
                contentRect: NSRect(x: 0, y: 0, width: 520, height: 560),
                styleMask: [.titled, .closable],
                backing: .buffered,
                defer: false)
            window.title = "设置"
            window.isReleasedWhenClosed = false
            window.level = .floating
            window.contentView = NSHostingView(rootView: SettingsView(
                model: model,
                onClose: { [weak window] in window?.close() }
            ))
            window.center()
            settingsWindow = window
        }
        popover.performClose(nil)
        NSApp.activate(ignoringOtherApps: true)
        settingsWindow?.makeKeyAndOrderFront(nil)
    }

    @objc private func openUsage() {
        if usageWindow == nil {
            let window = NSWindow(
                contentRect: NSRect(x: 0, y: 0, width: 520, height: 640),
                styleMask: [.titled, .closable],
                backing: .buffered,
                defer: false)
            window.title = "用量统计"
            window.isReleasedWhenClosed = false
            window.level = .floating
            window.contentView = NSHostingView(rootView: UsageView(
                onClose: { [weak window] in window?.close() }
            ))
            window.center()
            usageWindow = window
        }
        popover.performClose(nil)
        NSApp.activate(ignoringOtherApps: true)
        usageWindow?.makeKeyAndOrderFront(nil)
    }
}

// MARK: - 入口

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
_ = app.setActivationPolicy(.accessory)
app.run()
