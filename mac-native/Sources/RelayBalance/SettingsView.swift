import SwiftUI

struct SettingsView: View {
    @ObservedObject var model: Model
    var onClose: () -> Void

    @State private var accounts: [Account]
    @State private var selected: Int
    @State private var quotaText: String
    @State private var message = ""
    @State private var testing = false

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
                Picker("", selection: Binding(
                    get: { model.cfg.interval },
                    set: { model.setInterval($0) })) {
                    ForEach(INTERVALS, id: \.0) { item in
                        Text(item.1).tag(item.0)
                    }
                }
                .pickerStyle(.menu)
                .labelsHidden()
                .frame(width: 100)
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
                Text("显示").font(.system(size: 11)).foregroundColor(Palette.muted)
                HStack(spacing: 16) {
                    Toggle("面板：网速", isOn: bind(\.netPanel)).toggleStyle(.checkbox)
                    Toggle("面板：今日 token", isOn: bind(\.panelToken)).toggleStyle(.checkbox)
                    Toggle("显示内存", isOn: bind(\.menuMem)).toggleStyle(.checkbox)
                }
                HStack(spacing: 16) {
                    Toggle("菜单栏：速度", isOn: bind(\.menuSpeed)).toggleStyle(.checkbox)
                    Toggle("菜单栏：余额", isOn: bind(\.menuBalance)).toggleStyle(.checkbox)
                    Toggle("菜单栏：token", isOn: bind(\.menuToken)).toggleStyle(.checkbox)
                }
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
        .frame(width: 520, height: 560)
        .background(Palette.bg)
        .foregroundColor(Palette.text)
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
        message = "已保存"
        model.refresh()
    }
}
