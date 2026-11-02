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

    /// 菜单栏上的一组内容：上行是数字，下行是第二行（速度的下行是下载值，
    /// 内存 / token 的下行是说明性小字）。
    private struct StatusBlock {
        var top: String
        var bottom: String?
        var bottomSmall: Bool = false
    }

    /// 把若干组内容画成一张 template 图。
    ///
    /// 不能用 title 拼换行——NSStatusBarButton 的 cell 是单行的，换行会被裁。
    /// 菜单栏高度是 NSStatusBar.system.thickness（22pt），9pt 字一行占 11pt，
    /// 两行正好 22pt；11pt 要 26pt 会露出半行。所以数字一律 9pt，
    /// MEM / tok 这类说明性小字再降到 7.5pt，既省高度又有层次。
    /// 画成 template 图后，深浅色菜单栏下会自动反色。
    private func statusImage(_ blocks: [StatusBlock]) -> NSImage {
        let mainFont = NSFont.monospacedDigitSystemFont(ofSize: 9, weight: .regular)
        let capFont = NSFont.monospacedDigitSystemFont(ofSize: 7.5, weight: .medium)
        let thickness = NSStatusBar.system.thickness
        let lineH = ceil(mainFont.ascender - mainFont.descender + mainFont.leading)
        let capH = ceil(capFont.ascender - capFont.descender + capFont.leading)
        let columnGap: CGFloat = 7
        let padX: CGFloat = 1

        func font(for block: StatusBlock) -> NSFont {
            block.bottomSmall ? capFont : mainFont
        }
        func width(of text: String, _ font: NSFont) -> CGFloat {
            ceil(NSAttributedString(string: text, attributes: [.font: font]).size().width)
        }

        // 每组宽度取上下两行里更宽的那个，保证块内左对齐、块间不互相挤
        let widths: [CGFloat] = blocks.map { block in
            let topWidth = width(of: block.top, mainFont)
            guard let bottom = block.bottom else { return topWidth }
            return max(topWidth, width(of: bottom, font(for: block)))
        }
        let totalWidth = padX * 2 + widths.reduce(0, +)
            + columnGap * CGFloat(max(0, blocks.count - 1))
        let stackTop = max(0, (thickness - lineH * 2) / 2)

        let image = NSImage(size: NSSize(width: totalWidth, height: thickness), flipped: true) { _ in
            var x = padX
            for (index, block) in blocks.enumerated() {
                let topText = NSAttributedString(string: block.top, attributes: [.font: mainFont])
                if let bottom = block.bottom {
                    let bottomFont = font(for: block)
                    let bottomText = NSAttributedString(string: bottom, attributes: [.font: bottomFont])
                    topText.draw(at: NSPoint(x: x, y: stackTop))
                    // 小字在自己的行框里垂直居中，避免看着往上顶
                    let offset = block.bottomSmall ? (lineH - capH) / 2 : 0
                    bottomText.draw(at: NSPoint(x: x, y: stackTop + lineH + offset))
                } else {
                    // 只有一行时垂直居中，跟原来单行状态栏的观感一致
                    topText.draw(at: NSPoint(x: x, y: max(0, (thickness - lineH) / 2)))
                }
                x += widths[index] + columnGap
            }
            return true
        }
        image.isTemplate = true
        return image
    }

    private func updateTitle() {
        guard let button = statusItem.button else { return }
        var blocks: [StatusBlock] = []
        var tooltip: [String] = []

        // 顺序：内存 · 上传下载 · 余额（今日 token 叠在余额下面）
        if model.cfg.menuMem, let m = model.mem {
            blocks.append(StatusBlock(
                top: String(format: "%.0f%%", m.usedPct),
                bottom: "MEM",
                bottomSmall: true
            ))
            tooltip.append(String(format: "内存已用 %.0f%% · 可用 %.1fG", m.usedPct, m.availableGB))
        }

        if model.cfg.menuSpeed {
            let up = fmtSpeedShort(model.up)
            let down = fmtSpeedShort(model.down)
            blocks.append(StatusBlock(top: "↑" + up, bottom: "↓" + down))
            tooltip.append("↑\(up) ↓\(down)")
        }

        let tokenText: String? = (model.cfg.menuToken ? model.todayTok : nil)
            .map { fmtTokens($0.total) + " tok" }

        if model.cfg.menuBalance {
            let money = model.balance.map { fmtMoney($0.left) } ?? "—"
            blocks.append(StatusBlock(top: money, bottom: tokenText, bottomSmall: true))
            tooltip.append("余额 " + money)
        } else if let tokenText = tokenText {
            // 余额关了但 token 还开着，单独占一组，否则会被一起吞掉
            blocks.append(StatusBlock(top: tokenText, bottom: nil))
        }
        if let tokenText = tokenText {
            tooltip.append("今日 " + tokenText)
        }

        guard !blocks.isEmpty else {
            button.image = nil
            button.imagePosition = .noImage
            button.attributedTitle = NSAttributedString(
                string: APP_NAME,
                attributes: [.font: NSFont.monospacedDigitSystemFont(ofSize: 11, weight: .regular)]
            )
            button.toolTip = APP_NAME
            return
        }

        button.attributedTitle = NSAttributedString(string: "")
        button.image = statusImage(blocks)
        button.imagePosition = .imageOnly
        // 内容全画成图片了，读屏和 hover 提示拿不到，补一份纯文字
        button.toolTip = tooltip.joined(separator: "  ")
        button.setAccessibilityLabel(button.toolTip)
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

        let logItem = NSMenuItem(title: "打开日志", action: #selector(openLog), keyEquivalent: "")
        logItem.target = self
        menu.addItem(logItem)

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

    @objc private func openLog() {
        Log.write("用户打开日志")
        Log.reveal()
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
