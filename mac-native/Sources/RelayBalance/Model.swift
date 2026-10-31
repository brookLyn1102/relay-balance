import Foundation
import SwiftUI

final class Model: ObservableObject {
    @Published var cfg: Config
    @Published var balance: Balance?
    @Published var week: WeekStat?
    @Published var todayTok: DayAgg?
    @Published var mem: MemInfo?
    @Published var up: Double = 0
    @Published var down: Double = 0
    @Published var busy = false
    @Published var statusText = "正在读取…"
    @Published var statusKind: StatusKind = .muted
    @Published var nightIdle = false
    @Published var update: UpdateInfo?
    @Published var checkingUpdate = false

    enum StatusKind {
        case muted, good, warn, bad
    }

    let net = NetMeter()
    private var timer: Timer?
    private var lastAt: Date?
    private var tickCount = 0
    private var tokMark: Int?
    private var lastErrorAt: Date?

    /// 标题 / 菜单需要跟着刷新时回调
    var onChange: (() -> Void)?

    init() {
        cfg = ConfigStore.load()
    }

    func start() {
        Log.write("启动 中转管家", Bundle.main.infoDictionary?["CFBundleShortVersionString"] ?? "dev")
        net.sample()
        let t = Timer(timeInterval: 1, repeats: true) { [weak self] _ in
            self?.onSecond()
        }
        RunLoop.main.add(t, forMode: .common)
        timer = t
        // 启动先查一次；夜间就不打扰了
        if inNight(cfg) {
            nightIdle = true
            statusText = "夜间暂停 \(fmtHM(cfg.nightFrom))–\(fmtHM(cfg.nightTo))"
            statusKind = .muted
        } else {
            refresh()
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 8) { [weak self] in
            self?.autoCheckUpdate()
        }
    }

    // MARK: 每秒

    private func onSecond() {
        net.sample()
        up = net.up
        down = net.down

        tickCount += 1
        if tickCount % 5 == 1 {
            if cfg.panelToken || cfg.refreshOnToken {
                todayTok = UsageReader.shared.todayLive()
            }
            if cfg.menuMem {
                mem = readMem()
            }
        }

        if inNight(cfg) {
            if !nightIdle {
                nightIdle = true
                statusText = "夜间暂停 \(fmtHM(cfg.nightFrom))–\(fmtHM(cfg.nightTo))"
                statusKind = .muted
            }
            onChange?()
            return
        }
        if nightIdle {
            nightIdle = false
            statusText = "已恢复刷新"
        }

        maybeRefresh()
        onChange?()
    }

    // MARK: 刷新

    func refresh() {
        if busy { return }
        busy = true
        statusText = "刷新中…"
        statusKind = .muted
        let snapshot = cfg

        DispatchQueue.global(qos: .utility).async {
            do {
                let data = try fetchBalance(snapshot)
                DispatchQueue.main.async { self.apply(data) }
            } catch {
                let msg = error.localizedDescription
                DispatchQueue.main.async { self.applyError(msg) }
            }
        }
    }

    private func apply(_ data: Balance) {
        busy = false
        lastErrorAt = nil
        balance = data
        let w = weekSplit(cfg, pool: data.left)
        week = w
        cfg.weekKey = weekKey()
        lastAt = Date()
        tokMark = todayTok?.total ?? UsageReader.shared.todayLive().total
        statusText = clockText(data.at)
        statusKind = w.pct >= 95 ? .bad : (w.pct >= 80 ? .warn : .good)
        onChange?()
    }

    private func applyError(_ message: String) {
        busy = false
        lastAt = Date()
        lastErrorAt = Date()
        statusText = shortError(message)
        statusKind = .bad
        Log.write("[查询失败]", message)
        onChange?()
        // 网络抖一下就等满整个间隔太亏，5 秒后补一次；期间成功了就不再补
        DispatchQueue.main.asyncAfter(deadline: .now() + 5) { [weak self] in
            guard let self = self, !self.busy, self.lastErrorAt != nil else { return }
            Log.write("[重试] 上次查询失败，补一次")
            self.refresh()
        }
    }

    func clockText(_ date: Date) -> String {
        let f = DateFormatter()
        f.dateFormat = "HH:mm:ss"
        return f.string(from: date) + " 刷新"
    }

    func shortError(_ msg: String) -> String {
        let low = msg.lowercased()
        if low.contains("magpie") || low.contains("配置") ||
            low.contains("no such file") || low.contains("没有供应商") {
            return "读不到 magpie / CC Switch 配置"
        }
        if low.contains("429") || low.contains("too many") || low.contains("rate limit") {
            return "请求太频繁，稍后再试"
        }
        if low.contains("401") || low.contains("unauthorized") { return "密钥无效" }
        if low.contains("403") || low.contains("forbidden") { return "密钥无权访问" }
        if low.contains("404") { return "中转没有计费接口" }
        for code in ["500", "502", "503", "504"] where low.contains(code) {
            return "中转服务异常（\(code)）"
        }
        if low.contains("timed out") || low.contains("超时") { return "超时，稍后再试" }
        if low.contains("certificate") || low.contains("ssl") || low.contains("tls") {
            return "证书校验失败，检查系统时间或代理"
        }
        if low.contains("refused") || low.contains("积极拒绝") {
            return "连不上中转，检查网络或代理"
        }
        if low.contains("hostname") || low.contains("dns") || low.contains("resolve") {
            return "域名解析失败，检查 DNS"
        }
        if low.contains("offline") || low.contains("internet") ||
            low.contains("unreachable") || low.contains("network") {
            return "网络不通，连接被中断"
        }
        return String(msg.prefix(28))
    }

    // MARK: 更新

    /// 启动后查一次，之后每 6 小时一次；夜间不查，半小时醒一次等天亮
    private func autoCheckUpdate() {
        let delay: TimeInterval
        if inNight(cfg) {
            delay = 30 * 60
        } else {
            checkUpdate()
            delay = 6 * 3600
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
            self?.autoCheckUpdate()
        }
    }

    func checkUpdate(manual: Bool = false) {
        if checkingUpdate { return }
        checkingUpdate = true
        if manual {
            statusText = "检查更新…"
            statusKind = .muted
        }
        onChange?()

        DispatchQueue.global(qos: .utility).async {
            let result: Result<UpdateInfo, Error>
            do {
                result = .success(try Updater.check())
            } catch {
                result = .failure(error)
            }
            DispatchQueue.main.async { self.applyUpdate(result, manual: manual) }
        }
    }

    private func applyUpdate(_ result: Result<UpdateInfo, Error>, manual: Bool) {
        checkingUpdate = false
        switch result {
        case .success(let info):
            update = info
            if manual {
                if info.behind > 0 {
                    statusText = "可更新 \(info.behind) 个提交 · \(info.remote ?? "")"
                    statusKind = .good
                } else {
                    statusText = "已是最新 v\(Updater.version)"
                    statusKind = .good
                }
            }
        case .failure(let error):
            update = nil
            if manual {
                statusText = shortError(error.localizedDescription)
                statusKind = .bad
            }
        }
        onChange?()
    }

    // MARK: 配置

    func save() {        ConfigStore.save(cfg)
        onChange?()
    }

    func setInterval(_ seconds: Int) {
        cfg.interval = seconds
        save()
        if !nightIdle { refresh() }
    }

    /// 有 token 变动才查余额，间隔仍是 interval；读不到用量日志时退回定时刷新
    func maybeRefresh() {
        if busy || inNight(cfg) { return }
        if let last = lastAt, Date().timeIntervalSince(last) < Double(cfg.interval) { return }
        guard cfg.refreshOnToken, UsageReader.shared.hasSource() else {
            refresh()
            return
        }
        let total = todayTok?.total
        if tokMark == nil || total == nil || total != tokMark {
            refresh()
        }
    }

    func toggleTokenRefresh() {
        cfg.refreshOnToken.toggle()
        save()
        if !cfg.refreshOnToken { refresh() }
    }

    func toggleNight() {
        cfg.nightPause.toggle()
        save()
        if cfg.nightPause {
            nightIdle = false
        } else {
            nightIdle = false
            refresh()
        }
    }

    func sourceName() -> String {
        if let acc = activeAccount(cfg) { return acc.name }
        return cfg.provider.map { "magpie · \($0)" } ?? "magpie"
    }

    /// 面板进度条同一套阈值
    func pctColor(_ pct: Double) -> Color {
        if pct >= 95 { return Color(hex: "#f87171") }
        if pct >= 80 { return Color(hex: "#f5b544") }
        return Color(hex: "#34d399")
    }

    var statusColor: Color {
        switch statusKind {
        case .muted: return Color(hex: "#8b93a1")
        case .good: return Color(hex: "#34d399")
        case .warn: return Color(hex: "#f5b544")
        case .bad: return Color(hex: "#f87171")
        }
    }

    var todayText: String? {
        guard let t = todayTok, t.req > 0 else { return nil }
        return "今日 \(fmtTokens(t.total)) tok · \(t.req) 次"
    }
}

// MARK: - 颜色

extension Color {
    init(hex: String) {
        var s = hex.trimmingCharacters(in: .whitespacesAndNewlines)
        if s.hasPrefix("#") { s.removeFirst() }
        var value: UInt64 = 0
        Scanner(string: s).scanHexInt64(&value)
        let r = Double((value >> 16) & 0xFF) / 255
        let g = Double((value >> 8) & 0xFF) / 255
        let b = Double(value & 0xFF) / 255
        self.init(red: r, green: g, blue: b)
    }
}
