import SwiftUI

struct SettingsView: View {
    @ObservedObject var model: Model
    var onClose: () -> Void

    @State private var accounts: [Account]
    @State private var selected: Int
    @State private var quotaText: String
    @State private var message = ""
    @State private var testing = false
    /// 开机自启的状态不在 config.json 里，以系统登录项的实际状态为准
    @State private var launchAtLogin = LoginItem.isEnabled
    @State private var needApproval = LoginItem.needsApproval

    init(model: Model, onClose: @escaping () -> Void) {
        self.model = model
        self.onClose = onClose
        _accounts = State(initialValue: model.cfg.accounts)
        _quotaText = State(initialValue: String(format: "%.0f", model.cfg.weekQuota))
        var start = 0
        if let name = model.cfg.active,
           let idx = model.cfg.accounts.firstIndex(where: { $0.name == name }) {
            start = idx
        }
        _selected = State(initialValue: start)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text("设置").font(.system(size: 15, weight: .semibold))
                Spacer()
                Button("关闭", action: onClose)
            }
            .padding(.bottom, 12)

            Text("账号").font(.system(size: 11)).foregroundColor(Palette.muted)

            ScrollView {
                VStack(spacing: 2) {
                    ForEach(Array(accounts.enumerated()), id: \.offset) { item in
                        row(item.offset, item.element)
                    }
                }
            }
            .frame(height: 118)
            .background(Palette.card)

            HStack(spacing: 8) {
                Button("新增") {
                    accounts.append(Account(name: "新账号 \(accounts.count + 1)"))
                    selected = accounts.count - 1
                }
                Button("删除") {
                    guard accounts.indices.contains(selected) else { return }
                    accounts.remove(at: selected)
                    selected = max(0, min(selected, accounts.count - 1))
                }
                .disabled(accounts.isEmpty)
                Button("读 magpie") { readMagpie() }
                Button("读 CC Switch") { readCCSwitch() }
                Spacer()
            }
            .padding(.top, 8)

            if accounts.indices.contains(selected) {
                VStack(alignment: .leading, spacing: 6) {
                    field("名称", text: $accounts[selected].name)
                    field("地址", text: $accounts[selected].base)
                    field("Key", text: $accounts[selected].key)
                }
                .padding(.top, 10)
            }

            Divider().padding(.vertical, 12)

            HStack(spacing: 12) {
                Text("周额度 $").font(.system(size: 12))
                TextField("100", text: $quotaText)
                    .frame(width: 70)
                    .textFieldStyle(.roundedBorder)
                Text("刷新间隔").font(.system(size: 12))
                IntervalMenu(value: model.cfg.interval, size: 12) { model.setInterval($0) }
            }

            HStack(spacing: 10) {
                Toggle("夜间暂停", isOn: bind(\.nightPause))
                    .toggleStyle(.checkbox)
                Text("\(fmtHM(model.cfg.nightFrom)) – \(fmtHM(model.cfg.nightTo))")
                    .font(.system(size: 12))
                    .foregroundColor(Palette.muted)
                Text("（改 config.json 的 night_from / night_to）")
                    .font(.system(size: 10))
                    .foregroundColor(Palette.muted)
                Spacer()
            }
            .padding(.top, 10)

            Divider().padding(.vertical, 12)

            VStack(alignment: .leading, spacing: 8) {
                Text("菜单栏显示（取消勾选后状态栏不再显示，也不再采集该项数据）")
                    .font(.system(size: 11)).foregroundColor(Palette.muted)
                HStack(spacing: 14) {
                    Toggle("内存", isOn: flag(\.menuMem)).toggleStyle(.checkbox)
                    Toggle("CPU 温度", isOn: flag(\.menuTemp))
                        .toggleStyle(.checkbox)
                        .help("Apple Silicon 走 IOHID 传感器读取，读不到时自动隐藏")
                    Toggle("网速", isOn: flag(\.menuSpeed)).toggleStyle(.checkbox)
                    Toggle("余额", isOn: flag(\.menuBalance))
                        .toggleStyle(.checkbox)
                        .help("取消后不再请求中转接口，面板里的余额 / 本周进度也一起隐藏")
                    Toggle("token", isOn: flag(\.menuToken)).toggleStyle(.checkbox)
                }
                HStack(spacing: 14) {
                    Text("面板").font(.system(size: 11)).foregroundColor(Palette.muted)
                    Toggle("网速", isOn: bind(\.netPanel)).toggleStyle(.checkbox)
                    Toggle("今日 token", isOn: bind(\.panelToken)).toggleStyle(.checkbox)
                }
            }

            // macOS 12 没有 SMAppService，这一行整体不显示
            if LoginItem.isSupported {
                HStack(spacing: 14) {
                    Text("启动").font(.system(size: 11)).foregroundColor(Palette.muted)
                    Toggle("开机自启", isOn: Binding(
                        get: { launchAtLogin },
                        set: { setLaunchAtLogin($0) }))
                        .toggleStyle(.checkbox)
                        .help("登录 macOS 时自动启动，只出现在菜单栏，不占 Dock")
                    if needApproval {
                        Button("去系统设置允许") { LoginItem.openSystemSettings() }
                            .buttonStyle(.link)
                            .font(.system(size: 11))
                    }
                    Spacer()
                }
                .padding(.top, 10)
            }

            Spacer(minLength: 12)

            HStack {
                Text(message).font(.system(size: 11)).foregroundColor(Palette.muted)
                Spacer()
                if testing {
                    ProgressView().scaleEffect(0.6).frame(width: 20)
                }
                Button("测试连接") { test() }
                    .disabled(testing || !accounts.indices.contains(selected))
                Button("保存并使用") { saveAndUse() }
                    .keyboardShortcut(.defaultAction)
            }
        }
        .padding(18)
        .frame(width: 520, height: 592)
        .background(Palette.bg)
        .foregroundColor(Palette.text)
        .onAppear {
            // 用户可能在系统设置里直接关掉登录项，每次打开都以系统状态为准
            launchAtLogin = LoginItem.isEnabled
            needApproval = LoginItem.needsApproval
        }
    }

    // MARK: 行

    private func row(_ idx: Int, _ acc: Account) -> some View {
        HStack(spacing: 8) {
            Circle()
                .fill(idx == selected ? Palette.green : Palette.track)
                .frame(width: 6, height: 6)
            Text(acc.name.isEmpty ? "未命名" : acc.name)
                .font(.system(size: 12))
            Spacer()
            Text(acc.base)
                .font(.system(size: 10))
                .foregroundColor(Palette.muted)
                .lineLimit(1)
            if acc.name == model.cfg.active {
                Text("使用中").font(.system(size: 10)).foregroundColor(Palette.green)
            }
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 5)
        .background(idx == selected ? Palette.line : Color.clear)
        .contentShape(Rectangle())
        .onTapGesture { selected = idx }
    }

    private func field(_ label: String, text: Binding<String>) -> some View {
        HStack(spacing: 8) {
            Text(label).font(.system(size: 11)).foregroundColor(Palette.muted)
                .frame(width: 34, alignment: .leading)
            TextField("", text: text)
                .textFieldStyle(.roundedBorder)
                .font(.system(size: 12))
        }
    }

    private func bind<T>(_ path: WritableKeyPath<Config, T>) -> Binding<T> {
        Binding(
            get: { model.cfg[keyPath: path] },
            set: { model.cfg[keyPath: path] = $0; model.save() }
        )
    }

    /// 菜单栏的勾选项走这里：除了存配置，还要触发对应的副作用
    /// （取消余额时停查并清缓存、重新勾上时立刻查一次）
    private func flag(_ key: WritableKeyPath<Config, Bool>) -> Binding<Bool> {
        Binding(
            get: { model.cfg[keyPath: key] },
            set: { model.setMenuFlag(key, $0) }
        )
    }

    // MARK: 动作

    private func addUnique(name: String, base: String, key: String) {
        let b = normBase(base)
        if let idx = accounts.firstIndex(where: { normBase($0.base) == b }) {
            accounts[idx].key = key
            if accounts[idx].name.isEmpty { accounts[idx].name = name }
            return
        }
        var title = name.isEmpty ? urlHost(b) : name
        if accounts.contains(where: { $0.name == title }) {
            title += " · " + urlHost(b)
        }
        accounts.append(Account(name: title, base: b, key: key))
    }

    private func readMagpie() {
        do {
            let pair = try magpieCreds(providerID: model.cfg.provider)
            addUnique(name: "magpie", base: pair.0, key: pair.1)
            message = "已从 magpie 导入 1 个配置"
        } catch {
            message = error.localizedDescription
        }
    }

    private func readCCSwitch() {
        do {
            let list = try ccProviders()
            guard !list.isEmpty else {
                message = "CC Switch 里没有可用的地址 + Key"
                return
            }
            for item in list {
                addUnique(name: item.name.isEmpty ? item.app : item.name,
                          base: item.base, key: item.key)
            }
            if let cur = list.first(where: { $0.current }),
               let idx = accounts.firstIndex(where: { normBase($0.base) == normBase(cur.base) }) {
                selected = idx
            }
            message = "已导入 \(list.count) 个配置"
        } catch {
            message = error.localizedDescription
        }
    }

    private func test() {
        guard accounts.indices.contains(selected) else { return }
        let acc = accounts[selected]
        testing = true
        message = "测试中…"
        DispatchQueue.global(qos: .userInitiated).async {
            do {
                let data = try queryBalance(base: normBase(acc.base), key: acc.key)
                DispatchQueue.main.async {
                    testing = false
                    message = String(format: "连接正常 · 总额 $%.2f，已用 $%.2f", data.total, data.used)
                }
            } catch {
                let msg = error.localizedDescription
                DispatchQueue.main.async {
                    testing = false
                    message = "失败：" + msg
                }
            }
        }
    }

    private func saveAndUse() {
        model.cfg.accounts = accounts
        if accounts.indices.contains(selected) {
            model.cfg.active = accounts[selected].name
        }
        if let q = Double(quotaText), q > 0 {
            model.cfg.weekQuota = q
        }
        model.save()
        model.refresh()
        // 账户 / 额度改完通常就是「保存完就走」，留一个空窗在这儿反而多一步
        onClose()
        // 设置窗是复用同一个实例（isReleasedWhenClosed = false），
        // 不清掉的话下次打开还挂着上次的提示
        message = ""
    }

    /// 开机自启直接落到系统登录项里，不走 config.json——
    /// 用户可能在系统设置里改过，配置文件和系统状态容易对不上
    private func setLaunchAtLogin(_ on: Bool) {
        message = LoginItem.set(on) ?? ""
        launchAtLogin = LoginItem.isEnabled
        needApproval = LoginItem.needsApproval
        if needApproval {
            message = "已登记，请在系统设置里允许"
            LoginItem.openSystemSettings()
        }
    }
}
